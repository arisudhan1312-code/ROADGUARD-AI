"""Admin report review and hazard-approval endpoints."""

import json
import math
import uuid
from datetime import datetime

import mysql.connector
from flask import Blueprint, current_app, jsonify, request, session, url_for

from access import require_role
from database import get_db_connection
from hazard_priority import (
    PRIORITY_ORDER,
    VALID_PRIORITIES,
    VALID_TRAFFIC_IMPORTANCE,
)
from hazard_priority_service import present_priority, recalculate_location_priorities
from notifications import add_notification, notify_role


admin_blueprint = Blueprint("admin", __name__, url_prefix="/api/admin")
VALID_SEVERITIES = {"High", "Medium", "Low"}
ALLOWED_HAZARD_CATEGORIES = {
    "Pothole",
    "Damaged Road",
    "Broken Streetlight",
    "Waterlogging",
    "Road Obstruction",
    "Other",
}
EARTH_RADIUS_KM = 6371.0


def _error(message, status):
    return jsonify({"error": message}), status


def _read_skills(value):
    """Return stored JSON skills as a list of non-empty strings."""
    try:
        skills = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
    except (TypeError, ValueError):
        return []
    if not isinstance(skills, list):
        return []
    return [skill.strip() for skill in skills if isinstance(skill, str) and skill.strip()]


def _safe_coordinate(value, minimum, maximum):
    if value is None or isinstance(value, bool):
        return None
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(coordinate) or not minimum <= coordinate <= maximum:
        return None
    return coordinate


def _distance_km(worker, hazard):
    worker_latitude = _safe_coordinate(worker["latitude"], -90, 90)
    worker_longitude = _safe_coordinate(worker["longitude"], -180, 180)
    hazard_latitude = _safe_coordinate(hazard["latitude"], -90, 90)
    hazard_longitude = _safe_coordinate(hazard["longitude"], -180, 180)
    if (
        worker_latitude is None
        or worker_longitude is None
        or hazard_latitude is None
        or hazard_longitude is None
    ):
        return None

    radians = math.radians
    latitude_delta = radians(hazard_latitude - worker_latitude)
    longitude_delta = radians(hazard_longitude - worker_longitude)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(radians(worker_latitude))
        * math.cos(radians(hazard_latitude))
        * math.sin(longitude_delta / 2) ** 2
    )
    value = min(1.0, max(0.0, value))
    return 2 * EARTH_RADIUS_KM * math.atan2(
        math.sqrt(value), math.sqrt(1 - value)
    )


def _score_worker(worker, hazard, workload):
    skills = _read_skills(worker["skills"])
    skill_match = any(
        skill.casefold() == hazard["category"].casefold() for skill in skills
    )
    available = worker["availability"] == "Available"
    distance_km = _distance_km(worker, hazard)

    skill_points = 40 if skill_match else 0
    availability_points = 25 if available else 0
    distance_points = (
        max(0, round(20 - distance_km * 2)) if distance_km is not None else 0
    )
    workload_penalty_by_priority = {"High": 5, "Medium": 3, "Low": 2.5}
    workload_penalty = workload_penalty_by_priority[hazard["severity"]]
    workload_points = max(0, round(15 - workload * workload_penalty))
    score = skill_points + availability_points + distance_points + workload_points

    reasons = [
        f"{'Skill matches' if skill_match else 'No direct skill match'} "
        f"({skill_points}/40)",
        f"{worker['availability']} ({availability_points}/25)",
        (
            f"{distance_km:.1f} km ({distance_points}/20)"
            if distance_km is not None
            else "Worker location not configured (0/20 distance)"
        ),
        f"{workload} open task(s) ({workload_points}/15 workload)",
        f"{hazard['severity']} priority workload penalty",
    ]

    return {
        "worker_id": int(worker["worker_id"]),
        "worker_code": f"RGW-{worker['worker_id']}",
        "name": worker["full_name"],
        "skills": skills,
        "availability": worker["availability"],
        "available": available,
        "current_workload": workload,
        "location_name": worker["location_name"] or "Location not configured",
        "latitude": _safe_coordinate(worker["latitude"], -90, 90),
        "longitude": _safe_coordinate(worker["longitude"], -180, 180),
        "distance_km": round(distance_km, 2) if distance_km is not None else None,
        "score": score,
        "score_breakdown": {
            "skill": skill_points,
            "availability": availability_points,
            "distance": distance_points,
            "workload": workload_points,
        },
        "recommendation_reason": " · ".join(reasons),
    }


def _load_worker_candidates(cursor, hazard):
    cursor.execute(
        """
        SELECT w.worker_id, w.skills, w.availability, w.location_name,
               w.latitude, w.longitude, u.full_name,
               (
                 SELECT COUNT(*)
                 FROM repair_tasks AS t
                 WHERE t.worker_id = w.worker_id
                   AND t.status IN (
                     'Assigned', 'Accepted', 'In Progress',
                     'Awaiting Admin Verification'
                   )
               ) AS current_workload
        FROM workers AS w
        INNER JOIN users AS u ON u.id = w.worker_id
        WHERE u.role = 'worker' AND u.status = 'active'
        """
    )
    candidates = [
        _score_worker(worker, hazard, int(worker["current_workload"]))
        for worker in cursor.fetchall()
    ]
    candidates.sort(
        key=lambda worker: (
            not worker["available"],
            -worker["score"],
            worker["current_workload"],
            worker["distance_km"] if worker["distance_km"] is not None else math.inf,
            worker["worker_id"],
        )
    )
    recommended = next(
        (worker["worker_id"] for worker in candidates if worker["available"]),
        None,
    )
    return candidates, recommended


@admin_blueprint.get("/reports")
@require_role("admin")
def list_reports():
    """List database reports for authenticated admin review."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT r.report_id, r.user_id, u.full_name AS citizen_name,
                   r.hazard_category, r.problem_description, r.location_name,
                   r.latitude, r.longitude, r.image_path, r.report_datetime,
                   r.priority, r.status, r.traffic_importance,
                   r.calculated_priority, r.hazard_priority, r.priority_score,
                   r.priority_report_count, r.priority_source,
                   h.severity,
                   (SELECT COUNT(*)
                    FROM road_reports AS same_location
                    WHERE LOWER(TRIM(same_location.location_name)) =
                          LOWER(TRIM(r.location_name))
                      AND same_location.status <> 'Rejected'
                   ) AS current_location_report_count,
                   d.detection_id AS ai_detection_id,
                   d.hazard_category AS ai_hazard_category,
                   d.detection_count AS ai_detection_count,
                   d.pothole_count AS ai_pothole_count,
                   d.confidence_score AS ai_confidence_score,
                   d.bounding_boxes AS ai_bounding_boxes,
                   d.detection_status AS ai_detection_status,
                   d.original_image_path AS ai_original_image_path,
                   d.annotated_image_path AS ai_annotated_image_path
            FROM road_reports AS r
            INNER JOIN users AS u ON u.id = r.user_id
            LEFT JOIN hazards AS h ON h.report_id = r.report_id
            LEFT JOIN ai_detections AS d
              ON d.detection_id = (
                  SELECT latest.detection_id
                  FROM ai_detections AS latest
                  WHERE latest.report_id = r.report_id
                  ORDER BY latest.created_at DESC, latest.detection_id DESC
                  LIMIT 1
              )
            ORDER BY r.report_datetime DESC, r.report_id DESC
            """
        )
        reports = cursor.fetchall()
        for report in reports:
            report.update(
                present_priority(
                    report,
                    int(
                        report.get("current_location_report_count")
                        or report.get("priority_report_count")
                        or 1
                    ),
                )
            )
            report.pop("current_location_report_count", None)
            if report["report_datetime"] is not None:
                report["report_datetime"] = report["report_datetime"].isoformat(
                    sep=" ", timespec="seconds"
                )
            if report["image_path"]:
                report["image_url"] = url_for(
                    "citizen.report_image",
                    report_id=report["report_id"],
                    _external=True,
                )
            else:
                report["image_url"] = None
            for image_kind, column in (
                ("original", "ai_original_image_path"),
                ("annotated", "ai_annotated_image_path"),
            ):
                report[f"ai_{image_kind}_image_url"] = (
                    url_for(
                        "ai_detection.detection_image",
                        detection_id=report["ai_detection_id"],
                        image_kind=image_kind,
                        _external=True,
                    )
                    if report["ai_detection_id"] and report[column]
                    else None
                )
            for column in ("ai_original_image_path", "ai_annotated_image_path"):
                report.pop(column, None)
            if report.get("ai_confidence_score") is not None:
                report["ai_confidence_score"] = float(report["ai_confidence_score"])
            bounding_boxes = report.get("ai_bounding_boxes")
            if isinstance(bounding_boxes, bytes):
                report["ai_bounding_boxes"] = json.loads(bounding_boxes.decode("utf-8"))
            elif isinstance(bounding_boxes, str):
                report["ai_bounding_boxes"] = json.loads(bounding_boxes)
        reports.sort(
            key=lambda report: report["report_datetime"] or "",
            reverse=True,
        )
        reports.sort(
            key=lambda report: (
                PRIORITY_ORDER.get(
                    report["hazard_priority"], len(PRIORITY_ORDER)
                ),
                -report["priority_score"],
            )
        )
    except mysql.connector.Error:
        current_app.logger.exception("Could not load reports for admin review.")
        return jsonify({"error": "Reports could not be loaded. Try again later."}), 503
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"reports": reports}), 200


@admin_blueprint.put("/reports/priority")
@require_role("admin")
def update_report_priority():
    """Recalculate or manually override priority without changing report status."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send report_id, priority, and optional traffic_importance as JSON.", 400)

    report_id = data.get("report_id")
    priority = data.get("priority")
    traffic_importance = data.get("traffic_importance")
    if not isinstance(report_id, str) or not report_id.strip() or len(report_id) > 19:
        return _error("A valid report_id is required.", 400)
    if not isinstance(priority, str) or (priority != "auto" and priority not in VALID_PRIORITIES):
        return _error("Priority must be Critical, High, Medium, Low, or auto.", 400)
    if traffic_importance is not None and (
        not isinstance(traffic_importance, str)
        or traffic_importance not in VALID_TRAFFIC_IMPORTANCE
    ):
        return _error("Traffic importance must be Unknown, Low, Medium, or High.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT report_id, status, location_name
            FROM road_reports
            WHERE report_id = %s
            FOR UPDATE
            """,
            (report_id.strip(),),
        )
        report = cursor.fetchone()
        if report is None:
            connection.rollback()
            return _error("Report not found.", 404)
        if report["status"] == "Rejected":
            connection.rollback()
            return _error("Rejected reports cannot be reprioritized.", 409)

        if priority == "auto":
            cursor.execute(
                """
                UPDATE road_reports
                SET traffic_importance = COALESCE(%s, traffic_importance),
                    hazard_priority = NULL,
                    priority_source = 'Rule-Based'
                WHERE report_id = %s
                """,
                (traffic_importance, report["report_id"]),
            )
        else:
            cursor.execute(
                """
                UPDATE road_reports
                SET traffic_importance = COALESCE(%s, traffic_importance),
                    hazard_priority = %s,
                    priority_source = 'Admin Override'
                WHERE report_id = %s
                """,
                (traffic_importance, priority, report["report_id"]),
            )

        results = recalculate_location_priorities(cursor, report["location_name"])
        priority_result = results[report["report_id"]]
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not update road-report priority.")
        return _error("Priority could not be updated. Check the database migration and try again.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": (
                "Rule-based priority recalculated."
                if priority == "auto"
                else "Administrator priority override saved."
            ),
            "report_id": report_id.strip(),
            **priority_result,
        }
    ), 200


@admin_blueprint.put("/reports/approve")
@require_role("admin")
def approve_report():
    """Approve a report and create its verified, driver-visible hazard."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send report_id and severity as a JSON object.", 400)

    report_id = data.get("report_id")
    severity = data.get("severity")
    if not isinstance(report_id, str) or not report_id.strip() or len(report_id) > 19:
        return _error("A valid report_id is required.", 400)
    if not isinstance(severity, str) or severity not in VALID_SEVERITIES:
        return _error("Choose High, Medium, or Low severity.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT report_id, user_id, hazard_category, location_name, latitude,
                   longitude, status, traffic_importance
            FROM road_reports
            WHERE report_id = %s
            FOR UPDATE
            """,
            (report_id.strip(),),
        )
        report = cursor.fetchone()
        if report is None:
            connection.rollback()
            return _error("Report not found.", 404)
        if report["status"] != "Pending Review":
            connection.rollback()
            return _error("Only reports pending review can be approved.", 409)
        if report["latitude"] is None or report["longitude"] is None:
            connection.rollback()
            return _error(
                "This report has no GPS coordinates and cannot be added to driver alerts.",
                400,
            )

        hazard_id = f"HZ-{uuid.uuid4().hex[:16].upper()}"
        cursor.execute(
            """
            INSERT INTO hazards (
                hazard_id, report_id, category, location_name,
                latitude, longitude, severity, status, verification_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Active', 'Verified')
            """,
            (
                hazard_id,
                report["report_id"],
                report["hazard_category"],
                report["location_name"],
                report["latitude"],
                report["longitude"],
                severity,
            ),
        )
        cursor.execute(
            """
            UPDATE road_reports
            SET status = 'Approved',
                traffic_importance = COALESCE(traffic_importance, 'Unknown')
            WHERE report_id = %s
            """,
            (report["report_id"],),
        )
        priority_results = recalculate_location_priorities(
            cursor,
            report["location_name"],
        )
        report_priority = priority_results[report["report_id"]]
        add_notification(
            cursor,
            report["user_id"],
            "report_approved",
            "Road report approved",
            (
                f"Report {report['report_id']} was approved as "
                f"{report_priority['hazard_priority']} priority and added to active hazards."
            ),
            "report",
            report["report_id"],
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not approve citizen road report.")
        return _error("The report could not be approved. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": "Report approved and added to verified driver hazards.",
            "hazard": {
                "hazard_id": hazard_id,
                "report_id": report["report_id"],
                "category": report["hazard_category"],
                "severity": severity,
                "latitude": float(report["latitude"]),
                "longitude": float(report["longitude"]),
                "location_name": report["location_name"],
                "status": "Active",
                "verification_status": "Verified",
                **report_priority,
            },
        }
    ), 201


@admin_blueprint.put("/reports/reject")
@require_role("admin")
def reject_report():
    """Reject a pending citizen report and notify its owner."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send report_id and an optional reason as JSON.", 400)
    report_id = data.get("report_id")
    reason = data.get("reason", "")
    if not isinstance(report_id, str) or not report_id.strip() or len(report_id) > 19:
        return _error("A valid report_id is required.", 400)
    if not isinstance(reason, str) or len(reason.strip()) > 1000:
        return _error("The rejection reason must be 1000 characters or fewer.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT report_id, user_id, status, location_name
            FROM road_reports
            WHERE report_id = %s
            FOR UPDATE
            """,
            (report_id.strip(),),
        )
        report = cursor.fetchone()
        if report is None:
            connection.rollback()
            return _error("Report not found.", 404)
        if report["status"] != "Pending Review":
            connection.rollback()
            return _error("Only reports pending review can be rejected.", 409)

        cursor.execute(
            "UPDATE road_reports SET status = 'Rejected' WHERE report_id = %s",
            (report["report_id"],),
        )
        recalculate_location_priorities(cursor, report["location_name"])
        message = f"Report {report['report_id']} was rejected."
        if reason.strip():
            message = f"{message} Reason: {reason.strip()}"
        add_notification(
            cursor,
            report["user_id"],
            "report_rejected",
            "Road report rejected",
            message,
            "report",
            report["report_id"],
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not reject citizen road report.")
        return _error("The report could not be rejected. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"message": f"Report {report_id.strip()} was rejected."}), 200


@admin_blueprint.put("/hazards/resolve")
@require_role("admin")
def resolve_hazard():
    """Remove an admin-confirmed, repaired hazard from active driver results."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send hazard_id as a JSON object.", 400)
    hazard_id = data.get("hazard_id")
    if not isinstance(hazard_id, str) or not hazard_id.strip() or len(hazard_id) > 19:
        return _error("A valid hazard_id is required.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE hazards
            SET status = 'Resolved'
            WHERE hazard_id = %s
              AND verification_status = 'Verified'
              AND status IN ('Active', 'Assigned')
              AND EXISTS (
                SELECT 1
                FROM repair_tasks AS t
                WHERE t.hazard_id = hazards.hazard_id
                  AND t.status = 'Verified'
              )
            """,
            (hazard_id.strip(),),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return _error(
                "A hazard can be resolved only after its repair task is verified.",
                409,
            )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not resolve verified road hazard.")
        return _error("The hazard could not be resolved. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": "Hazard marked resolved. It will no longer appear in driver alerts.",
            "hazard_id": hazard_id.strip(),
            "status": "Resolved",
        }
    ), 200


@admin_blueprint.get("/workers")
@require_role("admin")
def list_workers():
    """List active worker profiles and optionally rank them for a hazard."""
    hazard_id = request.args.get("hazard_id", "").strip()
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        hazard = None
        if hazard_id:
            if len(hazard_id) > 19:
                return _error("A valid hazard_id is required.", 400)
            cursor.execute(
                """
                SELECT hazard_id, category, severity, latitude, longitude,
                       location_name
                FROM hazards
                WHERE hazard_id = %s
                  AND verification_status = 'Verified'
                  AND status = 'Active'
                """,
                (hazard_id,),
            )
            hazard = cursor.fetchone()
            if hazard is None:
                return _error("No active, verified hazard matched that ID.", 404)

        if hazard is None:
            cursor.execute(
                """
                SELECT w.worker_id, w.skills, w.availability, w.location_name,
                       w.latitude, w.longitude, u.full_name,
                       (
                         SELECT COUNT(*)
                         FROM repair_tasks AS t
                         WHERE t.worker_id = w.worker_id
                           AND t.status IN (
                             'Assigned', 'Accepted', 'In Progress',
                             'Awaiting Admin Verification'
                           )
                       ) AS current_workload
                FROM workers AS w
                INNER JOIN users AS u ON u.id = w.worker_id
                WHERE u.role = 'worker' AND u.status = 'active'
                ORDER BY u.full_name, w.worker_id
                """
            )
            workers = [
                {
                    "worker_id": int(row["worker_id"]),
                    "worker_code": f"RGW-{row['worker_id']}",
                    "name": row["full_name"],
                    "skills": _read_skills(row["skills"]),
                    "availability": row["availability"],
                    "available": row["availability"] == "Available",
                    "current_workload": int(row["current_workload"]),
                    "location_name": row["location_name"] or "Location not configured",
                    "latitude": _safe_coordinate(row["latitude"], -90, 90),
                    "longitude": _safe_coordinate(row["longitude"], -180, 180),
                }
                for row in cursor.fetchall()
            ]
            return jsonify({"workers": workers}), 200

        candidates, recommended_worker_id = _load_worker_candidates(cursor, hazard)
        return jsonify(
            {
                "hazard": {
                    "hazard_id": hazard["hazard_id"],
                    "category": hazard["category"],
                    "severity": hazard["severity"],
                    "location_name": hazard["location_name"],
                },
                "scoring": {
                    "skill_match": "40 points for an exact category skill match",
                    "availability": "25 points when marked Available",
                    "distance": "Up to 20 points; 2 points deducted per kilometer",
                    "workload": (
                        "Up to 15 points; each open task deducts 5 for High, "
                        "3 for Medium, or 2.5 for Low priority"
                    ),
                    "model": "Transparent rule-based ranking; not a trained AI model",
                },
                "recommended_worker_id": recommended_worker_id,
                "workers": candidates,
            }
        ), 200
    except mysql.connector.Error:
        current_app.logger.exception("Could not load worker allocation data.")
        return _error("Worker allocation data is unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@admin_blueprint.put("/workers/profile")
@require_role("admin")
def update_worker_profile():
    """Configure a worker's skill tags and base location for allocation scoring."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send worker_id, skills, and location details as JSON.", 400)
    worker_id = data.get("worker_id")
    skills = data.get("skills")
    location_name = data.get("location_name", "")
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    if isinstance(worker_id, bool) or not isinstance(worker_id, int) or worker_id < 1:
        return _error("A valid worker_id is required.", 400)
    if not isinstance(skills, list) or len(skills) > 20:
        return _error("Skills must be a list containing at most 20 categories.", 400)
    normalized_skills = []
    for skill in skills:
        if not isinstance(skill, str) or not skill.strip() or len(skill.strip()) > 50:
            return _error("Every skill must be a non-empty string of at most 50 characters.", 400)
        if skill.strip() not in ALLOWED_HAZARD_CATEGORIES:
            return _error("Choose skills from the supported road hazard categories.", 400)
        if skill.strip() not in normalized_skills:
            normalized_skills.append(skill.strip())
    if not isinstance(location_name, str) or len(location_name.strip()) > 255:
        return _error("location_name must be at most 255 characters.", 400)

    if latitude is None and longitude is None:
        safe_latitude = safe_longitude = None
    else:
        safe_latitude = _safe_coordinate(latitude, -90, 90)
        safe_longitude = _safe_coordinate(longitude, -180, 180)
        if safe_latitude is None or safe_longitude is None:
            return _error("Provide both valid latitude and longitude coordinates.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT w.worker_id
            FROM workers AS w
            INNER JOIN users AS u ON u.id = w.worker_id
            WHERE w.worker_id = %s
              AND u.role = 'worker'
              AND u.status = 'active'
            FOR UPDATE
            """,
            (worker_id,),
        )
        if cursor.fetchone() is None:
            connection.rollback()
            return _error("Active worker profile not found.", 404)
        cursor.execute(
            """
            UPDATE workers AS w
            INNER JOIN users AS u ON u.id = w.worker_id
            SET w.skills = %s, w.location_name = %s,
                w.latitude = %s, w.longitude = %s
            WHERE w.worker_id = %s
              AND u.role = 'worker'
              AND u.status = 'active'
            """,
            (
                json.dumps(normalized_skills),
                location_name.strip() or None,
                safe_latitude,
                safe_longitude,
                worker_id,
            ),
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not update worker allocation profile.")
        return _error("The worker profile could not be updated. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": "Worker skills and base location updated.",
            "worker": {
                "worker_id": worker_id,
                "skills": normalized_skills,
                "location_name": location_name.strip() or "Location not configured",
                "latitude": safe_latitude,
                "longitude": safe_longitude,
            },
        }
    ), 200


@admin_blueprint.post("/assign-worker")
@require_role("admin")
def assign_worker():
    """Create a repair task after recalculating the selected worker's score."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send hazard_id and worker_id as a JSON object.", 400)
    hazard_id = data.get("hazard_id")
    worker_id = data.get("worker_id")
    if not isinstance(hazard_id, str) or not hazard_id.strip() or len(hazard_id) > 19:
        return _error("A valid hazard_id is required.", 400)
    if isinstance(worker_id, bool) or not isinstance(worker_id, int) or worker_id < 1:
        return _error("A valid worker_id is required.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT hazard_id, category, severity, latitude, longitude,
                   location_name
            FROM hazards
            WHERE hazard_id = %s
              AND verification_status = 'Verified'
              AND status = 'Active'
            FOR UPDATE
            """,
            (hazard_id.strip(),),
        )
        hazard = cursor.fetchone()
        if hazard is None:
            connection.rollback()
            return _error("No active, verified hazard matched that ID.", 404)

        cursor.execute(
            """
            SELECT w.worker_id, w.skills, w.availability, w.location_name,
                   w.latitude, w.longitude, u.full_name
            FROM workers AS w
            INNER JOIN users AS u ON u.id = w.worker_id
            WHERE w.worker_id = %s
              AND u.role = 'worker'
              AND u.status = 'active'
            FOR UPDATE
            """,
            (worker_id,),
        )
        worker = cursor.fetchone()
        if worker is None:
            connection.rollback()
            return _error("Worker profile not found.", 404)
        if worker["availability"] != "Available":
            connection.rollback()
            return _error("The selected worker is not currently available.", 409)

        cursor.execute(
            """
            SELECT COUNT(*) AS current_workload
            FROM repair_tasks
            WHERE worker_id = %s
              AND status IN (
                'Assigned', 'Accepted', 'In Progress',
                'Awaiting Admin Verification'
              )
            """,
            (worker_id,),
        )
        workload = int(cursor.fetchone()["current_workload"])
        scored_worker = _score_worker(worker, hazard, workload)
        task_id = f"RT-{uuid.uuid4().hex[:16].upper()}"
        cursor.execute(
            """
            INSERT INTO repair_tasks (
                task_id, hazard_id, worker_id, assigned_by, priority, status
            )
            VALUES (%s, %s, %s, %s, %s, 'Assigned')
            """,
            (
                task_id,
                hazard["hazard_id"],
                worker_id,
                session["user_id"],
                hazard["severity"],
            ),
        )
        add_notification(
            cursor,
            worker_id,
            "task_assigned",
            "New repair task assigned",
            f"Task {task_id} was assigned to you for {hazard['category']} at {hazard['location_name']}.",
            "repair_task",
            task_id,
        )
        cursor.execute(
            """
            UPDATE workers
            SET availability = 'On Task'
            WHERE worker_id = %s AND availability = 'Available'
            """,
            (worker_id,),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return _error("The worker's availability changed before assignment.", 409)
        cursor.execute(
            """
            UPDATE hazards
            SET status = 'Assigned'
            WHERE hazard_id = %s AND status = 'Active'
            """,
            (hazard["hazard_id"],),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return _error("The hazard changed before it could be assigned.", 409)
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not assign a worker to the hazard.")
        return _error("The repair task could not be assigned. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": f"Task {task_id} assigned to {worker['full_name']}.",
            "task": {
                "task_id": task_id,
                "hazard_id": hazard["hazard_id"],
                "worker_id": worker_id,
                "worker_name": worker["full_name"],
                "priority": hazard["severity"],
                "status": "Assigned",
                "allocation_score": scored_worker["score"],
                "score_breakdown": scored_worker["score_breakdown"],
                "recommendation_reason": scored_worker["recommendation_reason"],
            },
        }
    ), 201


@admin_blueprint.get("/repair-tasks")
@require_role("admin")
def list_repair_tasks():
    """List repair tasks and verified hazards that can be assigned."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT t.task_id, t.hazard_id, h.category, h.location_name,
                   h.latitude, h.longitude, t.priority, t.status,
                   t.assigned_at, t.updated_at, t.worker_id, u.full_name AS worker_name
            FROM repair_tasks AS t
            INNER JOIN hazards AS h ON h.hazard_id = t.hazard_id
            INNER JOIN users AS u ON u.id = t.worker_id
            ORDER BY t.assigned_at DESC, t.task_id
            """
        )
        tasks = cursor.fetchall()
        for task in tasks:
            for field in ("assigned_at", "updated_at"):
                if task[field] is not None:
                    task[field] = task[field].isoformat(sep=" ", timespec="seconds")
            task["latitude"] = _safe_coordinate(task["latitude"], -90, 90)
            task["longitude"] = _safe_coordinate(task["longitude"], -180, 180)
            task["worker_id"] = int(task["worker_id"])

        cursor.execute(
            """
            SELECT h.hazard_id, h.report_id, h.category, h.location_name,
                   h.latitude, h.longitude, h.severity
            FROM hazards AS h
            WHERE h.verification_status = 'Verified'
              AND h.status = 'Active'
            ORDER BY FIELD(h.severity, 'High', 'Medium', 'Low'), h.created_at
            """
        )
        assignable_hazards = cursor.fetchall()
        for hazard in assignable_hazards:
            hazard["latitude"] = _safe_coordinate(hazard["latitude"], -90, 90)
            hazard["longitude"] = _safe_coordinate(hazard["longitude"], -180, 180)
    except mysql.connector.Error:
        current_app.logger.exception("Could not load repair task data.")
        return _error("Repair task data is unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {"repair_tasks": tasks, "assignable_hazards": assignable_hazards}
    ), 200


@admin_blueprint.get("/hazards")
@require_role("admin")
def list_hazards():
    """Return all database hazards for the admin map and statistics."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT h.hazard_id, h.report_id, h.category, h.location_name,
                   h.latitude, h.longitude, h.severity, h.status,
                   h.verification_status, r.traffic_importance,
                   r.priority_source, r.hazard_priority, r.priority_score,
                   r.priority_report_count,
                   (SELECT COUNT(*)
                    FROM road_reports AS same_location
                    WHERE LOWER(TRIM(same_location.location_name)) =
                          LOWER(TRIM(r.location_name))
                      AND same_location.status <> 'Rejected'
                   ) AS current_location_report_count,
                   t.task_id, t.worker_id,
                   u.full_name AS worker_name
            FROM hazards AS h
            LEFT JOIN road_reports AS r ON r.report_id = h.report_id
            LEFT JOIN repair_tasks AS t
              ON t.hazard_id = h.hazard_id
             AND t.status NOT IN ('Declined', 'Rejected')
            LEFT JOIN users AS u ON u.id = t.worker_id
            ORDER BY h.created_at DESC, h.hazard_id
            """
        )
        hazards = cursor.fetchall()
        for hazard in hazards:
            hazard["latitude"] = _safe_coordinate(hazard["latitude"], -90, 90)
            hazard["longitude"] = _safe_coordinate(hazard["longitude"], -180, 180)
            hazard.update(
                present_priority(
                    hazard,
                    int(
                        hazard.get("current_location_report_count")
                        or hazard.get("priority_report_count")
                        or 1
                    ),
                )
            )
            hazard.pop("current_location_report_count", None)
        hazards.sort(
            key=lambda hazard: (
                PRIORITY_ORDER.get(
                    hazard["hazard_priority"], len(PRIORITY_ORDER)
                ),
                -hazard["priority_score"],
            )
        )
    except mysql.connector.Error:
        current_app.logger.exception("Could not load admin hazard records.")
        return _error("Hazard records are unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"hazards": hazards}), 200


@admin_blueprint.get("/verification")
@require_role("admin")
def list_repair_verification():
    """List repair evidence that is awaiting an administrator decision."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT e.evidence_id, e.task_id, e.worker_id, e.latitude,
                   e.longitude, e.repair_description, e.completion_datetime,
                   e.status, t.hazard_id, t.priority, h.category,
                   h.location_name, w.full_name AS worker_name
            FROM repair_evidence AS e
            INNER JOIN repair_tasks AS t ON t.task_id = e.task_id
            INNER JOIN hazards AS h ON h.hazard_id = t.hazard_id
            INNER JOIN users AS w ON w.id = e.worker_id
            WHERE e.status = 'Submitted'
              AND t.status = 'Awaiting Admin Verification'
            ORDER BY e.completion_datetime, e.evidence_id
            """
        )
        evidence = cursor.fetchall()
        for item in evidence:
            if item["completion_datetime"] is not None:
                item["completion_datetime"] = item["completion_datetime"].isoformat(
                    sep=" ", timespec="seconds"
                )
            item["latitude"] = _safe_coordinate(item["latitude"], -90, 90)
            item["longitude"] = _safe_coordinate(item["longitude"], -180, 180)
            item["before_image_url"] = url_for(
                "worker.repair_evidence_image",
                evidence_id=item["evidence_id"],
                image_kind="before",
                _external=True,
            )
            item["after_image_url"] = url_for(
                "worker.repair_evidence_image",
                evidence_id=item["evidence_id"],
                image_kind="after",
                _external=True,
            )
    except mysql.connector.Error:
        current_app.logger.exception("Could not load repair evidence for review.")
        return _error("Repair evidence is unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"evidence": evidence}), 200


@admin_blueprint.get("/activities")
@require_role("admin")
def list_volunteer_activities():
    """List activities and their worker participant submissions."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT a.activity_id, a.name, a.description, a.location_name,
                   a.activity_date, a.required_volunteers, a.volunteer_points,
                   a.status, a.created_at,
                   COUNT(p.participant_id) AS participant_count
            FROM volunteer_activities AS a
            LEFT JOIN volunteer_participants AS p
              ON p.activity_id = a.activity_id
             AND p.status != 'Rejected'
            GROUP BY a.activity_id
            ORDER BY a.activity_date DESC, a.created_at DESC
            """
        )
        activities = cursor.fetchall()
        for activity in activities:
            if activity["activity_date"] is not None:
                activity["activity_date"] = activity["activity_date"].isoformat()
            if activity["created_at"] is not None:
                activity["created_at"] = activity["created_at"].isoformat(
                    sep=" ", timespec="seconds"
                )

        cursor.execute(
            """
            SELECT p.participant_id, p.activity_id, p.worker_id, p.status,
                   p.completion_details, p.joined_at, p.completed_at,
                   p.review_notes, u.full_name AS worker_name
            FROM volunteer_participants AS p
            INNER JOIN users AS u ON u.id = p.worker_id
            ORDER BY p.activity_id, p.joined_at, p.participant_id
            """
        )
        participants = cursor.fetchall()
        for participant in participants:
            for field in ("joined_at", "completed_at"):
                if participant[field] is not None:
                    participant[field] = participant[field].isoformat(
                        sep=" ", timespec="seconds"
                    )
        participant_map = {}
        for participant in participants:
            participant_map.setdefault(participant["activity_id"], []).append(
                participant
            )
        for activity in activities:
            activity["participants"] = participant_map.get(
                activity["activity_id"], []
            )
    except mysql.connector.Error:
        current_app.logger.exception("Could not load volunteer activities.")
        return _error("Volunteer activities are unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"activities": activities}), 200


@admin_blueprint.post("/activities")
@require_role("admin")
def create_volunteer_activity():
    """Create an optional activity and announce it to active workers."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send activity details as a JSON object.", 400)
    name = data.get("name")
    description = data.get("description")
    location = data.get("location")
    date_value = data.get("date")
    volunteers = data.get("required_volunteers")
    points = data.get("volunteer_points", 0)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        return _error("Activity name is required and must be at most 80 characters.", 400)
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1000:
        return _error("Activity description is required and must be at most 1000 characters.", 400)
    if not isinstance(location, str) or not 1 <= len(location.strip()) <= 255:
        return _error("Activity location is required and must be at most 255 characters.", 400)
    if not isinstance(date_value, str):
        return _error("A valid activity date is required.", 400)
    try:
        activity_date = datetime.strptime(date_value, "%Y-%m-%d").date()
    except ValueError:
        return _error("Activity date must use YYYY-MM-DD format.", 400)
    if isinstance(volunteers, bool) or not isinstance(volunteers, int) or not 1 <= volunteers <= 500:
        return _error("required_volunteers must be a whole number from 1 to 500.", 400)
    if isinstance(points, bool) or not isinstance(points, int) or not 0 <= points <= 500:
        return _error("volunteer_points must be a whole number from 0 to 500.", 400)

    activity_id = f"VA-{uuid.uuid4().hex[:16].upper()}"
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO volunteer_activities (
                activity_id, name, description, location_name, activity_date,
                required_volunteers, volunteer_points, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                activity_id,
                name.strip(),
                description.strip(),
                location.strip(),
                activity_date,
                volunteers,
                points,
                session["user_id"],
            ),
        )
        notify_role(
            cursor,
            "worker",
            "new_volunteer_activity",
            "New optional volunteer activity",
            f"{name.strip()} is available at {location.strip()} on {activity_date.isoformat()}.",
            "volunteer_activity",
            activity_id,
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not create volunteer activity.")
        return _error("The volunteer activity could not be created. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": "Optional activity created and shared with workers.",
            "activity": {
                "activity_id": activity_id,
                "name": name.strip(),
                "description": description.strip(),
                "location_name": location.strip(),
                "activity_date": activity_date.isoformat(),
                "required_volunteers": volunteers,
                "volunteer_points": points,
                "status": "Open",
            },
        }
    ), 201


def _review_volunteer_submission(action):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send participant_id and an optional review_notes field as JSON.", 400)
    participant_id = data.get("participant_id")
    notes = data.get("review_notes", "")
    if isinstance(participant_id, bool) or not isinstance(participant_id, int) or participant_id < 1:
        return _error("A valid participant_id is required.", 400)
    if not isinstance(notes, str) or len(notes.strip()) > 1000:
        return _error("Review notes must be 1000 characters or fewer.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT p.participant_id, p.worker_id, p.status, p.activity_id,
                   a.name, a.volunteer_points
            FROM volunteer_participants AS p
            INNER JOIN volunteer_activities AS a ON a.activity_id = p.activity_id
            WHERE p.participant_id = %s
            FOR UPDATE
            """,
            (participant_id,),
        )
        participant = cursor.fetchone()
        if participant is None:
            connection.rollback()
            return _error("Volunteer submission not found.", 404)
        if participant["status"] != "Submitted":
            connection.rollback()
            return _error("Only submitted volunteer activity completions can be reviewed.", 409)

        next_status = "Approved" if action == "approve" else "Rejected"
        cursor.execute(
            """
            UPDATE volunteer_participants
            SET status = %s, review_notes = %s,
                reviewed_by = %s, reviewed_at = CURRENT_TIMESTAMP
            WHERE participant_id = %s
            """,
            (next_status, notes.strip() or None, session["user_id"], participant_id),
        )
        reward_awarded = False
        if action == "approve":
            cursor.execute(
                """
                INSERT IGNORE INTO rewards (
                    worker_id, reward_type, participant_id, points
                )
                VALUES (%s, 'Volunteer', %s, %s)
                """,
                (
                    participant["worker_id"],
                    participant_id,
                    participant["volunteer_points"],
                ),
            )
            reward_awarded = cursor.rowcount == 1
        add_notification(
            cursor,
            participant["worker_id"],
            "volunteer_approved" if action == "approve" else "volunteer_rejected",
            "Volunteer activity approved" if action == "approve" else "Volunteer activity needs changes",
            (
                f"Your completion for {participant['name']} was approved."
                if action == "approve"
                else f"Your completion for {participant['name']} was rejected. {notes.strip()}".strip()
            ),
            "volunteer_activity",
            participant["activity_id"],
        )
        if reward_awarded:
            add_notification(
                cursor,
                participant["worker_id"],
                "reward_points_updated",
                "Volunteer points updated",
                f"{participant['volunteer_points']} volunteer points were awarded for {participant['name']}.",
                "volunteer_activity",
                participant["activity_id"],
            )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not review volunteer activity completion.")
        return _error("The volunteer completion could not be reviewed. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": "Volunteer completion approved and points awarded."
            if action == "approve"
            else "Volunteer completion rejected.",
            "participant_id": participant_id,
            "status": next_status,
            "points_awarded": participant["volunteer_points"] if reward_awarded else 0,
        }
    ), 200


@admin_blueprint.put("/activities/approve")
@require_role("admin")
def approve_volunteer_activity():
    """Approve one worker's completion and award volunteer points once."""
    return _review_volunteer_submission("approve")


@admin_blueprint.put("/activities/reject")
@require_role("admin")
def reject_volunteer_activity():
    """Reject one worker's completion and notify that worker."""
    return _review_volunteer_submission("reject")


@admin_blueprint.get("/rewards")
@require_role("admin")
def list_worker_rewards():
    """Return separate verified repair and volunteer point totals."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT w.worker_id, u.full_name AS worker_name,
                   COALESCE(r.repair_points, 0) AS repair_points,
                   COALESCE(r.volunteer_points, 0) AS volunteer_points,
                   COALESCE(t.completed_tasks, 0) AS completed_tasks,
                   COALESCE(t.verified_repairs, 0) AS verified_repairs
            FROM workers AS w
            INNER JOIN users AS u ON u.id = w.worker_id
            LEFT JOIN (
              SELECT worker_id,
                     SUM(CASE WHEN reward_type = 'Repair'
                              THEN points ELSE 0 END) AS repair_points,
                     SUM(CASE WHEN reward_type = 'Volunteer'
                              THEN points ELSE 0 END) AS volunteer_points
              FROM rewards
              GROUP BY worker_id
            ) AS r ON r.worker_id = w.worker_id
            LEFT JOIN (
              SELECT worker_id,
                     COUNT(CASE WHEN status IN (
                       'Awaiting Admin Verification', 'Verified'
                     ) THEN 1 END) AS completed_tasks,
                     COUNT(CASE WHEN status = 'Verified' THEN 1 END)
                       AS verified_repairs
              FROM repair_tasks
              GROUP BY worker_id
            ) AS t ON t.worker_id = w.worker_id
            ORDER BY u.full_name, w.worker_id
            """
        )
        rewards = cursor.fetchall()
        for reward in rewards:
            for field in (
                "repair_points",
                "volunteer_points",
                "completed_tasks",
                "verified_repairs",
            ):
                reward[field] = int(reward[field])
            reward["total_points"] = (
                reward["repair_points"] + reward["volunteer_points"]
            )
    except mysql.connector.Error:
        current_app.logger.exception("Could not load worker reward totals.")
        return _error("Worker reward totals are unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"workers": rewards}), 200


def _review_repair_evidence(action):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send evidence_id and optional review_notes as JSON.", 400)
    evidence_id = data.get("evidence_id")
    notes = data.get("review_notes", "")
    if isinstance(evidence_id, bool) or not isinstance(evidence_id, int) or evidence_id < 1:
        return _error("A valid evidence_id is required.", 400)
    if not isinstance(notes, str) or len(notes.strip()) > 1000:
        return _error("Review notes must be 1000 characters or fewer.", 400)
    if action != "approve" and not notes.strip():
        return _error("Provide review notes when rejecting or requesting more evidence.", 400)

    connection = None
    cursor = None
    reward_awarded = False
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT e.evidence_id, e.task_id, e.worker_id, e.status AS evidence_status,
                   t.hazard_id, t.status AS task_status, h.report_id,
                   r.user_id AS citizen_user_id
            FROM repair_evidence AS e
            INNER JOIN repair_tasks AS t ON t.task_id = e.task_id
            INNER JOIN hazards AS h ON h.hazard_id = t.hazard_id
            INNER JOIN road_reports AS r ON r.report_id = h.report_id
            WHERE e.evidence_id = %s
            FOR UPDATE
            """,
            (evidence_id,),
        )
        evidence = cursor.fetchone()
        if evidence is None:
            connection.rollback()
            return _error("Repair evidence not found.", 404)
        if (
            evidence["evidence_status"] != "Submitted"
            or evidence["task_status"] != "Awaiting Admin Verification"
        ):
            connection.rollback()
            return _error("This repair evidence is no longer awaiting review.", 409)

        if action == "approve":
            cursor.execute(
                """
                UPDATE repair_evidence
                SET status = 'Approved', review_notes = %s,
                    reviewed_by = %s, reviewed_at = CURRENT_TIMESTAMP
                WHERE evidence_id = %s AND status = 'Submitted'
                """,
                (notes.strip() or None, session["user_id"], evidence_id),
            )
            cursor.execute(
                "UPDATE repair_tasks SET status = 'Verified' WHERE task_id = %s",
                (evidence["task_id"],),
            )
            cursor.execute(
                """
                UPDATE hazards
                SET status = 'Resolved'
                WHERE hazard_id = %s
                  AND verification_status = 'Verified'
                  AND status IN ('Active', 'Assigned')
                """,
                (evidence["hazard_id"],),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return _error("The related verified hazard could not be resolved.", 409)
            cursor.execute(
                "UPDATE road_reports SET status = 'Completed' WHERE report_id = %s",
                (evidence["report_id"],),
            )
            cursor.execute(
                """
                INSERT IGNORE INTO rewards (
                    worker_id, reward_type, task_id, points
                )
                VALUES (%s, 'Repair', %s, 100)
                """,
                (evidence["worker_id"], evidence["task_id"]),
            )
            reward_awarded = cursor.rowcount == 1
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
                (evidence["worker_id"],),
            )
            if int(cursor.fetchone()["open_task_count"]) == 0:
                cursor.execute(
                    """
                    UPDATE workers SET availability = 'Available'
                    WHERE worker_id = %s AND availability = 'On Task'
                    """,
                    (evidence["worker_id"],),
                )
            add_notification(
                cursor,
                evidence["worker_id"],
                "repair_approved",
                "Repair evidence approved",
                f"Task {evidence['task_id']} was verified by an administrator.",
                "repair_task",
                evidence["task_id"],
            )
            if reward_awarded:
                add_notification(
                    cursor,
                    evidence["worker_id"],
                    "reward_points_updated",
                    "Repair reward points updated",
                    f"100 official repair points were awarded for task {evidence['task_id']}.",
                    "repair_task",
                    evidence["task_id"],
                )
            add_notification(
                cursor,
                evidence["citizen_user_id"],
                "repair_completed",
                "Reported road issue repaired",
                f"The repair for report {evidence['report_id']} was verified and marked complete.",
                "report",
                evidence["report_id"],
            )
            next_status = "Verified"
            message = "Repair verified, hazard resolved, and eligible repair rewards updated."
        else:
            evidence_status = (
                "Rejected" if action == "reject" else "Additional Evidence Requested"
            )
            cursor.execute(
                """
                UPDATE repair_evidence
                SET status = %s, review_notes = %s,
                    reviewed_by = %s, reviewed_at = CURRENT_TIMESTAMP
                WHERE evidence_id = %s AND status = 'Submitted'
                """,
                (evidence_status, notes.strip(), session["user_id"], evidence_id),
            )
            cursor.execute(
                "UPDATE repair_tasks SET status = 'In Progress' WHERE task_id = %s",
                (evidence["task_id"],),
            )
            add_notification(
                cursor,
                evidence["worker_id"],
                "repair_evidence_rejected" if action == "reject" else "repair_evidence_more",
                "Repair evidence rejected" if action == "reject" else "Additional repair evidence requested",
                f"Task {evidence['task_id']}: {notes.strip()}",
                "repair_task",
                evidence["task_id"],
            )
            next_status = "In Progress"
            message = (
                "Repair evidence rejected and returned to the worker."
                if action == "reject"
                else "Additional repair evidence requested from the worker."
            )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not review repair evidence.")
        return _error("Repair evidence could not be reviewed. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": message,
            "evidence_id": evidence_id,
            "task_id": evidence["task_id"],
            "task_status": next_status,
            "reward_points_awarded": 100 if reward_awarded else 0,
            "hazard_status": "Resolved" if action == "approve" else "Assigned",
        }
    ), 200


@admin_blueprint.put("/verification/approve")
@require_role("admin")
def approve_repair_evidence():
    """Verify evidence, resolve the hazard, notify affected users, and reward once."""
    return _review_repair_evidence("approve")


@admin_blueprint.put("/verification/reject")
@require_role("admin")
def reject_repair_evidence():
    """Reject repair evidence and send the task back for a new submission."""
    return _review_repair_evidence("reject")


@admin_blueprint.put("/verification/request-more")
@require_role("admin")
def request_more_repair_evidence():
    """Return a repair task to its worker with a request for additional evidence."""
    return _review_repair_evidence("request")
