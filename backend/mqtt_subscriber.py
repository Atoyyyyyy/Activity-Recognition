
import json
import sqlite3
from pathlib import Path
from collections import deque

import joblib
import numpy as np
import paho.mqtt.client as mqtt

BASE_DIR = Path(__file__).resolve().parent

DB_PATH = BASE_DIR / "activities.db"
MODEL_PATH = BASE_DIR / "ml" / "models" / "activity_model.joblib"

BROKER = "localhost"
PORT = 1883
TOPIC = "activity/sensors"

LABELS = {
    0: "Walking",
    1: "Running"
}

# Load trained model
model_data = joblib.load(MODEL_PATH)
model = model_data["model"]
window_size = model_data["window_size"]

# Match the feature order used during training
sensor_keys = {
    "acceleration_x": "acc_x",
    "acceleration_y": "acc_y",
    "acceleration_z": "acc_z",
    "gyro_x": "gyro_x",
    "gyro_y": "gyro_y",
    "gyro_z": "gyro_z"
}

sensor_order = [
    sensor_keys[name]
    for name in model_data["sensors"]
]

# Store a window of incoming sensor readings
sensor_buffer = deque(maxlen=window_size)


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                acc_x REAL,
                acc_y REAL,
                acc_z REAL,
                gyro_x REAL,
                gyro_y REAL,
                gyro_z REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity TEXT NOT NULL,
                confidence REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)


def extract_features(values):
    return np.concatenate([
        values.mean(axis=0),
        values.std(axis=0),
        np.abs(np.diff(values, axis=0)).mean(axis=0)
    ]).reshape(1, -1)


def classify_activity():
    values = np.array(sensor_buffer, dtype=float)

    features = extract_features(values)

    prediction = int(model.predict(features)[0])

    probabilities = model.predict_proba(features)[0]

    index = list(model.classes_).index(prediction)

    confidence = float(probabilities[index]) * 100

    activity = LABELS[prediction]

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO activities (activity, confidence)
            VALUES (?, ?)
        """, (activity, confidence))

    print("\n========== ACTIVITY PREDICTION ==========")
    print("Predicted Activity:", activity)
    print(f"Confidence: {confidence:.2f}%")
    print("Saved to SQLite database!")
    print("========================================\n")


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        sensor_buffer.clear()
        print("Connected to MQTT broker!")
        client.subscribe(TOPIC)
        print("Subscribed to:", TOPIC)
    else:
        print("MQTT connection failed:", reason_code)


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))

        reading = [
            float(data[key])
            for key in sensor_order
        ]

        if not np.all(np.isfinite(reading)):
            raise ValueError("Non-finite sensor value")

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO sensor_readings (
                    acc_x, acc_y, acc_z,
                    gyro_x, gyro_y, gyro_z
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, tuple(float(data[key]) for key in [
                "acc_x", "acc_y", "acc_z",
                "gyro_x", "gyro_y", "gyro_z"
            ]))

        sensor_buffer.append(reading)

        print(
            f"Sensor reading received: "
            f"{len(sensor_buffer)}/{window_size}"
        )

        if len(sensor_buffer) == window_size:
            classify_activity()
            sensor_buffer.clear()

    except (ValueError, KeyError, TypeError) as error:
        print("Invalid sensor data:", error)
    except sqlite3.Error as error:
        print("Database error:", error)


if __name__ == "__main__":
    init_db()

    print("Machine Learning model loaded!")
    print("Window size:", window_size)

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )

    client.on_connect = on_connect
    client.on_message = on_message

    print("Connecting to MQTT broker...")

    client.connect(BROKER, PORT, 60)
    client.loop_forever()
