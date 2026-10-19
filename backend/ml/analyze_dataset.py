
import pandas as pd
from pathlib import Path

# Dataset location
DATASET_PATH = Path(__file__).parent / "data" / "dataset.csv"

# Load dataset
df = pd.read_csv(DATASET_PATH)

print("\n========== DATASET ANALYSIS ==========")

# 1. Dataset dimensions
print("\n[1] DATASET SHAPE")
print("Rows:", df.shape[0])
print("Columns:", df.shape[1])

# 2. Column names
print("\n[2] COLUMN NAMES")
print(df.columns.tolist())

# 3. First five records
print("\n[3] FIRST FIVE RECORDS")
print(df.head().to_string())

# 4. Data types
print("\n[4] DATA TYPES")
print(df.dtypes)

# 5. Missing values
print("\n[5] MISSING VALUES")
print(df.isnull().sum())

# 6. Activity labels
print("\n[6] ACTIVITY LABELS")
if "activity" in df.columns:
    print(df["activity"].value_counts(dropna=False))
else:
    print("Activity column not found.")

# 7. Sensor statistics
print("\n[7] NUMERICAL STATISTICS")
print(df.describe().to_string())

print("\n========== ANALYSIS COMPLETE ==========")
