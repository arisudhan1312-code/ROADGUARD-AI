"""Authenticated worker task retrieval and status updates."""

import math
import os
import uuid
from pathlib import Path

import mysql.connector
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
from notifications import add_notification, notify_role


worker_blueprint = Blueprint("worker", __name__, url_prefix="/api/worker")
TASK_TRANSITIONS = {
    "Assigned": {"Accepted", "Declined"},
    "Accepted": {"In Progress", "Declined"},
}
MAX_EVIDENCE_IMAGE_SIZE = 5 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _error(message, status):
    return jsonify({"error": message}), status


def _safe_coordinate(value, minimum, maximum):
    if value is None:
        return None
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(coordinate) or not minimum <= coordinate <= maximum:
        return None
    return coordinate


def _serialize_task(task):
    for field in ("assigned_at", "updated_at"):
        if task[field] is not None:
            task[field] = task[field].isoformat(sep=" ", timespec="seconds")
    if task["completion_datetime"] is not None:
        task["completion_datetime"] = task["completion_datetime"].isoformat(
            sep=" ", timespec="seconds"
        )
    task["latitude"] = _safe_coordinate(task["latitude"], -90, 90)
    task["longitude"] = _safe_coordinate(task["longitude"], -180, 180)
    return task


def _read_evidence_image(field_name):
    image = request.files.get(field_name)
    if image is None or not image.filename:
        return None, None, f"Choose a {field_name.replace('_', ' ')}."
    extension = Path(image.filename).suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png"}:
        return None, None, "Evidence photos must be JPG, JPEG, or PNG."

    content = image.stream.read(MAX_EVIDENCE_IMAGE_SIZE + 1)
    if not content:
        return None, None, "Evidence photos cannot be empty."
    if len(content) > MAX_EVIDENCE_IMAGE_SIZE:
        return None, None, "Each evidence photo must be 5 MB or smaller."

    valid_signature = (
        content.startswith(b"\xff\xd8\xff")
        if extension in {".jpg", ".jpeg"}
        else content.startswith(PNG_SIGNATURE)
    )
    if not valid_signature:
        return None, None, "Evidence photo contents do not match a supported image."

    stored_extension = ".jpg" if extension == ".jpeg" else extension
    return content, f"{uuid.uuid4().hex}{stored_extension}", None


def _save_evidence_image(directory, filename, content):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    try:
        with path.open("xb") as image_file:
            image_file.write(content)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return path


@worker_blueprint.get("/tasks")
@require_role("worker")
def list_worker_tasks():
    """Return tasks for the worker identified by the signed-in session."""
    user_id = session["user_id"]
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT w.worker_id, w.skills, w.availability, w.location_name,
                   u.full_name, u.email
            FROM workers AS w
            INNER JOIN users AS u ON u.id = w.worker_id
            WHERE w.worker_id = %s
              AND u.role = 'worker'
              AND u.status = 'active'
            """,
            (user_id,),
        )
        worker = cursor.fetchone()
        if worker is None:
            return _error(
                "A worker profile is not configured for this account. "
                "Apply the worker task migration and ensure the profile exists.",
                404,
            )

        cursor.execute(
            """
            SELECT t.task_id, t.hazard_id, h.category AS hazard_category,
                   h.location_name, h.latitude, h.longitude, t.priority,
                   t.status, t.assigned_at, t.updated_at,
                   e.evidence_id AS latest_evidence_id,
                   e.completion_datetime, e.review_notes,
                   e.latitude AS completion_latitude,
                   e.longitude AS completion_longitude,
                   e.repair_description
            FROM repair_tasks AS t
            INNER JOIN hazards AS h ON h.hazard_id = t.hazard_id
            LEFT JOIN repair_evidence AS e
              ON e.evidence_id = (
                SELECT MAX(e2.evidence_id)
                FROM repair_evidence AS e2
                WHERE e2.task_id = t.task_id
              )
            WHERE t.worker_id = %s
            ORDER BY t.assigned_at DESC, t.task_id
            """,
            (user_id,),
        )
        tasks = [_serialize_task(task) for task in cursor.fetchall()]
        for task in tasks:
            if task["completion_latitude"] is not None:
                task["completion_latitude"] = _safe_coordinate(
                    task["completion_latitude"], -90, 90
                )
                task["completion_longitude"] = _safe_coordinate(
                    task["completion_longitude"], -180, 180
                )
            if task["latest_evidence_id"] is not None:
                task["before_image_url"] = url_for(
                    "worker.repair_evidence_image",
                    evidence_id=task["latest_evidence_id"],
                    image_kind="before",
                    _external=True,
                )
                task["after_image_url"] = url_for(
                    "worker.repair_evidence_image",
                    evidence_id=task["latest_evidence_id"],
                    image_kind="after",
                    _external=True,
                )
            else:
                task["before_image_url"] = None
                task["after_image_url"] = None
    except mysql.connector.Error:
        current_app.logger.exception("Could not load worker repair tasks.")
        return _error("Worker tasks are unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "worker": {
                "worker_id": int(worker["worker_id"]),
                "worker_code": f"RGW-{worker['worker_id']}",
                "name": worker["full_name"],
                "email": worker["email"],
                "availability": worker["availability"],
                "location_name": worker["location_name"] or "Location not configured",
            },
            "tasks": tasks,
        }
    ), 200


@worker_blueprint.post("/repair-evidence")
@require_role("worker")
def submit_repair_evidence():
    """Save validated before/after photos for the signed-in worker's task."""
    if not request.mimetype or not request.mimetype.startswith("multipart/form-data"):
        return _error("Submit repair evidence as multipart form data.", 400)
    task_id = request.form.get("task_id", "").strip()
    if not task_id or len(task_id) > 19:
        return _error("A valid task_id is required.", 400)
    description = request.form.get("repair_description", "").strip()
    if not 10 <= len(description) <= 2000:
        return _error("Repair description must be between 10 and 2000 characters.", 400)

    try:
        latitude = float(request.form.get("latitude", ""))
        longitude = float(request.form.get("longitude", ""))
    except (TypeError, ValueError):
        return _error("Valid completion latitude and longitude are required.", 400)
    if (
        not math.isfinite(latitude)
        or not -90 <= latitude <= 90
        or not math.isfinite(longitude)
        or not -180 <= longitude <= 180
    ):
        return _error("Completion GPS coordinates are out of range.", 400)

    before_content, before_name, before_error = _read_evidence_image("before_photo")
    if before_error:
        return _error(before_error, 400)
    after_content, after_name, after_error = _read_evidence_image("after_photo")
    if after_error:
        return _error(after_error, 400)

    upload_directory = Path(current_app.config["EVIDENCE_UPLOAD_FOLDER"])
    saved_paths = []
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT task_id, status
            FROM repair_tasks
            WHERE task_id = %s AND worker_id = %s
            FOR UPDATE
            """,
            (task_id, session["user_id"]),
        )
        task = cursor.fetchone()
        if task is None:
            connection.rollback()
            return _error("Task not found for the signed-in worker.", 404)
        if task["status"] != "In Progress":
            connection.rollback()
            return _error("Only an in-progress task can receive repair evidence.", 409)

        before_path = _save_evidence_image(
            upload_directory, before_name, before_content
        )
        saved_paths.append(before_path)
        after_path = _save_evidence_image(upload_directory, after_name, after_content)
        saved_paths.append(after_path)
        cursor.execute(
            """
            INSERT INTO repair_evidence (
                task_id, worker_id, before_image_path, after_image_path,
                latitude, longitude, repair_description
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                task_id,
                session["user_id"],
                f"uploads/repair-evidence/{before_name}",
                f"uploads/repair-evidence/{after_name}",
                latitude,
                longitude,
                description,
            ),
        )
        evidence_id = cursor.lastrowid
        cursor.execute(
            """
            UPDATE repair_tasks
            SET status = 'Awaiting Admin Verification'
            WHERE task_id = %s AND worker_id = %s AND status = 'In Progress'
            """,
            (task_id, session["user_id"]),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            for saved_path in saved_paths:
                saved_path.unlink(missing_ok=True)
            return _error("The task changed before its evidence could be submitted.", 409)
        notify_role(
            cursor,
            "admin",
            "repair_evidence_submitted",
            "Repair evidence needs review",
            f"Task {task_id} has new before-and-after repair evidence.",
            "repair_task",
            task_id,
        )
        cursor.execute(
            """
            SELECT completion_datetime
            FROM repair_evidence
            WHERE evidence_id = %s
            """,
            (evidence_id,),
        )
        completion_datetime = cursor.fetchone()["completion_datetime"]
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        for saved_path in saved_paths:
            try:
                saved_path.unlink(missing_ok=True)
            except OSError:
                current_app.logger.exception("Could not remove an uncommitted evidence photo.")
        current_app.logger.exception("Could not submit worker repair evidence.")
        return _error("Repair evidence could not be saved. Try again later.", 503)
    except OSError:
        for saved_path in saved_paths:
            try:
                saved_path.unlink(missing_ok=True)
            except OSError:
                current_app.logger.exception("Could not remove an incomplete evidence photo.")
        current_app.logger.exception("Could not store worker repair photos.")
        return _error("The evidence photos could not be saved. Try again.", 500)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": "Repair evidence submitted for administrator verification.",
            "evidence": {
                "evidence_id": evidence_id,
                "task_id": task_id,
                "status": "Submitted",
                "completion_datetime": completion_datetime.isoformat(
                    sep=" ", timespec="seconds"
                ),
                "latitude": latitude,
                "longitude": longitude,
            },
            "task": {"task_id": task_id, "status": "Awaiting Admin Verification"},
        }
    ), 201


@worker_blueprint.get("/evidence/<int:evidence_id>/image/<image_kind>")
@require_role("worker", "admin")
def repair_evidence_image(evidence_id, image_kind):
    """Serve evidence photos only to their owner or an authorized administrator."""
    if image_kind not in {"before", "after"}:
        return _error("Choose a before or after evidence image.", 400)
    column = "before_image_path" if image_kind == "before" else "after_image_path"
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT {column} AS image_path, worker_id
            FROM repair_evidence
            WHERE evidence_id = %s
            """,
            (evidence_id,),
        )
        evidence = cursor.fetchone()
    except mysql.connector.Error:
        current_app.logger.exception("Could not locate repair evidence image.")
        return _error("The repair evidence image is unavailable.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    if evidence is None or not evidence["image_path"]:
        return _error("Repair evidence image not found.", 404)
    if session.get("role") != "admin" and evidence["worker_id"] != session["user_id"]:
        return _error("You do not have permission to view this evidence.", 403)
    return send_from_directory(
        current_app.config["EVIDENCE_UPLOAD_FOLDER"],
        os.path.basename(evidence["image_path"]),
        max_age=0,
    )


@worker_blueprint.get("/activities")
@require_role("worker")
def list_worker_activities():
    """List open volunteer activities and this worker's prior participation."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT a.activity_id, a.name, a.description, a.location_name,
                   a.activity_date, a.required_volunteers, a.volunteer_points,
                   a.status, p.participant_id, p.status AS participant_status,
                   p.completion_details, p.review_notes,
                   (
                     SELECT COUNT(*)
                     FROM volunteer_participants AS p2
                     WHERE p2.activity_id = a.activity_id
                       AND p2.status != 'Rejected'
                   ) AS participant_count
            FROM volunteer_activities AS a
            LEFT JOIN volunteer_participants AS p
              ON p.activity_id = a.activity_id
             AND p.worker_id = %s
            WHERE a.status = 'Open' OR p.participant_id IS NOT NULL
            ORDER BY a.activity_date, a.created_at DESC
            """,
            (session["user_id"],),
        )
        activities = cursor.fetchall()
        for activity in activities:
            if activity["activity_date"] is not None:
                activity["activity_date"] = activity["activity_date"].isoformat()
            activity["participant_count"] = int(activity["participant_count"])
            activity["volunteer_points"] = int(activity["volunteer_points"])
    except mysql.connector.Error:
        current_app.logger.exception("Could not load worker volunteer activities.")
        return _error("Volunteer activities are unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"activities": activities}), 200


@worker_blueprint.post("/activities/join")
@require_role("worker")
def join_volunteer_activity():
    """Join an open activity without creating or changing repair tasks."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send activity_id as a JSON object.", 400)
    activity_id = data.get("activity_id")
    if not isinstance(activity_id, str) or not activity_id.strip() or len(activity_id) > 19:
        return _error("A valid activity_id is required.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT activity_id, name, status, required_volunteers
            FROM volunteer_activities
            WHERE activity_id = %s
            FOR UPDATE
            """,
            (activity_id.strip(),),
        )
        activity = cursor.fetchone()
        if activity is None:
            connection.rollback()
            return _error("Volunteer activity not found.", 404)
        if activity["status"] != "Open":
            connection.rollback()
            return _error("This volunteer activity is closed.", 409)
        worker_id = session["user_id"]
        cursor.execute(
            """
            SELECT participant_id, status
            FROM volunteer_participants
            WHERE activity_id = %s AND worker_id = %s
            FOR UPDATE
            """,
            (activity["activity_id"], worker_id),
        )
        participant = cursor.fetchone()
        if participant and participant["status"] != "Rejected":
            connection.rollback()
            return _error("You have already joined this activity.", 409)
        cursor.execute(
            """
            SELECT COUNT(*) AS participant_count
            FROM volunteer_participants
            WHERE activity_id = %s AND status != 'Rejected'
            """,
            (activity["activity_id"],),
        )
        participant_count = int(cursor.fetchone()["participant_count"])
        if participant_count >= int(activity["required_volunteers"]):
            connection.rollback()
            return _error("This activity has reached its volunteer limit.", 409)
        if participant:
            cursor.execute(
                """
                UPDATE volunteer_participants
                SET status = 'Joined', completion_details = NULL,
                    completed_at = NULL, reviewed_at = NULL, reviewed_by = NULL,
                    review_notes = NULL
                WHERE participant_id = %s
                """,
                (participant["participant_id"],),
            )
        else:
            cursor.execute(
                """
                INSERT INTO volunteer_participants (activity_id, worker_id)
                VALUES (%s, %s)
                """,
                (activity["activity_id"], worker_id),
            )
        participant_id = (
            participant["participant_id"] if participant else cursor.lastrowid
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not join volunteer activity.")
        return _error("The activity could not be joined. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": f"You joined {activity['name']}.",
            "participant": {
                "participant_id": participant_id,
                "activity_id": activity["activity_id"],
                "status": "Joined",
            },
        }
    ), 201


@worker_blueprint.post("/activities/complete")
@require_role("worker")
def submit_volunteer_completion():
    """Submit details for an activity joined by the signed-in worker."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send activity_id and completion_details as JSON.", 400)
    activity_id = data.get("activity_id")
    details = data.get("completion_details")
    if not isinstance(activity_id, str) or not activity_id.strip() or len(activity_id) > 19:
        return _error("A valid activity_id is required.", 400)
    if not isinstance(details, str) or not 10 <= len(details.strip()) <= 2000:
        return _error("Completion details must be between 10 and 2000 characters.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT p.participant_id, p.status, a.name
            FROM volunteer_participants AS p
            INNER JOIN volunteer_activities AS a ON a.activity_id = p.activity_id
            WHERE p.activity_id = %s AND p.worker_id = %s
            FOR UPDATE
            """,
            (activity_id.strip(), session["user_id"]),
        )
        participant = cursor.fetchone()
        if participant is None:
            connection.rollback()
            return _error("Join this activity before submitting completion details.", 404)
        if participant["status"] not in {"Joined", "Rejected"}:
            connection.rollback()
            return _error("This activity cannot be submitted from its current status.", 409)
        cursor.execute(
            """
            UPDATE volunteer_participants
            SET status = 'Submitted', completion_details = %s,
                completed_at = CURRENT_TIMESTAMP, reviewed_at = NULL,
                reviewed_by = NULL, review_notes = NULL
            WHERE participant_id = %s
            """,
            (details.strip(), participant["participant_id"]),
        )
        notify_role(
            cursor,
            "admin",
            "volunteer_completion_submitted",
            "Volunteer activity completion needs review",
            f"A worker submitted completion details for {participant['name']}.",
            "volunteer_activity",
            activity_id.strip(),
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not submit volunteer completion.")
        return _error("The volunteer completion could not be submitted. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": "Volunteer completion submitted for administrator review.",
            "participant_id": participant["participant_id"],
            "status": "Submitted",
        }
    ), 200


@worker_blueprint.get("/rewards")
@require_role("worker")
def worker_rewards():
    """Return separate reward totals for the signed-in worker."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN reward_type = 'Repair'
                                THEN points ELSE 0 END), 0) AS repair_points,
              COALESCE(SUM(CASE WHEN reward_type = 'Volunteer'
                                THEN points ELSE 0 END), 0) AS volunteer_points
            FROM rewards
            WHERE worker_id = %s
            """,
            (session["user_id"],),
        )
        totals = cursor.fetchone()
        cursor.execute(
            """
            SELECT
              COUNT(DISTINCT CASE WHEN status IN (
                'Awaiting Admin Verification', 'Verified'
              ) THEN task_id END) AS completed_tasks,
              COUNT(DISTINCT CASE WHEN status = 'Verified'
                                  THEN task_id END) AS verified_repairs
            FROM repair_tasks
            WHERE worker_id = %s
            """,
            (session["user_id"],),
        )
        task_counts = cursor.fetchone()
        cursor.execute(
            """
            SELECT COUNT(*) AS completed_activities
            FROM volunteer_participants
            WHERE worker_id = %s AND status = 'Approved'
            """,
            (session["user_id"],),
        )
        activity_count = cursor.fetchone()["completed_activities"]
    except mysql.connector.Error:
        current_app.logger.exception("Could not load worker reward totals.")
        return _error("Your reward totals are unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    repair_points = int(totals["repair_points"])
    volunteer_points = int(totals["volunteer_points"])
    return jsonify(
        {
            "repair_points": repair_points,
            "volunteer_points": volunteer_points,
            "total_points": repair_points + volunteer_points,
            "completed_tasks": int(task_counts["completed_tasks"]),
            "verified_repairs": int(task_counts["verified_repairs"]),
            "completed_activities": int(activity_count),
        }
    ), 200


@worker_blueprint.put("/tasks/status")
@require_role("worker")
def update_task_status():
    """Apply one allowed status transition to the signed-in worker's own task."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send task_id and status as a JSON object.", 400)

    task_id = data.get("task_id")
    new_status = data.get("status")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 19:
        return _error("A valid task_id is required.", 400)
    if not isinstance(new_status, str):
        return _error("A valid task status is required.", 400)
    new_status = new_status.strip()
    if new_status not in {"Accepted", "Declined", "In Progress"}:
        return _error("Workers cannot set tasks to that status.", 400)
    user_id = session["user_id"]

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT task_id, hazard_id, status
            FROM repair_tasks
            WHERE task_id = %s AND worker_id = %s
            FOR UPDATE
            """,
            (task_id.strip(), user_id),
        )
        task = cursor.fetchone()
        if task is None:
            connection.rollback()
            return _error("Task not found for the signed-in worker.", 404)
        if new_status not in TASK_TRANSITIONS.get(task["status"], set()):
            connection.rollback()
            return _error(
                "That status transition is not allowed. Repairs must be submitted "
                "with evidence before they can await admin verification.",
                409,
            )

        cursor.execute(
            """
            UPDATE repair_tasks
            SET status = %s
            WHERE task_id = %s AND worker_id = %s
            """,
            (new_status, task["task_id"], user_id),
        )
        if new_status == "Declined":
            cursor.execute(
                """
                UPDATE hazards
                SET status = 'Active'
                WHERE hazard_id = %s AND status = 'Assigned'
                """,
                (task["hazard_id"],),
            )
            cursor.execute(
                """
                SELECT COUNT(*) AS open_task_count
                FROM repair_tasks
                WHERE worker_id = %s
                  AND status IN (
                    'Assigned', 'Accepted', 'In Progress',
                    'Awaiting Admin Verification'
                  )
                """,
                (user_id,),
            )
            if int(cursor.fetchone()["open_task_count"]) == 0:
                cursor.execute(
                    """
                    UPDATE workers
                    SET availability = 'Available'
                    WHERE worker_id = %s AND availability = 'On Task'
                    """,
                    (user_id,),
                )
        else:
            cursor.execute(
                """
                UPDATE workers
                SET availability = 'On Task'
                WHERE worker_id = %s AND availability = 'Available'
                """,
                (user_id,),
            )
        notification_title = (
            "Task accepted" if new_status == "Accepted" else
            "Task declined" if new_status == "Declined" else
            "Repair started"
        )
        add_notification(
            cursor,
            user_id,
            f"task_{new_status.lower().replace(' ', '_')}",
            notification_title,
            f"Task {task['task_id']} status is now {new_status}.",
            "repair_task",
            task["task_id"],
        )
        cursor.execute(
            "SELECT availability FROM workers WHERE worker_id = %s",
            (user_id,),
        )
        worker_availability = cursor.fetchone()["availability"]
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not update worker task status.")
        return _error("The task status could not be updated. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": f"Task {task['task_id']} status updated to {new_status}.",
            "task": {
                "task_id": task["task_id"],
                "hazard_id": task["hazard_id"],
                "status": new_status,
            },
            "worker_availability": worker_availability,
        }
    ), 200
