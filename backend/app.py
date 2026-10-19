
from flask import Flask, jsonify
import sqlite3
from pathlib import Path

app = Flask(__name__)

DB_PATH = Path(__file__).parent / "activities.db"

# Initialize database
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

# Health check endpoint
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "message": "Activity Recognition API is running"
    })

# Retrieve activity records
@app.route("/api/activities", methods=["GET"])
def get_activities():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        records = conn.execute("""
            SELECT * FROM activities
            ORDER BY id DESC LIMIT 50
        """).fetchall()

    return jsonify([dict(row) for row in records])

if __name__ == "__main__":
    app.run(debug=True, port=5000)
