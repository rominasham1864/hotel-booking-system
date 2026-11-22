import json
from unittest.mock import patch
from uuid import UUID

from django.db import OperationalError
from django.test import Client, SimpleTestCase, override_settings

from config.logging import JsonLogFormatter


@override_settings(DEBUG=False)
class RequestLoggingTests(SimpleTestCase):
    def test_unknown_api_path_logs_a_generated_request_id_without_raw_url(self):
        with self.assertLogs("hotel.api", level="WARNING") as logs:
            response = self.client.get(
                "/api/v1/private-path-marker/?secret=query-marker",
                HTTP_X_REQUEST_ID="client-supplied-marker",
            )

        self.assertEqual(response.status_code, 404)
        request_id = response["X-Request-ID"]
        self.assertEqual(UUID(request_id).version, 4)
        line = JsonLogFormatter().format(logs.records[0])
        entry = json.loads(line)
        self.assertEqual(entry["event"], "api.error")
        self.assertEqual(entry["method"], "GET")
        self.assertEqual(entry["status_code"], 404)
        self.assertEqual(entry["error_code"], "not_found")
        self.assertEqual(entry["request_id"], request_id)
        self.assertNotIn("marker", line)

    def test_database_error_logs_type_and_request_id_without_error_details(self):
        client = Client(raise_request_exception=False)
        with (
            patch("rooms.views.RoomViewSet.list", side_effect=OperationalError("db-secret")),
            self.assertLogs("django.request", level="ERROR") as database_logs,
            self.assertLogs("hotel.api", level="ERROR") as api_logs,
        ):
            response = client.get("/api/v1/rooms/")

        self.assertEqual(response.status_code, 500)
        request_id = response["X-Request-ID"]
        line = JsonLogFormatter().format(database_logs.records[0])
        entry = json.loads(line)
        self.assertEqual(entry["event"], "database.error")
        self.assertEqual(entry["exception_type"], "OperationalError")
        self.assertEqual(entry["request_id"], request_id)
        self.assertNotIn("db-secret", line)
        self.assertEqual(api_logs.records[0].request_id, request_id)
