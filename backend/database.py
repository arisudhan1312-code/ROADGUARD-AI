"""MySQL connection helper used by the Flask API."""

from threading import Lock

import mysql.connector
from flask import current_app
from mysql.connector.pooling import MySQLConnectionPool


_POOL_LOCK = Lock()


def get_db_connection():
    """Return a pooled MySQL connection for the active Flask application."""
    app = current_app._get_current_object()
    pool = app.extensions.get("roadguard_mysql_pool")
    if pool is None:
        with _POOL_LOCK:
            pool = app.extensions.get("roadguard_mysql_pool")
            if pool is None:
                pool = MySQLConnectionPool(
                    pool_name=f"roadguard_{id(app)}",
                    pool_size=app.config.get("MYSQL_POOL_SIZE", 5),
                    pool_reset_session=True,
                    **app.config["MYSQL_CONFIG"],
                )
                app.extensions["roadguard_mysql_pool"] = pool
    return pool.get_connection()
