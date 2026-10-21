import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


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
