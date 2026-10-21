#!/usr/bin/env python3
"""Forward only fresh predictions created after this process starts."""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


FRESHNESS_SECONDS = 10
POLL_SECONDS = 1.0
MAX_BATCH = 100
ALLOWED_ACTIVITIES = {"Idle", "Walking", "Running"}
ALLOWED_SOURCES = {"motion_heuristic", "random_forest"}
DEFAULT_DB = Path(__file__).resolve().parents[1] / "backend" / "activities.db"


def parse_utc(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value):
    parsed = parse_utc(value)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") if parsed else None


def open_readonly_database(path):
    if not path.is_file():
        raise FileNotFoundError("SQLite database does not exist: " + str(path))
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def current_high_water_marks(connection):
    prediction = connection.execute("SELECT COALESCE(MAX(id), 0) FROM activities").fetchone()[0]
    try:
        sensor = connection.execute(
            "SELECT id FROM sensor_readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError as error:
        raise RuntimeError("SQLite sensor_readings table is unavailable") from error
    return int(prediction or 0), int(sensor[0]) if sensor else 0


def latest_sensor(connection):
    row = connection.execute(
        "SELECT id, timestamp FROM sensor_readings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return (int(row["id"]), row["timestamp"]) if row else (0, None)


def activity_source_expression(connection):
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(activities)")
    }
    # The Flask app's legacy schema predates the additive source column.
    return "source" if "source" in columns else "'random_forest'"


def fresh_enough(timestamp, now):
    parsed = parse_utc(timestamp)
    if parsed is None:
        return False
    age = (now - parsed).total_seconds()
    return -5 <= age <= FRESHNESS_SECONDS


def post_prediction(api_url, token, prediction):
    request = Request(
        api_url.rstrip("/") + "/api/ingest",
        data=json.dumps(prediction, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        if response.status not in (200, 201, 202):
            raise RuntimeError("ingest endpoint returned HTTP " + str(response.status))


def validate_configuration(api_url, token):
    parsed = urlparse(api_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("ACTIVITY_API_URL must be an HTTPS Vercel origin without credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("ACTIVITY_API_URL must not contain a query or fragment.")
    if not token:
        raise ValueError("The ingestion token is required.")


def run():
    api_url = os.environ.get("ACTIVITY_API_URL", "").strip().rstrip("/")
    token = os.environ.get("ACTIVITY_INGEST_TOKEN") or getpass("Vercel ingestion token: ")
    db_path = Path(os.environ.get("ACTIVITY_DB_PATH", DEFAULT_DB)).expanduser()
    validate_configuration(api_url, token)

    connection = open_readonly_database(db_path)
    try:
        prediction_cursor, sensor_start_id = current_high_water_marks(connection)
        print("Live prediction uploader started; existing rows will be skipped.")
        print("SQLite is read-only. Waiting for new fresh classifier predictions.")
        while True:
            now = datetime.now(timezone.utc)
            sensor_id, sensor_timestamp = latest_sensor(connection)
            source_expression = activity_source_expression(connection)
            rows = connection.execute(
                "SELECT id, activity, confidence, " + source_expression + " AS source, timestamp "
                "FROM activities WHERE id > ? ORDER BY id ASC LIMIT ?",
                (prediction_cursor, MAX_BATCH),
            ).fetchall()

            for row in rows:
                prediction_id = int(row["id"])
                source = row["source"]
                sensor_time = parse_utc(sensor_timestamp)
                valid = (
                    row["activity"] in ALLOWED_ACTIVITIES
                    and source in ALLOWED_SOURCES
                    and (source != "motion_heuristic" or row["activity"] == "Idle")
                    and (source != "random_forest" or row["activity"] in {"Walking", "Running"})
                    and sensor_id > sensor_start_id
                    and sensor_time is not None
                    and fresh_enough(row["timestamp"], now)
                    and fresh_enough(sensor_timestamp, now)
                    and not (source == "motion_heuristic" and row["confidence"] is not None)
                )
                if not valid:
                    prediction_cursor = prediction_id
                    continue

                prediction = {
                    "id": prediction_id,
                    "activity": row["activity"],
                    "confidence": None if source == "motion_heuristic" else row["confidence"],
                    "source": source,
                    "timestamp": iso_utc(row["timestamp"]),
                    "sensor_timestamp": iso_utc(sensor_timestamp),
                }
                try:
                    post_prediction(api_url, token, prediction)
                except HTTPError as error:
                    print("Prediction upload failed with HTTP " + str(error.code) + ". Retrying.")
                    break
                except (URLError, TimeoutError, OSError, RuntimeError):
                    print("Prediction upload failed. Retrying when the API is reachable.")
                    break
                prediction_cursor = prediction_id
                print("Forwarded new prediction ID " + str(prediction_id) + ".")

            time.sleep(POLL_SECONDS)
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("Uploader stopped.")
    except (FileNotFoundError, ValueError, RuntimeError, sqlite3.Error) as error:
        print("Uploader could not start: " + str(error), file=sys.stderr)
        raise SystemExit(1)
