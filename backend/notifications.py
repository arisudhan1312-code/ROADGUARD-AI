"""Role-scoped notification APIs and transactional notification helpers."""

import math
import time

import mysql.connector
from flask import Blueprint, current_app, jsonify, request, session

from access import require_role
from database import get_db_connection


notifications_blueprint = Blueprint("notifications", __name__, url_prefix="/api/notifications")
ALL_ROLES = ("citizen", "driver", "worker", "admin")
EARTH_RADIUS_METERS = 6_371_000


def add_notification(
    cursor,
    recipient_user_id,
    notification_type,
    title,
    message,
    related_entity_type=None,
    related_entity_id=None,
    event_key=None,
):
    """Insert a notification using the caller's current database transaction."""
    cursor.execute(
        """
        INSERT INTO notifications (
            recipient_user_id, notification_type, title, message,
            related_entity_type, related_entity_id, event_key
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            recipient_user_id,
            notification_type,
            title,
            message,
            related_entity_type,
            related_entity_id,
            event_key,
        ),
    )


def notify_role(
    cursor,
    role,
    notification_type,
    title,
    message,
    related_entity_type=None,
    related_entity_id=None,
):
    """Notify each active account in a role within the current transaction."""
    cursor.execute(
        "SELECT id FROM users WHERE role = %s AND status = 'active'",
        (role,),
    )
    for row in cursor.fetchall():
        recipient_user_id = row["id"] if isinstance(row, dict) else row[0]
        add_notification(
            cursor,
            recipient_user_id,
            notification_type,
            title,
            message,
            related_entity_type,
            related_entity_id,
        )


def _error(message, status):
    return jsonify({"error": message}), status


def _number(data, name, minimum, maximum):
    value = data.get(name)
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        return None
    return number


def _distance_meters(latitude_a, longitude_a, latitude_b, longitude_b):
    radians = math.radians
    latitude_delta = radians(latitude_b - latitude_a)
    longitude_delta = radians(longitude_b - longitude_a)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(radians(latitude_a))
        * math.cos(radians(latitude_b))
        * math.sin(longitude_delta / 2) ** 2
    )
    value = min(1.0, max(0.0, value))
    return 2 * EARTH_RADIUS_METERS * math.atan2(
        math.sqrt(value), math.sqrt(1 - value)
    )


@notifications_blueprint.get("")
@require_role(*ALL_ROLES)
def list_notifications():
    """Return only notifications addressed to the signed-in user."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT notification_id, notification_type, title, message,
                   related_entity_type, related_entity_id, created_at, read_at
            FROM notifications
            WHERE recipient_user_id = %s
            ORDER BY created_at DESC, notification_id DESC
            LIMIT 100
            """,
            (session["user_id"],),
        )
        notifications = cursor.fetchall()
        for item in notifications:
            for field in ("created_at", "read_at"):
                if item[field] is not None:
                    item[field] = item[field].isoformat(sep=" ", timespec="seconds")
    except mysql.connector.Error:
        current_app.logger.exception("Could not load notifications.")
        return _error("Notifications are unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"notifications": notifications}), 200


@notifications_blueprint.put("/read")
@require_role(*ALL_ROLES)
def mark_notification_read():
    """Mark one notification as read only when it belongs to this user."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send notification_id as a JSON object.", 400)
    notification_id = data.get("notification_id")
    if isinstance(notification_id, bool) or not isinstance(notification_id, int) or notification_id < 1:
        return _error("A valid notification_id is required.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE notifications
            SET read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
            WHERE notification_id = %s AND recipient_user_id = %s
            """,
            (notification_id, session["user_id"]),
        )
        if cursor.rowcount == 0:
            cursor.execute(
                """
                SELECT notification_id
                FROM notifications
                WHERE notification_id = %s AND recipient_user_id = %s
                """,
                (notification_id, session["user_id"]),
            )
            if cursor.fetchone() is None:
                connection.rollback()
                return _error("Notification not found for the signed-in user.", 404)
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not mark notification as read.")
        return _error("The notification could not be updated. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify({"message": "Notification marked as read."}), 200


@notifications_blueprint.post("/hazard-warning")
@require_role("driver")
def create_hazard_warning():
    """Persist a nearby warning only after checking the hazard and coordinates."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send hazard_id, latitude, longitude, and radius as JSON.", 400)

    hazard_id = data.get("hazard_id")
    if not isinstance(hazard_id, str) or not hazard_id.strip() or len(hazard_id) > 19:
        return _error("A valid hazard_id is required.", 400)
    latitude = _number(data, "latitude", -90, 90)
    longitude = _number(data, "longitude", -180, 180)
    radius = _number(data, "radius", 1, 10_000)
    if latitude is None or longitude is None or radius is None:
        return _error("Valid latitude, longitude, and radius values are required.", 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT category, severity, latitude, longitude
            FROM hazards
            WHERE hazard_id = %s
              AND verification_status = 'Verified'
              AND status IN ('Active', 'Assigned')
            """,
            (hazard_id.strip(),),
        )
        hazard = cursor.fetchone()
        if hazard is None:
            return _error("No active, verified hazard matched that ID.", 404)
        distance = _distance_meters(
            latitude,
            longitude,
            float(hazard["latitude"]),
            float(hazard["longitude"]),
        )
        if distance > radius:
            return _error("The hazard is outside the submitted search radius.", 409)

        user_id = session["user_id"]
        event_bucket = int(time.time() // (15 * 60))
        event_key = f"driver-hazard:{user_id}:{hazard_id.strip()}:{event_bucket}"
        cursor.execute(
            """
            INSERT INTO notifications (
                recipient_user_id, notification_type, title, message,
                related_entity_type, related_entity_id, event_key
            )
            VALUES (%s, 'hazard_warning', %s, %s, 'hazard', %s, %s)
            ON DUPLICATE KEY UPDATE notification_id = LAST_INSERT_ID(notification_id)
            """,
            (
                user_id,
                f"{hazard['category']} nearby",
                (
                    f"{hazard['category']} detected approximately "
                    f"{round(distance)} metres away. Please slow down and watch the road."
                ),
                hazard_id.strip(),
                event_key,
            ),
        )
        created = cursor.rowcount == 1
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Could not save a driver hazard notification.")
        return _error("The hazard warning could not be saved. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": "Verified hazard warning recorded."
            if created
            else "A recent warning for this hazard was already recorded.",
            "created": created,
        }
    ), 201 if created else 200
