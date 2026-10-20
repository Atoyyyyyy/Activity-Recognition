
from flask import (
    Flask,
    jsonify,
    render_template,
    send_from_directory,
    make_response
)
import sqlite3
from pathlib import Path

# ========================================
# APPLICATION CONFIGURATION
# ========================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "activities.db"

app = Flask(__name__)


# ========================================
# DATABASE INITIALIZATION
# ========================================

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity TEXT NOT NULL,
                confidence REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)


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


# ========================================
# ACTIVITY HISTORY API
# ========================================

@app.route("/api/activities", methods=["GET"])
def get_activities():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        records = conn.execute("""
            SELECT
                id,
                activity,
                confidence,
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
        host="127.0.0.1",
        port=5000,
        debug=True
    )
