"""Small role checks for routes that require a logged-in session."""

from functools import wraps

import mysql.connector
from flask import current_app, jsonify, session

from database import get_db_connection


def require_role(*allowed_roles):
    """Require a signed-in, active account with a current allowed role."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user_id = session.get("user_id")
            session_role = session.get("role")
            if user_id is None or not session_role:
                return jsonify({"error": "Login is required to use this endpoint."}), 401

            connection = None
            cursor = None
            try:
                connection = get_db_connection()
                cursor = connection.cursor(dictionary=True)
                cursor.execute(
                    "SELECT role, status FROM users WHERE id = %s LIMIT 1",
                    (user_id,),
                )
                user = cursor.fetchone()
            except mysql.connector.Error:
                current_app.logger.exception("Could not validate the signed-in account.")
                return jsonify({"error": "Authorization is unavailable. Try again later."}), 503
            finally:
                if cursor is not None:
                    cursor.close()
                if connection is not None:
                    connection.close()

            if (
                user is None
                or user["status"] != "active"
                or user["role"] != session_role
            ):
                session.clear()
                return jsonify(
                    {"error": "Your session is no longer valid. Please log in again."}
                ), 401

            if session_role not in allowed_roles:
                return jsonify({"error": "You do not have permission to use this endpoint."}), 403
            return view(*args, **kwargs)
        return wrapped
    return decorate
