"""Authenticated driver APIs for verified hazards near a GPS position."""

import math

import mysql.connector
from flask import Blueprint, current_app, jsonify, request

from access import require_role
from database import get_db_connection
from hazard_priority import sort_priority_key
from hazard_priority_service import present_priority


driver_blueprint = Blueprint("driver", __name__, url_prefix="/api")
DEFAULT_RADIUS_METERS = 500
MAX_RADIUS_METERS = 10_000
EARTH_RADIUS_METERS = 6_371_000


def _error(message, status):
    return jsonify({"error": message}), status


def _parse_number(name, minimum, maximum, default=None):
    raw_value = request.args.get(name)
    if raw_value is None:
        if default is not None:
            return default, None
        return None, f"{name.replace('_', ' ').capitalize()} is required."
    if not raw_value.strip():
        return None, f"{name.replace('_', ' ').capitalize()} must be a number."

    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None, f"{name.replace('_', ' ').capitalize()} must be a number."

    if not math.isfinite(value) or not minimum <= value <= maximum:
        return None, f"{name.replace('_', ' ').capitalize()} is out of range."
    return value, None


def _distance_meters(latitude_a, longitude_a, latitude_b, longitude_b):
    """Return great-circle distance using the Haversine formula."""
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


@driver_blueprint.get("/hazards/nearby")
@require_role("driver")
def nearby_hazards():
    """Return verified, unresolved hazards within the requested radius."""
    latitude, error = _parse_number("latitude", -90, 90)
    if error:
        return _error(error, 400)
    longitude, error = _parse_number("longitude", -180, 180)
    if error:
        return _error(error, 400)
    radius, error = _parse_number(
        "radius", 1, MAX_RADIUS_METERS, DEFAULT_RADIUS_METERS
    )
    if error:
        return _error(error, 400)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT h.hazard_id, h.category, h.severity, h.latitude, h.longitude,
                   h.location_name, h.status, r.traffic_importance,
                   r.priority_source, r.hazard_priority, r.priority_score,
                   r.priority_report_count,
                   (SELECT COUNT(*)
                    FROM road_reports AS same_location
                    WHERE LOWER(TRIM(same_location.location_name)) =
                          LOWER(TRIM(r.location_name))
                      AND same_location.status <> 'Rejected'
                   ) AS current_location_report_count
            FROM hazards AS h
            LEFT JOIN road_reports AS r ON r.report_id = h.report_id
            WHERE verification_status = 'Verified'
              AND h.status IN ('Active', 'Assigned')
              AND h.latitude IS NOT NULL
              AND h.longitude IS NOT NULL
            """
        )
        rows = cursor.fetchall()
    except mysql.connector.Error:
        current_app.logger.exception("Could not load nearby road hazards.")
        return _error("Nearby hazards are unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    hazards = []
    for row in rows:
        hazard_latitude = float(row["latitude"])
        hazard_longitude = float(row["longitude"])
        distance = _distance_meters(
            latitude, longitude, hazard_latitude, hazard_longitude
        )
        if distance <= radius:
            hazards.append(
                {
                    "hazard_id": row["hazard_id"],
                    "category": row["category"],
                    "severity": row["severity"],
                    "latitude": hazard_latitude,
                    "longitude": hazard_longitude,
                    "location_name": row["location_name"],
                    "distance_meters": round(distance, 1),
                    "status": row["status"],
                    **present_priority(
                        row,
                        int(
                            row.get("current_location_report_count")
                            or row.get("priority_report_count")
                            or 1
                        ),
                    ),
                }
            )

    hazards.sort(
        key=lambda hazard: sort_priority_key(
            hazard["hazard_priority"],
            hazard["priority_score"],
            hazard["distance_meters"],
        )
    )
    pothole_count = sum(
        hazard["category"].casefold() == "pothole" for hazard in hazards
    )
    return jsonify(
        {
            "radius_meters": radius,
            "total_nearby_hazards": len(hazards),
            "total_nearby_potholes": pothole_count,
            "total_other_hazards": len(hazards) - pothole_count,
            "hazards": hazards,
        }
    ), 200


@driver_blueprint.get("/hazards")
@require_role("driver")
def list_active_hazards():
    """Return all verified unresolved hazards for route-risk comparisons."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT h.hazard_id, h.category, h.severity, h.latitude, h.longitude,
                   h.location_name, h.status, r.traffic_importance,
                   r.priority_source, r.hazard_priority, r.priority_score,
                   r.priority_report_count,
                   (SELECT COUNT(*)
                    FROM road_reports AS same_location
                    WHERE LOWER(TRIM(same_location.location_name)) =
                          LOWER(TRIM(r.location_name))
                      AND same_location.status <> 'Rejected'
                   ) AS current_location_report_count
            FROM hazards AS h
            LEFT JOIN road_reports AS r ON r.report_id = h.report_id
            WHERE verification_status = 'Verified'
              AND h.status IN ('Active', 'Assigned')
              AND h.latitude IS NOT NULL
              AND h.longitude IS NOT NULL
            ORDER BY h.created_at DESC, h.hazard_id
            """
        )
        rows = cursor.fetchall()
    except mysql.connector.Error:
        current_app.logger.exception("Could not load active hazards for route planning.")
        return _error("Active hazards are unavailable. Try again later.", 503)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "hazards": sorted(
                [
                    {
                        "hazard_id": row["hazard_id"],
                        "category": row["category"],
                        "severity": row["severity"],
                        "latitude": float(row["latitude"]),
                        "longitude": float(row["longitude"]),
                        "location_name": row["location_name"],
                        "status": row["status"],
                        **present_priority(
                            row,
                            int(
                                row.get("current_location_report_count")
                                or row.get("priority_report_count")
                                or 1
                            ),
                        ),
                    }
                    for row in rows
                ],
                key=lambda hazard: sort_priority_key(
                    hazard["hazard_priority"],
                    hazard["priority_score"],
                ),
            )
        }
    ), 200
