"""Flask application entry point for the ROADGUARD AI API."""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS
import mysql.connector

from auth import auth_blueprint
from admin import admin_blueprint
from ai_detection import ai_detection_blueprint
from citizen import citizen_blueprint
from database import get_db_connection
from driver import driver_blueprint
from notifications import notifications_blueprint
from push_notifications import push_blueprint
from worker import worker_blueprint


load_dotenv(Path(__file__).with_name(".env"))

REQUIRED_DATABASE_TABLES = frozenset(
    {
        "users",
        "road_reports",
        "hazards",
        "workers",
        "repair_tasks",
        "repair_evidence",
        "volunteer_activities",
        "volunteer_participants",
        "rewards",
        "notifications",
        "ai_detections",
    }
)


def create_app():
    app = Flask(__name__)

    secret_key = os.getenv("SECRET_KEY", "").strip()
    if len(secret_key) < 32 or secret_key.startswith("replace_"):
        raise RuntimeError(
            "Set SECRET_KEY in backend/.env to a random value of at least 32 characters."
        )

    app.config.update(
        SECRET_KEY=secret_key,
        MYSQL_CONFIG={
            "host": os.getenv("MYSQL_HOST", "localhost"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DATABASE", "roadguard_ai"),
            "charset": "utf8mb4",
        },
        MYSQL_POOL_SIZE=int(os.getenv("MYSQL_POOL_SIZE", "5")),
        MAX_CONTENT_LENGTH=11 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        REPORT_UPLOAD_FOLDER=os.getenv(
            "REPORT_UPLOAD_FOLDER",
            str(Path(__file__).with_name("uploads") / "reports"),
        ),
        EVIDENCE_UPLOAD_FOLDER=os.getenv(
            "EVIDENCE_UPLOAD_FOLDER",
            str(Path(__file__).with_name("uploads") / "repair-evidence"),
        ),
        AI_DETECTION_UPLOAD_FOLDER=os.getenv(
            "AI_DETECTION_UPLOAD_FOLDER",
            str(Path(__file__).with_name("detection_uploads")),
        ),
        ROADGUARD_YOLO_MODEL=os.getenv(
            "ROADGUARD_YOLO_MODEL",
            str(Path(__file__).with_name("models") / "road_damage.pt"),
        ),
        AI_DETECTION_CONFIDENCE=os.getenv("AI_DETECTION_CONFIDENCE", "0.10"),
        AI_DETECTION_REVIEW_THRESHOLD=os.getenv("AI_DETECTION_REVIEW_THRESHOLD", "0.35"),
    )

    frontend_origins = [
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_ORIGINS",
            (
                "http://127.0.0.1:5500,http://localhost:5500,"
                "http://127.0.0.1:5501,http://localhost:5501"
            ),
        ).split(",")
        if origin.strip()
    ]
    CORS(
        app,
        resources={r"/api/*": {"origins": frontend_origins}},
        supports_credentials=True,
        allow_headers=["Content-Type"],
        methods=["GET", "POST", "PUT", "OPTIONS"],
    )

    app.register_blueprint(auth_blueprint)
    app.register_blueprint(ai_detection_blueprint)
    app.register_blueprint(citizen_blueprint)
    app.register_blueprint(admin_blueprint)
    app.register_blueprint(driver_blueprint)
    app.register_blueprint(worker_blueprint)
    app.register_blueprint(notifications_blueprint)
    app.register_blueprint(push_blueprint)

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify({"error": "The uploaded request exceeds the configured image upload limit."}), 413

    @app.get("/api/health")
    @app.get("/api/health/database")
    def health_check():
        """Check MySQL connectivity and required tables without exposing credentials."""
        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            connection.ping(reconnect=False, attempts=1, delay=0)
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT TABLE_NAME
                FROM information_schema.tables
                WHERE TABLE_SCHEMA = %s
                """,
                (app.config["MYSQL_CONFIG"]["database"],),
            )
            existing_tables = {row[0] for row in cursor.fetchall()}
            missing_tables = sorted(REQUIRED_DATABASE_TABLES - existing_tables)
            schema_status = "complete" if not missing_tables else "incomplete"
            response_status = 200 if not missing_tables else 503
            return jsonify(
                {
                    "status": "ok" if not missing_tables else "error",
                    "database": "connected",
                    "schema": schema_status,
                    "required_table_count": len(REQUIRED_DATABASE_TABLES),
                    "missing_tables": missing_tables,
                }
            ), response_status
        except mysql.connector.Error:
            app.logger.exception("Database health check failed.")
            return jsonify(
                {
                    "status": "error",
                    "database": "unavailable",
                    "message": "Database is unavailable.",
                }
            ), 503
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    return app


app = create_app()


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="127.0.0.1", port=5000, debug=debug_mode)
