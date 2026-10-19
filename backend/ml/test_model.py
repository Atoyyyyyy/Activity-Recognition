
import numpy as np
import pandas as pd
import joblib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Load our trained model
model_data = joblib.load(
    BASE_DIR / "models" / "activity_model.joblib"
)

model = model_data["model"]
sensors = model_data["sensors"]
window_size = model_data["window_size"]

print("Model loaded successfully!")

# Load dataset
df = pd.read_csv(BASE_DIR / "data" / "dataset.csv")

# Select first 10 sensor readings for a smoke test
sample = df.iloc[:window_size]

values = sample[sensors].to_numpy(dtype=float)

# Use the same feature extraction as training
features = np.concatenate([
    values.mean(axis=0),
    values.std(axis=0),
    np.abs(np.diff(values, axis=0)).mean(axis=0)
]).reshape(1, -1)

# Predict activity
prediction = int(model.predict(features)[0])
probabilities = model.predict_proba(features)[0]

labels = {
    0: "Walking",
    1: "Running"
}

prediction_index = list(model.classes_).index(prediction)
confidence = float(probabilities[prediction_index]) * 100

print("\n========== PREDICTION RESULT ==========")
print("Actual Activity:",
      labels[int(sample["activity"].iloc[0])])
print("Predicted Activity:", labels[prediction])
print(f"Confidence: {confidence:.2f}%")
print("=======================================")
