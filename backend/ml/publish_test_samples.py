
import json
import time
from pathlib import Path

import pandas as pd
import paho.mqtt.client as mqtt

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "data" / "dataset.csv"

BROKER = "localhost"
PORT = 1883
TOPIC = "activity/sensors"

# Load the first 10 readings
df = pd.read_csv(DATASET_PATH).head(10)

if len(df) != 10 or df["activity"].nunique() != 1:
    raise ValueError(
        "Expected 10 readings from one activity."
    )

labels = {0: "Walking", 1: "Running"}

expected_activity = labels[int(df["activity"].iloc[0])]

client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2
)

client.connect(BROKER, PORT, 60)
client.loop_start()

print("Connected to MQTT broker!")
print("Expected activity:", expected_activity)
print("Publishing 10 sensor readings...\n")

try:
    for i, (_, row) in enumerate(df.iterrows(), start=1):

        data = {
            "acc_x": float(row["acceleration_x"]),
            "acc_y": float(row["acceleration_y"]),
            "acc_z": float(row["acceleration_z"]),
            "gyro_x": float(row["gyro_x"]),
            "gyro_y": float(row["gyro_y"]),
            "gyro_z": float(row["gyro_z"])
        }

        payload = json.dumps(data)

        result = client.publish(
            TOPIC,
            payload,
            qos=1
        )

        result.wait_for_publish()

        print(f"Published reading {i}/10")

        # Approximate dataset sampling interval
        time.sleep(0.2)

finally:
    client.disconnect()
    client.loop_stop()

print("\nAll sensor readings published!")
