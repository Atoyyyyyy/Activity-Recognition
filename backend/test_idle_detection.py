"""Unit and Step 89 data checks for the experimental Idle motion gate."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import numpy as np

from idle_detector import IdleThresholds, is_idle_candidate, measure_motion


@contextmanager
def database_connection(database, *, uri=False):
    connection = sqlite3.connect(database, uri=uri)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class IdleDetectorTests(unittest.TestCase):
    def setUp(self):
        self.thresholds = IdleThresholds()

    def test_stationary_fixture_with_gravity_is_idle_candidate(self):
        # Clearly labeled synthetic fixture: gravity is present, with small sensor noise.
        rng = np.random.default_rng(93)
        window = np.column_stack((
            rng.normal(0.0, 0.001, 10),
            rng.normal(0.0, 0.001, 10),
            rng.normal(1.0, 0.001, 10),
            rng.normal(0.4, 0.04, 10),
            rng.normal(-0.7, 0.04, 10),
            rng.normal(0.2, 0.04, 10),
        ))
        self.assertTrue(is_idle_candidate(window, self.thresholds))
        self.assertAlmostEqual(
            measure_motion(window).accel_max_axis_std_g,
            np.max(np.std(window[:, :3], axis=0)),
            places=12,
        )

    def test_synthetic_axis_motion_routes_away_from_idle(self):
        # Clearly labeled synthetic edge fixture: strong acceleration changes.
        window = np.zeros((10, 6), dtype=float)
        window[:, 2] = 1.0
        window[::2, 0] = 0.2
        self.assertFalse(is_idle_candidate(window, self.thresholds))

    def test_synthetic_gyro_motion_routes_away_from_idle(self):
        # Clearly labeled synthetic edge fixture: a rotating sensor.
        window = np.zeros((10, 6), dtype=float)
        window[:, 2] = 1.0
        window[:, 3] = np.linspace(-2.0, 2.0, 10)
        self.assertFalse(is_idle_candidate(window, self.thresholds))

    def test_constant_gravity_does_not_count_as_motion(self):
        window = np.zeros((10, 6), dtype=float)
        window[:, 2] = 1.0
        self.assertEqual(measure_motion(window).accel_max_axis_std_g, 0.0)
        self.assertTrue(is_idle_candidate(window, self.thresholds))

    def test_invalid_window_is_rejected(self):
        with self.assertRaises(ValueError):
            is_idle_candidate(np.zeros((10, 5)), self.thresholds)

    def test_step89_actual_stationary_windows_are_plausible_candidates(self):
        # Read-only source data from Step 89; never copies or modifies the source database.
        temp = Path(os.environ.get("TEMP", tempfile.gettempdir()))
        database = temp / "ActivityRecognitionLab_Step78_20261021_084701.sqlite"
        if not database.exists():
            self.skipTest("Step 89 isolated hardware database is unavailable")
        uri = database.resolve().as_uri() + "?mode=ro"
        with database_connection(uri, uri=True) as connection:
            values = np.asarray(connection.execute(
                """SELECT acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z
                   FROM sensor_readings
                   WHERE timestamp >= ? AND timestamp <= ? ORDER BY id""",
                ("2026-10-21 02:39:19", "2026-10-21 02:40:14"),
            ).fetchall(), dtype=float)
        windows = [values[start:start + 10] for start in range(max(0, len(values) - 9))]
        candidates = sum(is_idle_candidate(window, self.thresholds) for window in windows)
        self.assertGreater(len(windows), 200)
        self.assertGreater(candidates, 0)
        print(f"Step 89 stationary windows: {candidates}/{len(windows)} plausible Idle candidates")

    def test_step87_actual_stationary_windows_are_plausible_candidates(self):
        # Step 87 had an irregularly delivered but user-confirmed stationary interval.
        temp = Path(os.environ.get("TEMP", tempfile.gettempdir()))
        database = temp / "ActivityRecognitionLab_Step78_20261021_084701.sqlite"
        if not database.exists():
            self.skipTest("Step 87 isolated hardware database is unavailable")
        uri = database.resolve().as_uri() + "?mode=ro"
        with database_connection(uri, uri=True) as connection:
            values = np.asarray(connection.execute(
                """SELECT acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z
                   FROM sensor_readings
                   WHERE timestamp >= ? AND timestamp <= ? ORDER BY id""",
                ("2026-10-21 02:18:21", "2026-10-21 02:18:49"),
            ).fetchall(), dtype=float)
        windows = [values[start:start + 10] for start in range(max(0, len(values) - 9))]
        candidates = sum(is_idle_candidate(window, self.thresholds) for window in windows)
        self.assertEqual(len(values), 43)
        self.assertEqual(len(windows), 34)
        self.assertGreater(candidates, 0)
        print(f"Step 87 stationary windows: {candidates}/{len(windows)} plausible Idle candidates")


class PipelineAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "isolated.sqlite"
        self.env = patch.dict(os.environ, {"ACTIVITY_DB_PATH": str(self.database), "ACTIVITY_IDLE_ENABLED": "0"})
        self.env.start()
        import app as flask_app
        import mqtt_subscriber as subscriber
        self.flask_app = flask_app
        self.subscriber = subscriber
        self.flask_app.DB_PATH = self.database
        self.subscriber.DB_PATH = self.database
        self.flask_app.init_db()
        self.subscriber.init_db()
        self.client = self.flask_app.app.test_client()

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    def test_legacy_activity_schema_migrates_with_random_forest_source(self):
        with database_connection(self.database) as connection:
            connection.execute("DROP TABLE activities")
            connection.execute("CREATE TABLE activities (id INTEGER PRIMARY KEY, activity TEXT, confidence REAL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
            connection.execute("INSERT INTO activities (activity, confidence) VALUES ('Walking', 82.5)")
        self.flask_app.init_db()
        record = self.client.get("/api/activities").get_json()[0]
        self.assertEqual(record["activity"], "Walking")
        self.assertEqual(record["source"], "random_forest")

    def test_three_labels_and_prediction_source_are_returned_by_api(self):
        with database_connection(self.database) as connection:
            for label, source, confidence in (
                ("Idle", "motion_heuristic", None),
                ("Walking", "random_forest", 80.0),
                ("Running", "random_forest", 90.0),
            ):
                connection.execute(
                    "INSERT INTO activities(activity, confidence, source) VALUES (?, ?, ?)",
                    (label, confidence, source),
                )
        records = self.client.get("/api/activities").get_json()
        self.assertEqual({record["activity"] for record in records}, {"Idle", "Walking", "Running"})
        idle = next(record for record in records if record["activity"] == "Idle")
        self.assertIsNone(idle["confidence"])
        self.assertEqual(idle["source"], "motion_heuristic")

    def test_missing_and_stale_sensor_data_are_not_idle(self):
        with database_connection(self.database) as connection:
            connection.execute("INSERT INTO activities(activity, confidence) VALUES ('Idle', NULL)")
        waiting = self.client.get("/api/status").get_json()
        self.assertEqual(waiting["stream_state"], "waiting")
        with database_connection(self.database) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY, acc_x REAL, acc_y REAL, acc_z REAL,
                gyro_x REAL, gyro_y REAL, gyro_z REAL, timestamp DATETIME
            )""")
            connection.execute("INSERT INTO sensor_readings(timestamp) VALUES ('2000-01-01 00:00:00')")
        stale = self.client.get("/api/status").get_json()
        self.assertEqual(stale["stream_state"], "stale")
        template = Path(self.flask_app.app.template_folder) / "index.html"
        html = template.read_text(encoding="utf-8")
        self.assertIn('systemStatus.stream_state !== "live"', html)
        self.assertIn('systemStatus.stream_state === "stale" ? "Stale" : "Waiting..."', html)

    def test_pipeline_keeps_rf_default_and_uses_heuristic_without_confidence_when_enabled(self):
        subscriber = self.subscriber
        window = np.zeros((10, 6), dtype=float)
        window[:, 2] = 1.0

        class FakeModel:
            classes_ = np.array([0, 1])

            def __init__(self):
                self.calls = 0

            def predict(self, features):
                self.calls += 1
                return np.array([0])

            def predict_proba(self, features):
                return np.array([[0.8, 0.2]])

        fake = FakeModel()
        original = subscriber.model
        subscriber.model = fake
        try:
            subscriber.classify_activity(window)
            self.assertEqual(fake.calls, 1)
            with patch.dict(os.environ, {"ACTIVITY_IDLE_ENABLED": "1"}):
                subscriber.classify_activity(window)
            self.assertEqual(fake.calls, 1)
        finally:
            subscriber.model = original
        with database_connection(self.database) as connection:
            records = connection.execute(
                "SELECT activity, confidence, source FROM activities ORDER BY id"
            ).fetchall()
        self.assertEqual(records[0], ("Walking", 80.0, "random_forest"))
        self.assertEqual(records[1], ("Idle", None, "motion_heuristic"))

    def test_dashboard_has_three_activity_icons_and_three_second_poll(self):
        html = (Path(self.flask_app.app.template_folder) / "index.html").read_text(encoding="utf-8")
        for label in ("Idle", "Walking", "Running"):
            self.assertIn(label, html)
        self.assertIn('Idle: "⏸️"', html)
        self.assertIn('Walking: "👣"', html)
        self.assertIn('Running: "🏃"', html)
        self.assertIn("setInterval(loadActivityData, 3000)", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
