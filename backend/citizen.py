"""Authenticated citizen road-report submission and tracking endpoints."""

import math
import os
import uuid
from pathlib import Path

import mysql.connector
from flask import Blueprint, current_app, jsonify, request, send_from_directory, session, url_for

from access import require_role
from database import get_db_connection
from hazard_priority_service import recalculate_location_priorities
from notifications import add_notification, notify_role


citizen_blueprint = Blueprint("citizen", __name__, url_prefix="/api")

ALLOWED_CATEGORIES = {
    "Pothole",
    "Damaged Road",
    "Broken Streetlight",
    "Waterlogging",
    "Road Obstruction",
    "Other",
}
MAX_IMAGE_SIZE = 5 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _report_dict(row):
    """Convert a database row into the public report response shape."""
    report = dict(row)
    if report.get("report_datetime") is not None:
        report["report_datetime"] = report["report_datetime"].isoformat(sep=" ", timespec="seconds")
    report["image_url"] = (
        url_for("citizen.report_image", report_id=report["report_id"])
        if report.get("image_path")
        else None
    )
    return report


def _error(message, status):
    return jsonify({"error": message}), status


def _parse_coordinates():
    latitude_value = request.form.get("latitude", "").strip()
    longitude_value = request.form.get("longitude", "").strip()

    if not latitude_value and not longitude_value:
        return None, None, None
    if not latitude_value or not longitude_value:
        return None, None, "Latitude and longitude must be provided together."

    try:
        latitude = float(latitude_value)
        longitude = float(longitude_value)
    except (TypeError, ValueError):
        return None, None, "GPS coordinates must be valid numbers."

    if not math.isfinite(latitude) or not -90 <= latitude <= 90:
        return None, None, "Latitude must be between -90 and 90."
    if not math.isfinite(longitude) or not -180 <= longitude <= 180:
        return None, None, "Longitude must be between -180 and 180."
    return latitude, longitude, None


def _read_image():
    """Validate an optional image using its extension and file signature."""
    image = request.files.get("image")
    if image is None or not image.filename:
        return None, None, None

    extension = Path(image.filename).suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png"}:
        return None, None, "Choose a JPG, JPEG, or PNG image."

    content = image.stream.read(MAX_IMAGE_SIZE + 1)
    if not content:
        return None, None, "The selected image is empty."
    if len(content) > MAX_IMAGE_SIZE:
        return None, None, "The image must be 5 MB or smaller."

    if extension in {".jpg", ".jpeg"}:
        valid_signature = content.startswith(b"\xff\xd8\xff")
    else:
        valid_signature = content.startswith(PNG_SIGNATURE)
    if not valid_signature:
        return None, None, "The image contents do not match a supported JPG or PNG file."

    stored_extension = ".jpg" if extension == ".jpeg" else extension
    stored_name = f"{uuid.uuid4().hex}{stored_extension}"
    return content, stored_name, None


@citizen_blueprint.post("/reports")
@require_role("citizen")
def submit_report():
    """Validate and save a citizen report, using the user ID from the session."""
    if not request.mimetype or not request.mimetype.startswith("multipart/form-data"):
        return _error("Submit the report as multipart form data.", 400)

    category = request.form.get("category", "").strip()
    if category not in ALLOWED_CATEGORIES:
        return _error("Choose a valid road problem category.", 400)

    description = request.form.get("description", "").strip()
    if not 10 <= len(description) <= 2000:
        return _error("Description must be between 10 and 2000 characters.", 400)

    location = request.form.get("location", "").strip()
    if not location or len(location) > 255:
        return _error("Location is required and must be 255 characters or fewer.", 400)

    latitude, longitude, coordinate_error = _parse_coordinates()
    if coordinate_error:
        return _error(coordinate_error, 400)

    image_content, image_name, image_error = _read_image()
    if image_error:
        return _error(image_error, 400)

    report_id = f"RG-{uuid.uuid4().hex[:16].upper()}"
    upload_directory = Path(current_app.config["REPORT_UPLOAD_FOLDER"])
    image_path = None
    saved_image_path = None

    try:
        if image_content is not None:
            upload_directory.mkdir(parents=True, exist_ok=True)
            saved_image_path = upload_directory / image_name
            with saved_image_path.open("xb") as image_file:
                image_file.write(image_content)
            image_path = f"uploads/reports/{image_name}"

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                INSERT INTO road_reports (
                    report_id, user_id, hazard_category, problem_description,
                    location_name, latitude, longitude, image_path
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    report_id,
                    session["user_id"],
                    category,
                    description,
                    location,
                    latitude,
                    longitude,
                    image_path,
                ),
            )
            priority_results = recalculate_location_priorities(cursor, location)
            cursor.execute(
                "SELECT report_datetime FROM road_reports WHERE report_id = %s",
                (report_id,),
            )
            report_datetime = cursor.fetchone()["report_datetime"]
            report_priority = priority_results[report_id]
            add_notification(
                cursor,
                session["user_id"],
                "report_submitted",
                "Road report submitted",
                f"Report {report_id} is pending administrator review.",
                "report",
                report_id,
            )
            notify_role(
                cursor,
                "admin",
                "new_report",
                "New citizen road report",
                f"Report {report_id} is ready for review.",
                "report",
                report_id,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
    except mysql.connector.Error:
        if saved_image_path is not None:
            saved_image_path.unlink(missing_ok=True)
        current_app.logger.exception("Database error while saving citizen report.")
        return _error("The report could not be saved. Check the database and try again.", 503)
    except OSError:
        if saved_image_path is not None:
            saved_image_path.unlink(missing_ok=True)
        current_app.logger.exception("Could not save an uploaded citizen report image.")
        return _error("The image could not be saved. Try again.", 500)

    return jsonify(
        {
            "message": "Your report was submitted for review.",
            "report": {
                "report_id": report_id,
                "hazard_category": category,
                "location_name": location,
                "latitude": latitude,
                "longitude": longitude,
                "image_path": image_path,
                "report_datetime": report_datetime.isoformat(sep=" ", timespec="seconds"),
                "priority": "Normal",
                "hazard_priority": report_priority["hazard_priority"],
                "priority_score": report_priority["priority_score"],
                "priority_explanation": report_priority["priority_explanation"],
                "status": "Pending Review",
                "image_url": (
                    url_for("citizen.report_image", report_id=report_id)
                    if image_path
                    else None
                ),
            },
        }
    ), 201


@citizen_blueprint.get("/reports/my")
@require_role("citizen")
def my_reports():
    """List only reports owned by the authenticated citizen."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT report_id, hazard_category, problem_description, location_name,
                   latitude, longitude, image_path, report_datetime, priority, status
            FROM road_reports
            WHERE user_id = %s
            ORDER BY report_datetime DESC, report_id DESC
            """,
            (session["user_id"],),
        )
        reports = [_report_dict(row) for row in cursor.fetchall()]
    except mysql.connector.Error:
        current_app.logger.exception("Could not load citizen reports.")
        return _error("Reports could not be loaded. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"reports": reports}), 200


@citizen_blueprint.get("/reports/status")
@require_role("citizen")
def report_statuses():
    """Return status and priority for the signed-in citizen's reports."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT report_id, hazard_category, location_name, report_datetime, priority, status
            FROM road_reports
            WHERE user_id = %s
            ORDER BY report_datetime DESC, report_id DESC
            """,
            (session["user_id"],),
        )
        rows = cursor.fetchall()
        for row in rows:
            if row["report_datetime"] is not None:
                row["report_datetime"] = row["report_datetime"].isoformat(sep=" ", timespec="seconds")
    except mysql.connector.Error:
        current_app.logger.exception("Could not load citizen report statuses.")
        return _error("Report statuses could not be loaded. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"reports": rows}), 200


@citizen_blueprint.get("/reports/<report_id>")
@require_role("citizen")
def report_details(report_id):
    """Return a single report only when it belongs to the logged-in citizen."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT report_id, hazard_category, problem_description, location_name,
                   latitude, longitude, image_path, report_datetime, priority, status
            FROM road_reports
            WHERE report_id = %s AND user_id = %s
            LIMIT 1
            """,
            (report_id, session["user_id"]),
        )
        row = cursor.fetchone()
    except mysql.connector.Error:
        current_app.logger.exception("Could not load citizen report details.")
        return _error("Report details could not be loaded. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    if row is None:
        return _error("Report not found.", 404)
    return jsonify({"report": _report_dict(row)}), 200


@citizen_blueprint.get("/reports/<report_id>/image")
@require_role("citizen", "admin")
def report_image(report_id):
    """Serve an uploaded image only to its owner or an authenticated admin."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT image_path, user_id
            FROM road_reports
            WHERE report_id = %s
            LIMIT 1
            """,
            (report_id,),
        )
        row = cursor.fetchone()
    except mysql.connector.Error:
        current_app.logger.exception("Could not locate citizen report image.")
        return _error("The report image is unavailable.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    if row is None or not row[0]:
        return _error("Report image not found.", 404)
    if session.get("role") != "admin" and row[1] != session["user_id"]:
        return _error("You do not have permission to view this image.", 403)

    filename = os.path.basename(row[0])
    return send_from_directory(
        current_app.config["REPORT_UPLOAD_FOLDER"],
        filename,
        conditional=True,
        max_age=0,
    )
