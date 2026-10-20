
import json
import sqlite3
from pathlib import Path
from collections import deque
from getpass import getpass

import joblib
import numpy as np
import paho.mqtt.client as mqtt


# ==========================================
# CONFIGURATION
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

DB_PATH = BASE_DIR / "activities.db"

MODEL_PATH = (
    BASE_DIR / "ml" / "models" / "activity_model.joblib"
)

# MQTT Configuration
BROKER = "127.0.0.1"
PORT = 1883
TOPIC = "activity/sensors"

MQTT_USERNAME = "rt_spark"

# Activity labels
LABELS = {
    0: "Walking",
    1: "Running"
}


# ==========================================
# LOAD MACHINE LEARNING MODEL
# ==========================================

print("Loading Machine Learning model...")

model_data = joblib.load(MODEL_PATH)

model = model_data["model"]
window_size = model_data["window_size"]

print("Machine Learning model loaded!")
print("Window size:", window_size)


# ==========================================
# SENSOR CONFIGURATION
# ==========================================

# Map dataset columns to incoming MQTT keys

sensor_keys = {
    "acceleration_x": "acc_x",
    "acceleration_y": "acc_y",
    "acceleration_z": "acc_z",
    "gyro_x": "gyro_x",
    "gyro_y": "gyro_y",
    "gyro_z": "gyro_z"
}

# Preserve the feature order used during training
sensor_order = [
    sensor_keys[name]
    for name in model_data["sensors"]
]

# Buffer stores consecutive sensor readings
sensor_buffer = deque(maxlen=window_size)


# ==========================================
# DATABASE INITIALIZATION
# ==========================================

def init_db():

    with sqlite3.connect(DB_PATH) as conn:

        # Raw sensor readings
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

        # Classified activity history
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity TEXT NOT NULL,
                confidence REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

    print("SQLite database initialized!")


# ==========================================
# FEATURE EXTRACTION
# ==========================================

def extract_features(values):

    # Mean value of each sensor axis
    mean_features = values.mean(axis=0)

    # Standard deviation of each sensor axis
    std_features = values.std(axis=0)

    # Average absolute change between readings
    diff_features = np.abs(
        np.diff(values, axis=0)
    ).mean(axis=0)

    # Combine into 18 features
    features = np.concatenate([
        mean_features,
        std_features,
        diff_features
    ])

    return features.reshape(1, -1)


# ==========================================
# ACTIVITY CLASSIFICATION
# ==========================================

def classify_activity():

    print("\nProcessing sensor readings...")

    values = np.array(
        sensor_buffer,
        dtype=float
    )

    # Generate features
    features = extract_features(values)

    # Predict activity
    prediction = int(
        model.predict(features)[0]
    )

    # Obtain prediction probabilities
    probabilities = model.predict_proba(features)[0]

    index = list(model.classes_).index(prediction)

    confidence = float(
        probabilities[index]
    ) * 100

    activity = LABELS.get(
        prediction,
        "Unknown"
    )

    # Save prediction
    with sqlite3.connect(DB_PATH) as conn:

        conn.execute("""
            INSERT INTO activities (
                activity,
                confidence
            )
            VALUES (?, ?)
        """, (
            activity,
            confidence
        ))

    print("\n========================================")
    print("       ACTIVITY RECOGNITION RESULT")
    print("========================================")

    print("Predicted Activity:", activity)
    print(f"Confidence: {confidence:.2f}%")
    print("Prediction saved to SQLite!")

    print("========================================\n")


# ==========================================
# MQTT CONNECTION CALLBACK
# ==========================================

def on_connect(
    client,
    userdata,
    flags,
    reason_code,
    properties
):

    if reason_code == 0:

        print("\nConnected to MQTT broker!")

        sensor_buffer.clear()

        client.subscribe(TOPIC)

        print("Subscribed to:", TOPIC)
        print("Waiting for sensor data...\n")

    else:

        print(
            "MQTT connection failed:",
            reason_code
        )


# ==========================================
# MQTT DISCONNECTION CALLBACK
# ==========================================

def on_disconnect(
    client,
    userdata,
    disconnect_flags,
    reason_code,
    properties
):

    sensor_buffer.clear()

    print(
        "MQTT disconnected:",
        reason_code
    )


# ==========================================
# MQTT MESSAGE CALLBACK
# ==========================================

def on_message(client, userdata, msg):

    try:

        # Decode incoming JSON payload
        payload = msg.payload.decode("utf-8")

        data = json.loads(payload)

        # Extract six sensor values
        reading = [
            float(data[key])
            for key in sensor_order
        ]

        # Validate sensor values
        if not np.all(np.isfinite(reading)):
            raise ValueError(
                "Invalid sensor value received"
            )

        # Save raw sensor readings
        with sqlite3.connect(DB_PATH) as conn:

            conn.execute("""
                INSERT INTO sensor_readings (
                    acc_x,
                    acc_y,
                    acc_z,
                    gyro_x,
                    gyro_y,
                    gyro_z
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                float(data["acc_x"]),
                float(data["acc_y"]),
                float(data["acc_z"]),
                float(data["gyro_x"]),
                float(data["gyro_y"]),
                float(data["gyro_z"])
            ))

        # Add reading to sensor buffer
        sensor_buffer.append(reading)

        print(
            f"Sensor reading received: "
            f"{len(sensor_buffer)}/{window_size}"
        )

        # Classify once buffer is full
        if len(sensor_buffer) == window_size:

            try:
                classify_activity()
            finally:
                sensor_buffer.clear()

    except (
        ValueError,
        KeyError,
        TypeError,
        UnicodeDecodeError
    ) as error:

        print("Invalid sensor data:", error)

    except sqlite3.Error as error:

        print("Database error:", error)

    except Exception as error:

        print("Classification error:", error)


# ==========================================
# MAIN PROGRAM
# ==========================================

if __name__ == "__main__":

    print("\n========================================")
    print(" ACTIVITY RECOGNITION - ACEY EDITION")
    print("========================================")

    # Initialize database
    init_db()

    # Request MQTT password securely
    mqtt_password = getpass(
        "Enter MQTT password for rt_spark: "
    )

    if not mqtt_password:
        raise ValueError(
            "MQTT password cannot be empty."
        )

    # Create MQTT client
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )

    # Configure MQTT authentication
    client.username_pw_set(
        MQTT_USERNAME,
        mqtt_password
    )

    # Register callbacks
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    print("\nConnecting to MQTT broker...")
    print("Broker:", BROKER)
    print("Port:", PORT)
    print("Topic:", TOPIC)

    try:

        client.connect(
            BROKER,
            PORT,
            keepalive=60
        )

        # Continuously receive MQTT messages
        client.loop_forever()

    except KeyboardInterrupt:

        print("\nStopping MQTT subscriber...")

        client.disconnect()

    except OSError as error:

        print("MQTT network error:", error)
