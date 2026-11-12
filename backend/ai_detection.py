"""Authenticated image intake and optional road-damage model inference."""

import json
import os
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

import cv2
import mysql.connector
import numpy as np
from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
    send_from_directory,
    session,
    url_for,
)

from access import require_role
from database import get_db_connection


ai_detection_blueprint = Blueprint("ai_detection", __name__, url_prefix="/api")

MAX_IMAGE_SIZE = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
JPEG_START_OF_FRAME_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}
_MODEL_LOAD_LOCK = threading.Lock()
_MODEL_INFERENCE_LOCK = threading.Lock()


class ModelUnavailableError(RuntimeError):
    """Raised when a configured road-specific model cannot be loaded."""


def _error(message, status):
    return jsonify({"error": message}), status


def _image_dimensions(content, extension):
    """Read raster dimensions from image headers before allocating decode memory."""
    if extension == ".png":
        if len(content) < 24 or content[12:16] != b"IHDR":
            return None
        width = int.from_bytes(content[16:20], "big")
        height = int.from_bytes(content[20:24], "big")
        return width, height

    offset = 2
    while offset + 4 <= len(content):
        if content[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content):
            return None
        marker = content[offset]
        offset += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(content):
            return None
        segment_length = int.from_bytes(content[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(content):
            return None
        if marker in JPEG_START_OF_FRAME_MARKERS:
            if segment_length < 7:
                return None
            height = int.from_bytes(content[offset + 3:offset + 5], "big")
            width = int.from_bytes(content[offset + 5:offset + 7], "big")
            return width, height
        offset += segment_length
    return None


def _read_valid_image():
    """Read an allowed image and confirm OpenCV can decode its contents."""
    image = request.files.get("image")
    if image is None or not image.filename:
        return None, None, None, "Choose a JPG, JPEG, or PNG road image."

    extension = Path(image.filename).suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png"}:
        return None, None, None, "Choose a JPG, JPEG, or PNG road image."

    content = image.stream.read(MAX_IMAGE_SIZE + 1)
    if not content:
        return None, None, None, "The selected image is empty."
    if len(content) > MAX_IMAGE_SIZE:
        return None, None, None, "The image must be 5 MB or smaller."

    if extension in {".jpg", ".jpeg"}:
        if not content.startswith(JPEG_SIGNATURE):
            return None, None, None, "The image contents do not match a JPG file."
        stored_extension = ".jpg"
    else:
        if not content.startswith(PNG_SIGNATURE):
            return None, None, None, "The image contents do not match a PNG file."
        stored_extension = ".png"

    dimensions = _image_dimensions(content, stored_extension)
    if dimensions is None:
        return None, None, None, "The image header is incomplete or invalid."
    width, height = dimensions
    if not width or not height or width * height > MAX_IMAGE_PIXELS:
        return None, None, None, "The image dimensions are invalid or exceed 20 megapixels."

    try:
        decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error:
        return None, None, None, "OpenCV could not decode this image."
    if decoded is None or decoded.size == 0:
        return None, None, None, "OpenCV could not decode this image."
    decoded_height, decoded_width = decoded.shape[:2]
    if decoded_width != width or decoded_height != height:
        return None, None, None, "The image dimensions are invalid or exceed 20 megapixels."

    return content, stored_extension, decoded, None


def _load_road_model():
    configured_path = Path(current_app.config["ROADGUARD_YOLO_MODEL"]).expanduser()
    model_path = configured_path if configured_path.is_absolute() else Path(__file__).parent / configured_path
    if not model_path.is_file():
        raise ModelUnavailableError(
            f"Road-damage model weights were not found at {model_path}. "
            "Train a road-specific detector and set ROADGUARD_YOLO_MODEL."
        )

    try:
        from ultralytics import YOLO
    except ImportError as error:
        raise ModelUnavailableError(
            "Ultralytics is not installed. Install the optional backend/requirements-yolo.txt "
            "dependencies in a compatible Python environment."
        ) from error

    cache_key = (str(model_path.resolve()), model_path.stat().st_mtime_ns, model_path.stat().st_size)
    with _MODEL_LOAD_LOCK:
        model_cache = current_app.extensions.setdefault("roadguard_yolo_models", {})
        model = model_cache.get(cache_key)
        if model is None:
            try:
                model = YOLO(str(model_path))
            except Exception as error:
                raise ModelUnavailableError(
                    f"The configured road-damage checkpoint could not be loaded: {error}"
                ) from error
            model_cache.clear()
            model_cache[cache_key] = model
    return model


def _class_name(names, class_id):
    if isinstance(names, dict):
        return str(names.get(class_id, f"class_{class_id}"))
    if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
        return str(names[class_id])
    return f"class_{class_id}"


def _is_pothole(category):
    normalized = category.strip().casefold().replace("-", "_").replace(" ", "_")
    return "pothole" in normalized or normalized == "d40"


def _run_model_inference(image):
    """Run only the configured road-specific checkpoint and annotate its boxes."""
    model = _load_road_model()
    try:
        confidence_threshold = float(current_app.config.get("AI_DETECTION_CONFIDENCE", 0.10))
        review_threshold = float(current_app.config.get("AI_DETECTION_REVIEW_THRESHOLD", 0.35))
    except (TypeError, ValueError) as error:
        raise RuntimeError("AI confidence settings must be numeric values from 0 to 1.") from error
    if not 0 <= confidence_threshold <= 1 or not 0 <= review_threshold <= 1:
        raise RuntimeError("AI confidence settings must be between 0 and 1.")

    try:
        with _MODEL_INFERENCE_LOCK:
            results = model.predict(image, conf=confidence_threshold, verbose=False)
    except Exception as error:
        raise RuntimeError(f"Road-damage inference failed: {error}") from error
    if not results:
        raise RuntimeError("The road-damage model returned no inference result.")

    result = results[0]
    boxes = []
    annotated_image = image.copy()
    model_names = getattr(result, "names", None) or getattr(model, "names", {})
    image_height, image_width = image.shape[:2]
    model_boxes = getattr(result, "boxes", None)
    if model_boxes is not None:
        coordinates = model_boxes.xyxy.cpu().numpy()
        class_ids = model_boxes.cls.cpu().numpy()
        confidences = model_boxes.conf.cpu().numpy()
        for coordinate, class_value, confidence_value in zip(coordinates, class_ids, confidences):
            class_id = int(class_value)
            category = _class_name(model_names, class_id)
            x1 = max(0, min(image_width - 1, int(round(coordinate[0]))))
            y1 = max(0, min(image_height - 1, int(round(coordinate[1]))))
            x2 = max(0, min(image_width - 1, int(round(coordinate[2]))))
            y2 = max(0, min(image_height - 1, int(round(coordinate[3]))))
            confidence = max(0.0, min(1.0, float(confidence_value)))
            boxes.append(
                {
                    "class_id": class_id,
                    "category": category,
                    "confidence": round(confidence, 5),
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                }
            )
            color = (42, 210, 135)
            cv2.rectangle(annotated_image, (x1, y1), (x2, y2), color, 2)
            label = f"{category} {confidence:.0%}"
            text_y = max(16, y1 - 6)
            cv2.putText(
                annotated_image,
                label,
                (x1, text_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                2,
                cv2.LINE_AA,
            )

    best_box = max(boxes, key=lambda box: box["confidence"], default=None)
    highest_confidence = best_box["confidence"] if best_box else None
    if not boxes:
        status = "No Detections"
    elif highest_confidence < review_threshold:
        status = "Low Confidence"
    else:
        status = "Pending Admin Review"

    return {
        "boxes": boxes,
        "annotated_image": annotated_image,
        "status": status,
        "highest_confidence": highest_confidence,
        "hazard_category": best_box["category"] if best_box else None,
        "pothole_count": sum(1 for box in boxes if _is_pothole(box["category"])),
    }


def _save_annotated_image(image, extension, upload_directory):
    success, encoded = cv2.imencode(extension, image)
    if not success:
        raise OSError("OpenCV could not encode the annotated image.")
    filename = f"{uuid.uuid4().hex}{extension}"
    path = upload_directory / filename
    with path.open("xb") as image_file:
        image_file.write(encoded.tobytes())
    return filename


def _update_inference_record(detection_id, inference, annotated_name):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE ai_detections
            SET hazard_category = %s,
                detection_count = %s,
                pothole_count = %s,
                confidence_score = %s,
                bounding_boxes = %s,
                annotated_image_path = %s,
                detection_status = %s,
                submitted_for_review_at = CASE
                    WHEN %s IN ('Pending Admin Review', 'Low Confidence')
                    THEN CURRENT_TIMESTAMP ELSE NULL
                END
            WHERE detection_id = %s
            """,
            (
                inference["hazard_category"],
                (
                    len(inference["boxes"])
                    if inference["status"] != "Model Unavailable"
                    else None
                ),
                (
                    inference["pothole_count"]
                    if inference["status"] != "Model Unavailable"
                    else None
                ),
                inference["highest_confidence"],
                (
                    json.dumps(inference["boxes"])
                    if inference["status"] != "Model Unavailable"
                    else None
                ),
                annotated_name,
                inference["status"],
                inference["status"],
                detection_id,
            ),
        )
        if cursor.rowcount != 1:
            raise mysql.connector.Error("The uploaded detection record could not be updated.")
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def _mark_inference_failed(detection_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE ai_detections
            SET detection_status = 'Failed'
            WHERE detection_id = %s
            """,
            (detection_id,),
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not mark the AI detection as failed.")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def _serialize_detection(row):
    detection = dict(row)
    if isinstance(detection.get("created_at"), (datetime, date)):
        detection["created_at"] = detection["created_at"].isoformat(sep=" ", timespec="seconds") \
            if isinstance(detection["created_at"], datetime) else detection["created_at"].isoformat()
    if detection.get("confidence_score") is not None:
        detection["confidence_score"] = float(detection["confidence_score"])
    bounding_boxes = detection.get("bounding_boxes")
    if isinstance(bounding_boxes, bytes):
        detection["bounding_boxes"] = json.loads(bounding_boxes.decode("utf-8"))
    elif isinstance(bounding_boxes, str):
        detection["bounding_boxes"] = json.loads(bounding_boxes)
    if isinstance(detection.get("bounding_boxes"), list):
        category_counts = {}
        for box in detection["bounding_boxes"]:
            category = box.get("category") if isinstance(box, dict) else None
            if category:
                category_counts[category] = category_counts.get(category, 0) + 1
        detection["category_counts"] = category_counts

    detection_id = detection["detection_id"]
    detection["original_image_url"] = url_for(
        "ai_detection.detection_image",
        detection_id=detection_id,
        image_kind="original",
    )
    annotated_image_url = (
        url_for(
            "ai_detection.detection_image",
            detection_id=detection_id,
            image_kind="annotated",
        )
        if detection.get("annotated_image_path")
        else None
    )
    detection.pop("original_image_path", None)
    detection.pop("annotated_image_path", None)
    detection["annotated_image_path"] = annotated_image_url
    detection["annotated_image_url"] = annotated_image_url
    return detection


@ai_detection_blueprint.post("/ai/detect")
@require_role("citizen", "admin")
def upload_road_image():
    """Validate an image, run an optional custom model, and store review results."""
    if not request.mimetype or not request.mimetype.startswith("multipart/form-data"):
        return _error("Submit the image as multipart form data.", 400)

    content, extension, decoded_image, image_error = _read_valid_image()
    if image_error:
        return _error(image_error, 400)

    report_id = request.form.get("report_id", "").strip() or None
    if report_id is not None and len(report_id) > 19:
        return _error("The linked report ID is invalid.", 400)

    detection_id = str(uuid.uuid4())
    image_name = f"{uuid.uuid4().hex}{extension}"
    upload_directory = Path(current_app.config["AI_DETECTION_UPLOAD_FOLDER"])
    saved_image_path = upload_directory / image_name
    connection = None
    cursor = None

    try:
        upload_directory.mkdir(parents=True, exist_ok=True)
        with saved_image_path.open("xb") as image_file:
            image_file.write(content)

        connection = get_db_connection()
        cursor = connection.cursor()
        if report_id:
            if session.get("role") == "admin":
                cursor.execute(
                    "SELECT report_id FROM road_reports WHERE report_id = %s LIMIT 1",
                    (report_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT report_id
                    FROM road_reports
                    WHERE report_id = %s AND user_id = %s
                    LIMIT 1
                    """,
                    (report_id, session["user_id"]),
                )
            if cursor.fetchone() is None:
                saved_image_path.unlink(missing_ok=True)
                return _error("The linked report was not found or does not belong to you.", 404)

        cursor.execute(
            """
            INSERT INTO ai_detections (
                detection_id, user_id, report_id, original_image_path, detection_status
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                detection_id,
                session["user_id"],
                report_id,
                image_name,
                "Pending Inference",
            ),
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        saved_image_path.unlink(missing_ok=True)
        current_app.logger.exception("Database error while saving an AI detection image.")
        return _error("The image could not be recorded. Check the database and try again.", 503)
    except OSError:
        saved_image_path.unlink(missing_ok=True)
        current_app.logger.exception("Could not save an AI detection image.")
        return _error("The image could not be stored. Check the upload folder and try again.", 500)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    try:
        inference = _run_model_inference(decoded_image)
    except ModelUnavailableError as error:
        current_app.logger.warning("Road-damage inference is unavailable: %s", error)
        try:
            _update_inference_record(
                detection_id,
                {
                    "hazard_category": None,
                    "boxes": [],
                    "pothole_count": 0,
                    "highest_confidence": None,
                    "status": "Model Unavailable",
                },
                None,
            )
        except mysql.connector.Error:
            current_app.logger.exception("Could not save unavailable-model status.")
            return _error("The image was saved, but its analysis status could not be recorded.", 503)
        return _detection_response(
            detection_id,
            session["user_id"],
            report_id,
            image_name,
            None,
            "Model Unavailable",
            (
                "The image was uploaded and decoded, but no detections were produced. "
                f"{error}"
            ),
        ), 201
    except Exception as error:
        current_app.logger.exception("Road-damage inference failed.")
        _mark_inference_failed(detection_id)
        return jsonify(
            {
                "error": "The image was stored, but road-damage inference failed. Check the backend log.",
                "detection_id": detection_id,
                "detection_status": "Failed",
            }
        ), 503

    try:
        annotated_name = _save_annotated_image(
            inference["annotated_image"],
            extension,
            upload_directory,
        )
        _update_inference_record(detection_id, inference, annotated_name)
    except (OSError, mysql.connector.Error):
        if "annotated_name" in locals():
            (upload_directory / annotated_name).unlink(missing_ok=True)
        current_app.logger.exception("Could not save road-damage inference results.")
        _mark_inference_failed(detection_id)
        return jsonify(
            {
                "error": "The image was stored, but inference results could not be saved.",
                "detection_id": detection_id,
                "detection_status": "Failed",
            }
        ), 503

    message_by_status = {
        "Pending Admin Review": (
            "Road-damage detections were saved and queued for administrator review. "
            "They are not confirmed hazards."
        ),
        "Low Confidence": (
            "Low-confidence detections were saved for administrator review and are not confirmed."
        ),
        "No Detections": (
            "The configured road-damage model did not detect any supported hazards in this image."
        ),
    }
    return _detection_response(
        detection_id,
        session["user_id"],
        report_id,
        image_name,
        inference,
        inference["status"],
        message_by_status[inference["status"]],
        annotated_name,
    ), 201


def _detection_response(
    detection_id,
    user_id,
    report_id,
    image_name,
    inference,
    status,
    message,
    annotated_name=None,
):
    inference = inference or {}
    has_inference = status not in {"Model Unavailable", "Pending Inference", "Failed"}
    detection = _serialize_detection(
        {
            "detection_id": detection_id,
            "report_id": report_id,
            "user_id": user_id,
            "hazard_category": inference.get("hazard_category"),
            "detection_count": len(inference.get("boxes", [])) if has_inference else None,
            "pothole_count": inference.get("pothole_count") if has_inference else None,
            "confidence_score": inference.get("highest_confidence"),
            "bounding_boxes": inference.get("boxes") if has_inference else None,
            "original_image_path": image_name,
            "annotated_image_path": annotated_name,
            "detection_status": status,
            "created_at": None,
        }
    )
    return jsonify({"message": message, "detection": detection})


@ai_detection_blueprint.get("/ai/detections/mine")
@require_role("citizen", "admin")
def my_detections():
    """Return only the signed-in user's uploaded road images."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT detection_id, report_id, hazard_category, detection_count, pothole_count,
                   confidence_score, bounding_boxes, original_image_path,
                   annotated_image_path, detection_status, created_at
            FROM ai_detections
            WHERE user_id = %s
            ORDER BY created_at DESC, detection_id DESC
            """,
            (session["user_id"],),
        )
        detections = [_serialize_detection(row) for row in cursor.fetchall()]
    except mysql.connector.Error:
        current_app.logger.exception("Could not load the user's AI detection uploads.")
        return _error("Image analysis records could not be loaded. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"detections": detections}), 200


@ai_detection_blueprint.post("/ai/detections/<detection_id>/submit")
@require_role("citizen")
def submit_detection_for_review(detection_id):
    """Place an owned upload in the administrator's manual review queue."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE ai_detections
            SET detection_status = 'Pending Admin Review',
                submitted_for_review_at = CURRENT_TIMESTAMP
            WHERE detection_id = %s
              AND user_id = %s
              AND detection_status = 'Model Unavailable'
            """,
            (detection_id, session["user_id"]),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return _error(
                "This image was not found, is not yours, or has already been submitted.",
                404,
            )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not submit an AI image for admin review.")
        return _error("The image could not be submitted for review. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": "The uploaded image was submitted for administrator review.",
            "detection_id": detection_id,
            "detection_status": "Pending Admin Review",
        }
    ), 200


@ai_detection_blueprint.get("/admin/ai/detections")
@require_role("admin")
def admin_detection_queue():
    """List citizen images explicitly submitted for human review."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT d.detection_id, d.user_id, d.report_id, u.full_name AS submitter_name,
                   d.hazard_category, d.detection_count, d.pothole_count,
                   d.confidence_score, d.bounding_boxes, d.original_image_path,
                   d.annotated_image_path, d.detection_status, d.created_at,
                   d.submitted_for_review_at
            FROM ai_detections AS d
            JOIN users AS u ON u.id = d.user_id
            WHERE d.detection_status IN ('Pending Admin Review', 'Low Confidence')
            ORDER BY d.submitted_for_review_at ASC, d.detection_id ASC
            LIMIT 200
            """
        )
        detections = [_serialize_detection(row) for row in cursor.fetchall()]
    except mysql.connector.Error:
        current_app.logger.exception("Could not load the admin AI image review queue.")
        return _error("AI image reviews could not be loaded. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"detections": detections}), 200


@ai_detection_blueprint.put("/admin/ai/detections/<detection_id>/review")
@require_role("admin")
def review_ai_detection(detection_id):
    """Record a human review without changing the related road report or hazard."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send a decision and optional notes as a JSON object.", 400)

    decision = data.get("decision")
    if not isinstance(decision, str) or decision not in {"confirm", "reject"}:
        return _error("Choose either confirm or reject.", 400)
    notes = data.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 1000:
        return _error("Review notes must be 1000 characters or fewer.", 400)

    status = "Confirmed" if decision == "confirm" else "Rejected"
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE ai_detections
            SET detection_status = %s,
                reviewed_by = %s,
                reviewed_at = CURRENT_TIMESTAMP,
                review_notes = %s
            WHERE detection_id = %s
              AND detection_status IN ('Pending Admin Review', 'Low Confidence')
            """,
            (status, session["user_id"], notes.strip() or None, detection_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return _error("This detection is not available for review.", 404)
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not save an administrator AI detection review.")
        return _error("The AI review could not be saved. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": (
                f"The AI result was marked {status}. This decision does not approve the "
                "citizen report or create a driver-visible hazard."
            ),
            "detection_id": detection_id,
            "detection_status": status,
        }
    ), 200


@ai_detection_blueprint.get("/ai/detections/<detection_id>/image/<image_kind>")
@require_role("citizen", "admin")
def detection_image(detection_id, image_kind):
    """Serve the original or annotated image only to its owner or an admin."""
    if image_kind not in {"original", "annotated"}:
        return _error("Choose either the original or annotated image.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT user_id, original_image_path, annotated_image_path
            FROM ai_detections
            WHERE detection_id = %s
            LIMIT 1
            """,
            (detection_id,),
        )
        row = cursor.fetchone()
    except mysql.connector.Error:
        current_app.logger.exception("Could not locate an AI detection image.")
        return _error("The image is unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    if row is None:
        return _error("Image record not found.", 404)
    if session.get("role") != "admin" and row[0] != session["user_id"]:
        return _error("You do not have permission to view this image.", 403)

    filename = row[1] if image_kind == "original" else row[2]
    if not filename:
        return _error("No annotated image is available because inference has not run.", 404)

    return send_from_directory(
        current_app.config["AI_DETECTION_UPLOAD_FOLDER"],
        os.path.basename(filename),
        conditional=True,
        max_age=0,
    )
