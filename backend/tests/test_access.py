import unittest
from unittest.mock import patch

import mysql.connector
from flask import Flask

from access import require_role


class FakeCursor:
    def __init__(self, user):
        self.user = user
        self.statement = None
        self.parameters = None
        self.closed = False

    def execute(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters

    def fetchone(self):
        return self.user

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, user):
        self.cursor_instance = FakeCursor(user)
        self.closed = False

    def cursor(self, **kwargs):
        if kwargs != {"dictionary": True}:
            raise AssertionError("Authorization must request dictionary rows.")
        return self.cursor_instance

    def close(self):
        self.closed = True


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="access-test-secret-key-long-enough")

        @self.app.get("/citizen")
        @require_role("citizen")
        def citizen_endpoint():
            return {"ok": True}, 200

        @self.app.get("/admin")
        @require_role("admin")
        def admin_endpoint():
            return {"ok": True}, 200

        self.client = self.app.test_client()

    def login(self, role="citizen", user_id=17):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role

    @staticmethod
    def active_user(role="citizen"):
        return {"role": role, "status": "active"}

    def test_active_user_with_unchanged_role_can_access_route(self):
        self.login()
        connection = FakeConnection(self.active_user())

        with patch("access.get_db_connection", return_value=connection):
            response = self.client.get("/citizen")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(connection.cursor_instance.parameters, (17,))
        self.assertTrue(connection.cursor_instance.closed)
        self.assertTrue(connection.closed)

    def test_user_with_wrong_current_role_is_forbidden(self):
        self.login("citizen")
        connection = FakeConnection(self.active_user("citizen"))

        with patch("access.get_db_connection", return_value=connection):
            response = self.client.get("/admin")

        self.assertEqual(response.status_code, 403)

    def test_disabled_user_session_is_cleared(self):
        self.login()
        connection = FakeConnection({"role": "citizen", "status": "disabled"})

        with patch("access.get_db_connection", return_value=connection):
            response = self.client.get("/citizen")

        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_deleted_user_session_is_cleared(self):
        self.login()
        connection = FakeConnection(None)

        with patch("access.get_db_connection", return_value=connection):
            response = self.client.get("/citizen")

        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_changed_role_invalidates_existing_session(self):
        self.login("citizen")
        connection = FakeConnection(self.active_user("admin"))

        with patch("access.get_db_connection", return_value=connection):
            response = self.client.get("/citizen")

        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_database_failure_fails_closed(self):
        self.login()

        with patch(
            "access.get_db_connection",
            side_effect=mysql.connector.Error("database unavailable"),
        ):
            response = self.client.get("/citizen")

        self.assertEqual(response.status_code, 503)

    def test_anonymous_request_does_not_query_database(self):
        with patch("access.get_db_connection") as get_connection:
            response = self.client.get("/citizen")

        self.assertEqual(response.status_code, 401)
        get_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
