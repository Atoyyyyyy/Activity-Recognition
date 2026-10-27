import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from email.message import Message
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "upload_live_predictions.py"
SPEC = importlib.util.spec_from_file_location("upload_live_predictions", SCRIPT)
UPLOADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPLOADER)


class LiveUploaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "isolated.sqlite"
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript("""
                create table sensor_readings (
                    id integer primary key,
                    timestamp text not null
                );
                create table activities (
                    id integer primary key,
                    activity text not null,
                    confidence real,
                    source text not null,
                    timestamp text not null
                );
                insert into sensor_readings values (8, '2026-01-01 00:00:00');
                insert into activities values (21, 'Walking', 75.0, 'random_forest', '2026-01-01 00:00:00');
            """)
        finally:
            connection.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_startup_high_water_marks_skip_existing_rows(self):
        connection = UPLOADER.open_readonly_database(self.database)
        try:
            prediction_id, sensor_id = UPLOADER.current_high_water_marks(connection)
            self.assertEqual((prediction_id, sensor_id), (21, 8))
            rows = connection.execute(
                "select id from activities where id > ?", (prediction_id,)
            ).fetchall()
            self.assertEqual(rows, [])
        finally:
            connection.close()

    def test_database_connection_is_read_only(self):
        connection = UPLOADER.open_readonly_database(self.database)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("insert into activities values (22, 'Running', 80, 'random_forest', 'now')")
        finally:
            connection.close()

    def test_only_fresh_prediction_and_sensor_timestamps_pass(self):
        now = datetime.now(timezone.utc)
        fresh = (now - timedelta(seconds=2)).isoformat()
        stale = (now - timedelta(seconds=20)).isoformat()
        self.assertTrue(UPLOADER.fresh_enough(fresh, now))
        self.assertFalse(UPLOADER.fresh_enough(stale, now))
        self.assertFalse(UPLOADER.fresh_enough(None, now))

    def test_configuration_accepts_bearer_token_syntax_without_prefix(self):
        UPLOADER.validate_configuration(
            "https://activity-recognition-two.vercel.app", "aBcd_123.-~+/=="
        )

    def test_configuration_rejects_prefixed_or_malformed_token_without_echoing_it(self):
        for token in ("Bearer secret-token", "secret token", "secret\r\ntoken", "bad=padding"):
            with self.subTest(token_type="prefixed" if token.lower().startswith("bearer ") else "invalid characters"):
                with self.assertRaises(ValueError) as caught:
                    UPLOADER.validate_configuration(
                        "https://activity-recognition-two.vercel.app", token
                    )
                self.assertNotIn(token, str(caught.exception))

    def test_safe_token_diagnostic_reports_shape_without_contents(self):
        token = "Bearer private-secret\n"
        diagnostic = UPLOADER.safe_token_diagnostic(token)
        self.assertIn("token_length=" + str(len(token)), diagnostic)
        self.assertIn("bearer_prefix=yes", diagnostic)
        self.assertIn("allowed_bearer_format=no", diagnostic)
        self.assertIn("unexpected_characters=2", diagnostic)
        self.assertIn("edge_whitespace=yes", diagnostic)
        self.assertNotIn("private-secret", diagnostic)

    def test_observed_idle_prediction_builds_contract_payload(self):
        row = {
            "id": 3788,
            "activity": "Idle",
            "confidence": None,
            "source": "motion_heuristic",
            "timestamp": "2026-10-27 04:39:21",
        }
        payload = UPLOADER.build_prediction(row, "2026-10-27 04:39:22")
        self.assertEqual(payload, {
            "id": 3788,
            "activity": "Idle",
            "confidence": None,
            "source": "motion_heuristic",
            "timestamp": "2026-10-27T04:39:21.000Z",
            "sensor_timestamp": "2026-10-27T04:39:22.000Z",
        })

    def test_http_error_summary_only_emits_allowlisted_code_and_fields(self):
        headers = Message()
        headers["Content-Type"] = "application/json; charset=utf-8"
        headers["x-vercel-id"] = "sfo1::abc123-1700000000000-0123456789ab"
        headers["x-vercel-error"] = "FUNCTION_INVOCATION_FAILED"
        headers["Set-Cookie"] = "private-cookie=must-not-log"
        body = json.dumps({
            "error": "invalid_prediction",
            "fields": ["activity", "confidence", "Authorization", "private-token"],
            "message": "private-token must never appear",
        }).encode()
        headers["Content-Length"] = str(len(body))
        response = HTTPError(
            "https://example.vercel.app/api/ingest?token=private-query",
            400,
            "Bad Request",
            hdrs=headers,
            fp=io.BytesIO(body),
        )
        summary = UPLOADER.summarize_http_error(
            response, "https://example.vercel.app/api/ingest?token=private-query"
        )
        self.assertIn("method=POST", summary)
        self.assertIn("url=https://example.vercel.app/api/ingest", summary)
        self.assertIn("status=400", summary)
        self.assertIn("content_type=application/json", summary)
        self.assertIn("response_length=" + str(len(body)), summary)
        self.assertIn("body_type=json", summary)
        self.assertIn("api_error_code=invalid_prediction", summary)
        self.assertIn("x_vercel_id=sfo1::abc123-1700000000000-0123456789ab", summary)
        self.assertIn("x_vercel_error=FUNCTION_INVOCATION_FAILED", summary)
        self.assertIn("fields=activity,confidence", summary)
        self.assertNotIn("private-token", summary)
        self.assertNotIn("private-query", summary)
        self.assertNotIn("private-cookie", summary)

    def test_http_error_body_classification_covers_html_text_and_empty(self):
        for body, content_type, expected in (
            (b"<!doctype html><title>Error</title>", "text/html", "body_type=html"),
            (b"Bad Request", "text/plain", "body_type=plain_text"),
            (b"", "application/json", "body_type=empty"),
        ):
            headers = Message()
            headers["Content-Type"] = content_type
            response = HTTPError(
                "https://example.vercel.app/api/ingest", 400, "Bad Request",
                hdrs=headers, fp=io.BytesIO(body)
            )
            summary = UPLOADER.summarize_http_error(
                response, "https://example.vercel.app/api/ingest"
            )
            self.assertIn(expected, summary)
            self.assertNotIn("Bad Request", summary)

    def test_legacy_activity_schema_defaults_source_without_migration(self):
        legacy_database = Path(self.temp.name) / "legacy.sqlite"
        connection = sqlite3.connect(legacy_database)
        connection.execute("create table activities (id integer primary key, activity text, confidence real, timestamp text)")
        connection.close()
        connection = UPLOADER.open_readonly_database(legacy_database)
        try:
            self.assertEqual(
                UPLOADER.activity_source_expression(connection), "'random_forest'"
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
