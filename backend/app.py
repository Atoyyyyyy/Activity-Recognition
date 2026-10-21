
from flask import (
    Flask,
    jsonify,
    render_template,
    send_from_directory,
    make_response
)
from datetime import datetime, timezone
import os
import sqlite3
from contextlib import closing
from pathlib import Path

# ========================================
# APPLICATION CONFIGURATION
# ========================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ACTIVITY_DB_PATH", BASE_DIR / "activities.db")).resolve()
SENSOR_STALE_AFTER_SECONDS = 10

app = Flask(__name__)


# ========================================
# DATABASE INITIALIZATION
# ========================================

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity TEXT NOT NULL,
                confidence REAL,
                source TEXT NOT NULL DEFAULT 'random_forest',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(activities)")}
        if "source" not in columns:
            conn.execute(
                "ALTER TABLE activities ADD COLUMN source TEXT NOT NULL DEFAULT 'random_forest'"
            )
        conn.commit()


init_db()


# ========================================
# DASHBOARD ROUTE
# ========================================

@app.route("/")
def dashboard():
    return render_template("index.html")


# ========================================
# API HEALTH CHECK
# ========================================

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "message": "Activity Recognition API is running"
    })


@app.route("/api/status", methods=["GET"])
def system_status():
    """Report sensor-stream freshness separately from Flask reachability."""
    latest_sensor = None
    latest_prediction = None
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "sensor_readings" in tables:
            row = conn.execute(
                "SELECT timestamp FROM sensor_readings ORDER BY id DESC LIMIT 1"
            ).fetchone()
            latest_sensor = row["timestamp"] if row else None
        if "activities" in tables:
            row = conn.execute(
                "SELECT id, activity, confidence, source, timestamp FROM activities ORDER BY id DESC LIMIT 1"
            ).fetchone()
            latest_prediction = dict(row) if row else None

    age_seconds = None
    if latest_sensor:
        try:
            received_at = datetime.fromisoformat(latest_sensor.replace(" ", "T")).replace(
                tzinfo=timezone.utc
            )
            age_seconds = max(0, (datetime.now(timezone.utc) - received_at).total_seconds())
        except ValueError:
            pass

    if latest_sensor is None:
        stream_state = "waiting"
    elif age_seconds is None or age_seconds > SENSOR_STALE_AFTER_SECONDS:
        stream_state = "stale"
    else:
        stream_state = "live"

    response = jsonify({
        "api": "ok",
        "stream_state": stream_state,
        "sensor_stale_after_seconds": SENSOR_STALE_AFTER_SECONDS,
        "last_sensor_timestamp": latest_sensor,
        "sensor_age_seconds": age_seconds,
        "latest_prediction": latest_prediction,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


# ========================================
# ACTIVITY HISTORY API
# ========================================

@app.route("/api/activities", methods=["GET"])
def get_activities():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row

        records = conn.execute("""
            SELECT
                id,
                activity,
                confidence,
                source,
                timestamp
            FROM activities
            ORDER BY id DESC
            LIMIT 50
        """).fetchall()

    response = jsonify([dict(row) for row in records])
    response.headers["Cache-Control"] = "no-store"
    return response


# ========================================
# PWA SERVICE WORKER ROUTE
# ========================================

@app.route("/service-worker.js")
def service_worker():
    response = make_response(
        send_from_directory(
            app.static_folder,
            "service-worker.js",
            mimetype="application/javascript"
        )
    )

    response.headers["Cache-Control"] = "no-cache"
    return response


# ========================================
# RUN FLASK
# ========================================

if __name__ == "__main__":
    print("======================================")
    print(" Activity Recognition - Acey Edition")
    print("======================================")
    print("Dashboard: http://127.0.0.1:5000/")
    print("API: http://127.0.0.1:5000/api/activities")

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
