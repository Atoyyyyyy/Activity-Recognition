
import json
import time
import threading
from pathlib import Path

import pandas as pd
import paho.mqtt.client as mqtt

# ==========================================
# CONFIGURATION
# ==========================================

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "data" / "dataset.csv"

BROKER = "localhost"
PORT = 1883
TOPIC = "activity/sensors"

WINDOW_SIZE = 10

LABELS = {
    0: "Walking",
    1: "Running"
}

# ==========================================
# LOAD DATASET
# ==========================================

print("Loading dataset...")

df = pd.read_csv(DATASET_PATH)

# Convert date and time to timestamps
time_text = df["time"].astype(str).str.replace(
    r":(\d+)$", r".\1", regex=True
)

df["timestamp"] = pd.to_datetime(
    df["date"].astype(str) + " " + time_text,
    format="mixed",
    errors="coerce"
)

df = df.dropna(subset=["timestamp"])

# Sort readings chronologically
df = df.sort_values(
    ["username", "wrist", "timestamp"]
).reset_index(drop=True)

# ==========================================
# SELECT RUNNING DATA
# ==========================================

# Identify continuous recording segments
gap = df.groupby(["username", "wrist"])[
    "timestamp"
].diff().dt.total_seconds()

new_segment = (
    df["username"].ne(df["username"].shift())
    | df["wrist"].ne(df["wrist"].shift())
    | df["activity"].ne(df["activity"].shift())
    | gap.isna()
    | (gap > 1.0)
    | (gap <= 0)
)

df["segment"] = new_segment.cumsum()

# Find 10 consecutive Running readings
running_samples = None

for _, segment in df.groupby("segment", sort=False):
    if (
        segment["activity"].iloc[0] == 1
        and len(segment) >= WINDOW_SIZE
    ):
        running_samples = segment.head(WINDOW_SIZE).copy()
        break

if running_samples is None:
    raise ValueError(
        "No suitable Running sequence found in dataset."
    )

print("\n========== TEST CONFIGURATION ==========")
print("Expected Activity: Running")
print("Number of Samples:", len(running_samples))
print("MQTT Broker:", BROKER)
print("MQTT Topic:", TOPIC)
print("========================================\n")

# ==========================================
# CONNECT TO MQTT BROKER
# ==========================================

connected = threading.Event()

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("Connected to MQTT broker!")
        connected.set()
    else:
        print("MQTT connection failed:", reason_code)

client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2
)

client.on_connect = on_connect

client.connect(BROKER, PORT, 60)
client.loop_start()

try:
    if not connected.wait(timeout=10):
        raise ConnectionError(
            "Could not connect to MQTT broker."
        )

    # ==========================================
    # PUBLISH RUNNING SENSOR READINGS
    # ==========================================

    print("\nPublishing Running sensor readings...\n")

    for i, (_, row) in enumerate(
        running_samples.iterrows(), start=1
    ):

        sensor_data = {
            "acc_x": float(row["acceleration_x"]),
            "acc_y": float(row["acceleration_y"]),
            "acc_z": float(row["acceleration_z"]),
            "gyro_x": float(row["gyro_x"]),
            "gyro_y": float(row["gyro_y"]),
            "gyro_z": float(row["gyro_z"])
        }

        payload = json.dumps(sensor_data)

        result = client.publish(
            TOPIC,
            payload,
            qos=1
        )

        result.wait_for_publish(timeout=10)

        if not result.is_published():
            raise RuntimeError(
                f"Reading {i} was not published."
            )

        print(f"Published Running reading {i}/{WINDOW_SIZE}")

        if i < WINDOW_SIZE:
            time.sleep(0.2)

    print("\n========================================")
    print("All Running sensor readings published!")
    print("Expected Prediction: Running")
    print("========================================")

finally:
    client.disconnect()
    client.loop_stop()
