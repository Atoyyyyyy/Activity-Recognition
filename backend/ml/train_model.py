
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix
)

BASE_DIR = Path(__file__).resolve().parent
DATASET = BASE_DIR / "data" / "dataset.csv"
MODEL_PATH = BASE_DIR / "models" / "activity_model.joblib"

SENSORS = [
    "acceleration_x",
    "acceleration_y",
    "acceleration_z",
    "gyro_x",
    "gyro_y",
    "gyro_z"
]

WINDOW_SIZE = 10

print("Loading dataset...")
df = pd.read_csv(DATASET)

# Convert date and time into timestamps
time_text = df["time"].astype(str).str.replace(
    r":(\d+)$", r".\1", regex=True
)

df["timestamp"] = pd.to_datetime(
    df["date"].astype(str) + " " + time_text,
    format="mixed",
    errors="coerce"
)

df = df.dropna(subset=["timestamp"] + SENSORS)
df = df.sort_values(
    ["username", "wrist", "timestamp"]
).reset_index(drop=True)

# Detect separate continuous recording segments
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

# Extract features from consecutive readings
def extract_features(values):
    return np.concatenate([
        values.mean(axis=0),
        values.std(axis=0),
        np.abs(np.diff(values, axis=0)).mean(axis=0)
    ])

X, y, groups = [], [], []

for segment_id, segment in df.groupby("segment"):
    if len(segment) < WINDOW_SIZE:
        continue

    for start in range(
        0, len(segment) - WINDOW_SIZE + 1, WINDOW_SIZE
    ):
        window = segment[SENSORS].iloc[
            start:start + WINDOW_SIZE
        ].to_numpy(dtype=float)

        X.append(extract_features(window))
        y.append(int(segment["activity"].iloc[0]))
        groups.append(segment_id)

X = np.asarray(X)
y = np.asarray(y)
groups = np.asarray(groups)

print("Windows created:", len(X))
print("Features per window:", X.shape[1])

# Hold out complete recording segments
# to reduce temporal data leakage.
rng = np.random.default_rng(42)
test_groups = []

for label in [0, 1]:
    unique_groups = np.unique(groups[y == label])

    if len(unique_groups) < 2:
        raise ValueError(
            f"Not enough separate recording segments "
            f"for activity {label}. Found {len(unique_groups)}."
        )

    n_test = max(1, round(0.2 * len(unique_groups)))
    n_test = min(n_test, len(unique_groups) - 1)

    selected = rng.choice(
        unique_groups, size=n_test, replace=False
    )
    test_groups.extend(selected)

test_mask = np.isin(groups, test_groups)

X_train, X_test = X[~test_mask], X[test_mask]
y_train, y_test = y[~test_mask], y[test_mask]

print("Training windows:", len(X_train))
print("Testing windows:", len(X_test))

# Train model
print("\nTraining Random Forest model...")

model = RandomForestClassifier(
    n_estimators=200,
    min_samples_leaf=2,
    random_state=42,
    n_jobs=-1
)

model.fit(X_train, y_train)

# Evaluate
predictions = model.predict(X_test)

print("\n========== MODEL RESULTS ==========")

print("Accuracy:", accuracy_score(
    y_test, predictions
))

print("\nClassification Report:")
print(classification_report(
    y_test, predictions,
    labels=[0, 1],
    target_names=["Walking", "Running"],
    zero_division=0
))

print("\nConfusion Matrix:")
print(confusion_matrix(
    y_test, predictions, labels=[0, 1]
))

# Save model and feature configuration
MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

joblib.dump({
    "model": model,
    "sensors": SENSORS,
    "window_size": WINDOW_SIZE
}, MODEL_PATH)

print("\nModel saved to:", MODEL_PATH)
