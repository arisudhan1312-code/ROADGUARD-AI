import os
import unittest
from unittest.mock import patch

import mysql.connector

os.environ["SECRET_KEY"] = "test-secret-key-for-database-health-checks"

from app import REQUIRED_DATABASE_TABLES, create_app  # noqa: E402


class FakeCursor:
    def __init__(self, table_names):
        self.table_names = table_names
        self.statement = None
        self.parameters = None
        self.closed = False

    def execute(self, statement, parameters=None):
        self.statement = statement
        self.parameters = parameters

    def fetchall(self):
        return [(name,) for name in self.table_names]

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, table_names):
        self.cursor_instance = FakeCursor(table_names)
        self.pinged = False
        self.closed = False

    def ping(self, reconnect=False, attempts=1, delay=0):
        self.pinged = True

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


class DatabaseHealthTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def test_health_alias_reports_connected_database_and_all_required_tables(self):
        connection = FakeConnection(REQUIRED_DATABASE_TABLES)

        with patch("app.get_db_connection", return_value=connection):
            response = self.client.get("/api/health/database")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["database"], "connected")
        self.assertEqual(payload["schema"], "complete")
        self.assertEqual(payload["required_table_count"], 11)
        self.assertEqual(payload["missing_tables"], [])
        self.assertTrue(connection.pinged)
        self.assertTrue(connection.cursor_instance.closed)
        self.assertTrue(connection.closed)
        self.assertEqual(
            connection.cursor_instance.parameters,
            (self.app.config["MYSQL_CONFIG"]["database"],),
        )

    def test_existing_health_path_remains_supported(self):
        connection = FakeConnection(REQUIRED_DATABASE_TABLES)

        with patch("app.get_db_connection", return_value=connection):
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)

    def test_connected_database_with_missing_tables_reports_schema_gap(self):
        table_names = REQUIRED_DATABASE_TABLES - {"workers", "ai_detections"}
        connection = FakeConnection(table_names)

        with patch("app.get_db_connection", return_value=connection):
            response = self.client.get("/api/health/database")

        payload = response.get_json()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(payload["database"], "connected")
        self.assertEqual(payload["schema"], "incomplete")
        self.assertEqual(payload["missing_tables"], ["ai_detections", "workers"])

    def test_connection_error_does_not_expose_driver_details(self):
        with patch(
            "app.get_db_connection",
            side_effect=mysql.connector.Error(1045, "credential details"),
        ):
            response = self.client.get("/api/health/database")

        payload = response.get_json()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(payload["database"], "unavailable")
        self.assertNotIn("credential details", response.get_data(as_text=True))
        self.assertNotIn("password", payload)


if __name__ == "__main__":
    unittest.main()
