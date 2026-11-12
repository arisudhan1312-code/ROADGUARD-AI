import os
import unittest
from unittest.mock import Mock, patch

os.environ["SECRET_KEY"] = "test-secret-key-for-hazard-priority-api-tests"

from app import create_app  # noqa: E402


class PriorityCursor:
    def __init__(self):
        self.fetchone_result = {
            "report_id": "RG-123",
            "status": "Pending Review",
            "location_name": "Main Road",
        }
        self.fetchall_result = [
            {
                "report_id": "RG-123",
                "hazard_category": "Other",
                "traffic_importance": "High",
                "priority_source": "Admin Override",
                "hazard_priority": "Critical",
                "severity": None,
            }
        ]
        self.executions = []

    def execute(self, query, parameters=None):
        self.executions.append((query, parameters))

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return self.fetchall_result

    def close(self):
        pass


class PriorityConnection:
    def __init__(self):
        self.cursor_instance = PriorityCursor()
        self.cursor_options = {}
        self.committed = False
        self.rolled_back = False

    def start_transaction(self):
        pass

    def cursor(self, **kwargs):
        self.cursor_options = kwargs
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class HazardPriorityApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.connection = PriorityConnection()
        self.database_patch = patch(
            "admin.get_db_connection",
            return_value=self.connection,
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.access_database_patch = patch(
            "access.get_db_connection",
            side_effect=self.authorization_connection,
        )
        self.access_database_patch.start()
        self.addCleanup(self.access_database_patch.stop)

    def authorization_connection(self):
        cursor = Mock()
        cursor.fetchone.return_value = {
            "role": self.current_role,
            "status": "active",
        }
        connection = Mock()
        connection.cursor.return_value = cursor
        return connection

    def login(self, role):
        self.current_role = role
        with self.client.session_transaction() as session:
            session["user_id"] = 42
            session["role"] = role

    def test_admin_can_save_priority_override_without_changing_report_status(self):
        self.login("admin")
        response = self.client.put(
            "/api/admin/reports/priority",
            json={
                "report_id": "RG-123",
                "priority": "Critical",
                "traffic_importance": "High",
            },
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["hazard_priority"], "Critical")
        self.assertEqual(payload["priority_source"], "Admin Override")
        self.assertTrue(self.connection.committed)
        updates = [
            execution[0]
            for execution in self.connection.cursor_instance.executions
            if execution[0].lstrip().startswith("UPDATE")
        ]
        self.assertTrue(updates)
        self.assertFalse(any("status =" in query for query in updates))

    def test_non_admin_cannot_update_priority(self):
        self.login("citizen")
        response = self.client.put(
            "/api/admin/reports/priority",
            json={"report_id": "RG-123", "priority": "Critical"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.connection.cursor_instance.executions)


if __name__ == "__main__":
    unittest.main()
