"""Focused upload-stage tests; database operations are mocked."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from flask import session as request_session

os.environ["SECRET_KEY"] = "test-secret-key-for-ai-detection-unit-tests"

from app import create_app  # noqa: E402


class FakeCursor:
    def __init__(self):
        self.rowcount = 1
        self.statement = None
        self.parameters = None
        self.fetchone_result = None
        self.fetchall_result = []
        self.closed = False
        self.executions = []

    def execute(self, statement, parameters=None):
        self.statement = statement
        self.parameters = parameters
        self.executions.append((statement, parameters))

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return self.fetchall_result

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.cursor_options = {}
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self, **kwargs):
        self.cursor_options = kwargs
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class AiDetectionUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            AI_DETECTION_UPLOAD_FOLDER=self.temp_directory.name,
            ROADGUARD_YOLO_MODEL=str(Path(self.temp_directory.name) / "missing-road-model.pt"),
        )
        self.client = self.app.test_client()
        self.connection = FakeConnection()
        self.db_patch = patch(
            "ai_detection.get_db_connection",
            return_value=self.connection,
        )
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.access_db_patch = patch(
            "access.get_db_connection",
            side_effect=self.authorization_connection,
        )
        self.access_db_patch.start()
        self.addCleanup(self.access_db_patch.stop)
        self.addCleanup(self.temp_directory.cleanup)

    @staticmethod
    def authorization_connection():
        connection = FakeConnection()
        connection.cursor_instance.fetchone_result = {
            "role": request_session.get("role"),
            "status": "active",
        }
        return connection

    def login(self, role="citizen", user_id=42):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role

    @staticmethod
    def jpeg_bytes():
        image = np.zeros((32, 48, 3), dtype=np.uint8)
        image[:, :, 1] = 127
        success, encoded = cv2.imencode(".jpg", image)
        if not success:
            raise RuntimeError("Could not generate an in-memory JPEG test image.")
        return encoded.tobytes()

    def test_valid_jpeg_is_stored_without_invented_detections(self):
        self.login()
        image_bytes = self.jpeg_bytes()
        response = self.client.post(
            "/api/ai/detect",
            data={"image": (io.BytesIO(image_bytes), "street.jpg")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 201)
        detection = response.get_json()["detection"]
        self.assertEqual(detection["detection_status"], "Model Unavailable")
        self.assertIsNone(detection["hazard_category"])
        self.assertIsNone(detection["detection_count"])
        self.assertIsNone(detection["pothole_count"])
        self.assertIsNone(detection["confidence_score"])
        self.assertIsNone(detection["bounding_boxes"])
        self.assertIsNone(detection["annotated_image_url"])
        self.assertIsNone(detection["annotated_image_path"])
        self.assertTrue(self.connection.committed)
        insert = next(
            parameters
            for statement, parameters in self.connection.cursor_instance.executions
            if "INSERT INTO ai_detections" in statement
        )
        self.assertEqual(insert[1], 42)
        saved_path = next(Path(self.temp_directory.name).iterdir())
        self.assertEqual(saved_path.read_bytes(), image_bytes)

        self.connection.cursor_instance.fetchone_result = (42, saved_path.name, None)
        image_response = self.client.get(detection["original_image_url"])
        try:
            self.assertEqual(image_response.status_code, 200)
            self.assertEqual(image_response.mimetype, "image/jpeg")
            self.assertEqual(image_response.get_data(), image_bytes)
        finally:
            image_response.close()

        self.login(user_id=99)
        denied_response = self.client.get(detection["original_image_url"])
        self.assertEqual(denied_response.status_code, 403)

    def test_inference_returns_annotated_boxes_and_requires_admin_review(self):
        self.login()

        class FakeTensor:
            def __init__(self, value):
                self.value = np.asarray(value)

            def cpu(self):
                return self

            def numpy(self):
                return self.value

        model = SimpleNamespace(
            names={0: "pothole"},
            predict=lambda image, **kwargs: [
                SimpleNamespace(
                    names={0: "pothole"},
                    boxes=SimpleNamespace(
                        xyxy=FakeTensor([[3, -4, 30, 40]]),
                        cls=FakeTensor([0]),
                        conf=FakeTensor([0.82]),
                    ),
                )
            ],
        )
        with patch("ai_detection._load_road_model", return_value=model):
            response = self.client.post(
                "/api/ai/detect",
                data={"image": (io.BytesIO(self.jpeg_bytes()), "street.jpg")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 201)
        result = response.get_json()
        detection = result["detection"]
        self.assertEqual(detection["detection_status"], "Pending Admin Review")
        self.assertEqual(detection["hazard_category"], "pothole")
        self.assertEqual(detection["detection_count"], 1)
        self.assertEqual(detection["pothole_count"], 1)
        self.assertEqual(detection["confidence_score"], 0.82)
        self.assertEqual(detection["category_counts"], {"pothole": 1})
        self.assertEqual(detection["bounding_boxes"][0]["x1"], 3)
        self.assertEqual(detection["bounding_boxes"][0]["y1"], 0)
        self.assertEqual(detection["bounding_boxes"][0]["y2"], 31)
        self.assertTrue(detection["annotated_image_url"])
        self.assertIn("not confirmed hazards", result["message"])
        self.assertTrue(
            any("UPDATE ai_detections" in sql for sql, _ in self.connection.cursor_instance.executions)
        )
        self.assertEqual(len(list(Path(self.temp_directory.name).glob("*.jpg"))), 2)

    def test_low_confidence_detections_are_not_confirmed(self):
        self.login()

        class FakeTensor:
            def __init__(self, value):
                self.value = np.asarray(value)

            def cpu(self):
                return self

            def numpy(self):
                return self.value

        model = SimpleNamespace(
            names={0: "longitudinal_crack"},
            predict=lambda image, **kwargs: [
                SimpleNamespace(
                    names={0: "longitudinal_crack"},
                    boxes=SimpleNamespace(
                        xyxy=FakeTensor([[3, 4, 30, 25]]),
                        cls=FakeTensor([0]),
                        conf=FakeTensor([0.20]),
                    ),
                )
            ],
        )
        with patch("ai_detection._load_road_model", return_value=model):
            response = self.client.post(
                "/api/ai/detect",
                data={"image": (io.BytesIO(self.jpeg_bytes()), "street.jpg")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["detection"]["detection_status"], "Low Confidence")
        update = next(
            sql
            for sql, _ in self.connection.cursor_instance.executions
            if "UPDATE ai_detections" in sql
        )
        self.assertIn("Low Confidence", update)
        self.assertIn("Pending Admin Review", update)

    def test_citizen_cannot_link_image_to_another_users_report(self):
        self.login(user_id=42)
        response = self.client.post(
            "/api/ai/detect",
            data={
                "report_id": "RG-1234567890ABCDEF",
                "image": (io.BytesIO(self.jpeg_bytes()), "street.jpg"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(list(Path(self.temp_directory.name).iterdir()), [])
        self.assertEqual(
            self.connection.cursor_instance.parameters,
            ("RG-1234567890ABCDEF", 42),
        )
        self.assertFalse(
            any("INSERT INTO ai_detections" in sql for sql, _ in self.connection.cursor_instance.executions)
        )

    def test_citizen_can_link_image_to_owned_report(self):
        self.login(user_id=42)
        report_id = "RG-1234567890ABCDEF"
        self.connection.cursor_instance.fetchone_result = (report_id,)
        response = self.client.post(
            "/api/ai/detect",
            data={
                "report_id": report_id,
                "image": (io.BytesIO(self.jpeg_bytes()), "street.jpg"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["detection"]["report_id"], report_id)
        insert = next(
            parameters
            for statement, parameters in self.connection.cursor_instance.executions
            if "INSERT INTO ai_detections" in statement
        )
        self.assertEqual(insert[2], report_id)

    def test_admin_report_list_exposes_linked_ai_results_for_review(self):
        self.login(role="admin", user_id=7)
        self.connection.cursor_instance.fetchall_result = [
            {
                "report_id": "RG-1234567890ABCDEF",
                "user_id": 42,
                "citizen_name": "Citizen",
                "hazard_category": "Pothole",
                "problem_description": "Road surface has a pothole.",
                "location_name": "Main Street",
                "latitude": 10.1,
                "longitude": 20.2,
                "image_path": None,
                "report_datetime": None,
                "priority": "Normal",
                "status": "Pending Review",
                "ai_detection_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "ai_hazard_category": "pothole",
                "ai_detection_count": 1,
                "ai_pothole_count": 1,
                "ai_confidence_score": 0.82,
                "ai_bounding_boxes": '[{"category":"pothole"}]',
                "ai_detection_status": "Pending Admin Review",
                "ai_original_image_path": "original.jpg",
                "ai_annotated_image_path": "annotated.jpg",
            }
        ]
        with patch("admin.get_db_connection", return_value=self.connection):
            response = self.client.get("/api/admin/reports")

        self.assertEqual(response.status_code, 200)
        report = response.get_json()["reports"][0]
        self.assertEqual(report["ai_detection_status"], "Pending Admin Review")
        self.assertEqual(report["ai_bounding_boxes"], [{"category": "pothole"}])
        self.assertIn("/annotated", report["ai_annotated_image_url"])
        self.assertEqual(report["status"], "Pending Review")

    def test_admin_can_review_model_result_without_approving_report(self):
        self.login(role="admin", user_id=7)
        detection_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        response = self.client.put(
            f"/api/admin/ai/detections/{detection_id}/review",
            json={"decision": "confirm", "notes": "Image reviewed."},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["detection_status"], "Confirmed")
        self.assertIn("does not approve the citizen report", response.get_json()["message"])
        update, parameters = self.connection.cursor_instance.executions[0]
        self.assertIn("UPDATE ai_detections", update)
        self.assertNotIn("UPDATE road_reports", update)
        self.assertEqual(parameters[0], "Confirmed")
        self.assertEqual(parameters[1], 7)
        self.assertEqual(parameters[-1], detection_id)
        self.assertTrue(self.connection.committed)

    def test_citizen_cannot_review_ai_detection(self):
        self.login(role="citizen", user_id=42)
        response = self.client.put(
            "/api/admin/ai/detections/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/review",
            json={"decision": "confirm"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.connection.cursor_instance.executions, [])

    def test_invalid_image_bytes_are_rejected_before_database_write(self):
        self.login()
        response = self.client.post(
            "/api/ai/detect",
            data={"image": (io.BytesIO(b"\xff\xd8\xffnot-a-decodable-jpeg"), "street.jpg")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.connection.committed)
        self.assertEqual(list(Path(self.temp_directory.name).iterdir()), [])

    def test_oversized_png_dimensions_are_rejected_before_opencv_decode(self):
        self.login()
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        success, encoded = cv2.imencode(".png", image)
        self.assertTrue(success)
        content = bytearray(encoded.tobytes())
        content[16:20] = (20_001).to_bytes(4, "big")
        content[20:24] = (20_000).to_bytes(4, "big")

        with patch("ai_detection.cv2.imdecode") as decode:
            response = self.client.post(
                "/api/ai/detect",
                data={"image": (io.BytesIO(content), "street.png")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 400)
        decode.assert_not_called()
        self.assertFalse(self.connection.committed)

    def test_file_over_five_megabytes_is_rejected(self):
        self.login()
        content = self.jpeg_bytes() + b"\x00" * (5 * 1024 * 1024)
        response = self.client.post(
            "/api/ai/detect",
            data={"image": (io.BytesIO(content), "street.jpg")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.connection.committed)
        self.assertEqual(list(Path(self.temp_directory.name).iterdir()), [])

    def test_endpoint_requires_login(self):
        response = self.client.post(
            "/api/ai/detect",
            data={"image": (io.BytesIO(self.jpeg_bytes()), "street.jpg")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 401)

    def test_driver_role_cannot_upload_ai_images(self):
        self.login(role="driver")
        response = self.client.post(
            "/api/ai/detect",
            data={"image": (io.BytesIO(self.jpeg_bytes()), "street.jpg")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 403)
