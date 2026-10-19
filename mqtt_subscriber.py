
import json
import sqlite3
from pathlib import Path

import paho.mqtt.client as mqtt

BROKER = "localhost"
PORT = 1883
TOPIC = "activity/sensors"

DB_PATH = Path(__file__).parent / "activities.db"


# Create table for raw sensor readings
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


# Called when connected to MQTT broker
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("Connected to MQTT broker!")
        client.subscribe(TOPIC)
        print(f"Subscribed to: {TOPIC}")
    else:
        print(f"Connection failed: {reason_code}")


# Called when a message is received
def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))

        values = (
            float(data["acc_x"]),
            float(data["acc_y"]),
            float(data["acc_z"]),
            float(data["gyro_x"]),
            float(data["gyro_y"]),
            float(data["gyro_z"])
        )

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO sensor_readings (
                    acc_x, acc_y, acc_z,
                    gyro_x, gyro_y, gyro_z
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, values)

        print("Sensor data received and saved:", data)

    except (ValueError, KeyError, TypeError) as error:
        print("Invalid sensor data:", error)
    except sqlite3.Error as error:
        print("Database error:", error)


if __name__ == "__main__":
    init_db()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )

    client.on_connect = on_connect
    client.on_message = on_message

    print("Connecting to MQTT broker...")
    client.connect(BROKER, PORT, 60)
    client.loop_forever()
