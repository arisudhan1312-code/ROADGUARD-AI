"""Registration and login endpoints."""

import re

import mysql.connector
from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db_connection


auth_blueprint = Blueprint("auth", __name__, url_prefix="/api")

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MOBILE_PATTERN = re.compile(r"^\+?[0-9\s().-]+$")
PUBLIC_ROLES = {"citizen", "driver", "worker"}
MAX_PASSWORD_LENGTH = 128


def _read_json_object():
    """Return a JSON object or a consistent client error response."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({"error": "Send the form as a JSON object."}), 400)
    return payload, None


def _required_text(payload, field, label, maximum):
    value = payload.get(field)
    if not isinstance(value, str):
        return None, f"{label} is required."
    value = value.strip()
    if not value:
        return None, f"{label} is required."
    if len(value) > maximum:
        return None, f"{label} must be {maximum} characters or fewer."
    return value, None


@auth_blueprint.post("/register")
def register():
    """Create a Citizen, Driver, or Worker account with a one-way password hash."""
    payload, error_response = _read_json_object()
    if error_response:
        return error_response

    name, error = _required_text(payload, "name", "Full name", 100)
    if error:
        return jsonify({"error": error}), 400
    if len(name) < 2:
        return jsonify({"error": "Full name must be at least 2 characters."}), 400

    email, error = _required_text(payload, "email", "Email address", 254)
    if error:
        return jsonify({"error": error}), 400
    email = email.lower()
    if not EMAIL_PATTERN.fullmatch(email):
        return jsonify({"error": "Enter a valid email address."}), 400

    mobile, error = _required_text(payload, "mobile", "Mobile number", 20)
    if error:
        return jsonify({"error": error}), 400
    digits = re.sub(r"\D", "", mobile)
    if not MOBILE_PATTERN.fullmatch(mobile) or not 7 <= len(digits) <= 15:
        return jsonify({"error": "Enter a mobile number with 7 to 15 digits."}), 400

    role, error = _required_text(payload, "role", "User role", 20)
    if error:
        return jsonify({"error": error}), 400
    role = role.lower()
    if role not in PUBLIC_ROLES:
        return jsonify(
            {"error": "Choose Citizen, Driver, or Worker. Admin accounts are provisioned separately."}
        ), 400

    password = payload.get("password")
    if not isinstance(password, str) or not 8 <= len(password) <= MAX_PASSWORD_LENGTH:
        return jsonify(
            {"error": "Password must be between 8 and 128 characters."}
        ), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO users (full_name, email, mobile, password_hash, role)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (name, email, mobile, generate_password_hash(password), role),
        )
        user_id = cursor.lastrowid
        if role == "worker":
            cursor.execute(
                """
                INSERT INTO workers (worker_id, skills, availability)
                VALUES (%s, JSON_ARRAY(), 'Available')
                """,
                (user_id,),
            )
        connection.commit()
    except mysql.connector.IntegrityError as error:
        if connection is not None:
            connection.rollback()
        if error.errno == mysql.connector.errorcode.ER_DUP_ENTRY:
            return jsonify({"error": "An account with that email already exists."}), 409
        current_app.logger.exception("Account registration failed.")
        return jsonify({"error": "The account could not be created."}), 503
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        current_app.logger.exception("Database error during account registration.")
        return jsonify({"error": "The database is unavailable. Try again later."}), 503
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "message": "Account created. You can now log in.",
            "user": {"id": user_id, "name": name, "email": email, "role": role},
        }
    ), 201


@auth_blueprint.post("/login")
def login():
    """Check a stored password hash and start a signed Flask session."""
    payload, error_response = _read_json_object()
    if error_response:
        return error_response

    email, error = _required_text(payload, "email", "Email address", 254)
    if error:
        return jsonify({"error": error}), 400
    email = email.lower()
    if not EMAIL_PATTERN.fullmatch(email):
        return jsonify({"error": "Enter a valid email address."}), 400

    password = payload.get("password")
    if not isinstance(password, str) or not password or len(password) > MAX_PASSWORD_LENGTH:
        return jsonify({"error": "Enter a valid email address and password."}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, full_name, email, password_hash, role, status
            FROM users
            WHERE email = %s
            LIMIT 1
            """,
            (email,),
        )
        user = cursor.fetchone()
    except mysql.connector.Error:
        current_app.logger.exception("Database error during login.")
        return jsonify({"error": "The database is unavailable. Try again later."}), 503
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    if (
        user is None
        or user["status"] != "active"
        or not check_password_hash(user["password_hash"], password)
    ):
        return jsonify({"error": "Invalid email or password."}), 401

    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["name"] = user["full_name"]

    return jsonify(
        {
            "message": "Login successful.",
            "user": {
                "id": user["id"],
                "name": user["full_name"],
                "email": user["email"],
                "role": user["role"],
            },
        }
    ), 200


@auth_blueprint.post("/logout")
def logout():
    """Clear the signed-in session and expire its browser cookie."""
    session.clear()
    return jsonify({"message": "You have been logged out."}), 200
