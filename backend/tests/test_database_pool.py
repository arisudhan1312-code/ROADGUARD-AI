import unittest
from unittest.mock import Mock, patch

from flask import Flask

from database import get_db_connection


class DatabasePoolTests(unittest.TestCase):
    def test_pool_is_created_once_per_application_and_reused(self):
        app = Flask(__name__)
        app.config.update(
            MYSQL_CONFIG={
                "host": "localhost",
                "port": 3306,
                "user": "root",
                "password": "test-only",
                "database": "roadguard_ai",
            },
            MYSQL_POOL_SIZE=3,
        )
        pool = Mock()
        first_connection = object()
        second_connection = object()
        pool.get_connection.side_effect = [first_connection, second_connection]

        with app.app_context(), patch(
            "database.MySQLConnectionPool",
            return_value=pool,
        ) as create_pool:
            self.assertIs(get_db_connection(), first_connection)
            self.assertIs(get_db_connection(), second_connection)

        create_pool.assert_called_once_with(
            pool_name=f"roadguard_{id(app)}",
            pool_size=3,
            pool_reset_session=True,
            **app.config["MYSQL_CONFIG"],
        )
        self.assertEqual(pool.get_connection.call_count, 2)


if __name__ == "__main__":
    unittest.main()
