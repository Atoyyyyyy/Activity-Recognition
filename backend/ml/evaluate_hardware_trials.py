"""Evaluate time-labeled RT-Spark predictions without changing the model or DB."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


CLASSES = ("Walking", "Running")
GROUND_TRUTH = {"stationary": "Stationary", "walking": "Walking", "running": "Running"}


@dataclass(frozen=True)
class Trial:
    trial_id: str
    start: datetime
    end: datetime
    label: str

    def scored_bounds(self, guard: timedelta) -> tuple[datetime, datetime]:
        return self.start + guard, self.end - guard


def parse_utc(value: str, description: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid {description} timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{description} must include a UTC offset or Z: {value!r}")
    return parsed.astimezone(timezone.utc)


def parse_sqlite_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace(" ", "T"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_trials(path: Path, guard: timedelta) -> list[Trial]:
    trials: list[Trial] = []
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {"trial_id", "start_utc", "end_utc", "label"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(
                "Labels CSV must include trial_id,start_utc,end_utc,label columns."
            )
        for row_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            label_key = (row.get("label") or "").strip().casefold()
            if label_key not in GROUND_TRUTH:
                raise ValueError(f"Row {row_number}: label must be Stationary, Walking, or Running.")
            trial = Trial(
                trial_id=(row.get("trial_id") or "").strip() or f"trial-{row_number - 1}",
                start=parse_utc(row.get("start_utc") or "", f"row {row_number} start_utc"),
                end=parse_utc(row.get("end_utc") or "", f"row {row_number} end_utc"),
                label=GROUND_TRUTH[label_key],
            )
            if trial.start >= trial.end:
                raise ValueError(f"Row {row_number}: end_utc must be after start_utc.")
            scored_start, scored_end = trial.scored_bounds(guard)
            if scored_start >= scored_end:
                raise ValueError(
                    f"Row {row_number}: trial is too short for the requested boundary guard."
                )
            trials.append(trial)

    trials.sort(key=lambda item: item.start)
    for earlier, later in zip(trials, trials[1:]):
        if earlier.end > later.start:
            raise ValueError(f"Trials {earlier.trial_id!r} and {later.trial_id!r} overlap.")
    if not trials:
        raise ValueError("Labels CSV contains no trials.")
    return trials


def open_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Database file does not exist: {resolved}")
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def assign_trial(
    timestamp: datetime,
    trials: list[Trial],
    guard: timedelta,
) -> tuple[Trial | None, bool]:
    """Return (trial, inside scored bounds); guard windows retain their trial label."""
    for trial in trials:
        if trial.start <= timestamp < trial.end:
            scored_start, scored_end = trial.scored_bounds(guard)
            return trial, scored_start <= timestamp < scored_end
    return None, False


def metrics(pairs: list[tuple[str, str]]) -> None:
    total = len(pairs)
    correct = sum(actual == predicted for actual, predicted in pairs)
    print("\nWalking-versus-Running evaluation (stationary trials excluded):")
    if total == 0:
        print("No scored Walking/Running predictions. Accuracy and class metrics are unavailable.")
        return

    print(f"Scored predictions: {total}")
    print(f"Accuracy: {correct / total:.3f} ({correct}/{total})")
    print("Confusion matrix (rows = actual, columns = predicted):")
    print("                 Pred Walking  Pred Running")
    for actual in CLASSES:
        counts = [sum(a == actual and p == predicted for a, p in pairs) for predicted in CLASSES]
        print(f"Actual {actual:<8} {counts[0]:>12} {counts[1]:>13}")

    print("Per-class metrics:")
    for label in CLASSES:
        tp = sum(actual == label and predicted == label for actual, predicted in pairs)
        support = sum(actual == label for actual, _ in pairs)
        predicted_count = sum(predicted == label for _, predicted in pairs)
        precision = tp / predicted_count if predicted_count else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        print(
            f"  {label}: precision={precision:.3f}, recall={recall:.3f}, "
            f"F1={f1:.3f}, support={support}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score saved RT-Spark predictions against UTC trial intervals."
    )
    parser.add_argument("--database", required=True, type=Path, help="isolated test SQLite file")
    parser.add_argument("--labels", required=True, type=Path, help="CSV with trial_id,start_utc,end_utc,label")
    parser.add_argument(
        "--boundary-guard-seconds",
        type=float,
        default=5.0,
        help="exclude predictions and readings near each trial edge (default: 5)",
    )
    parser.add_argument(
        "--predictions-out",
        type=Path,
        help="optional path for a per-prediction CSV report",
    )
    args = parser.parse_args()
    if args.boundary_guard_seconds < 0:
        parser.error("--boundary-guard-seconds must be nonnegative")

    try:
        guard = timedelta(seconds=args.boundary_guard_seconds)
        trials = load_trials(args.labels, guard)
        with open_read_only(args.database) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not {"sensor_readings", "activities"}.issubset(tables):
                raise ValueError("Database must contain sensor_readings and activities tables.")
            readings = [
                parse_sqlite_utc(row[0])
                for row in connection.execute("SELECT timestamp FROM sensor_readings")
            ]
            predictions = [
                dict(row)
                for row in connection.execute(
                    "SELECT id, activity, confidence, timestamp FROM activities ORDER BY id"
                )
            ]

        scored_pairs: list[tuple[str, str]] = []
        trial_predictions: dict[str, list[tuple[dict, bool]]] = {
            trial.trial_id: [] for trial in trials
        }
        unassigned_predictions = 0
        for row in predictions:
            timestamp = parse_sqlite_utc(row["timestamp"])
            trial, inside_scored_bounds = assign_trial(timestamp, trials, guard)
            if trial is None:
                unassigned_predictions += 1
                continue
            trial_predictions[trial.trial_id].append((row, inside_scored_bounds))
            predicted = str(row["activity"])
            if inside_scored_bounds and trial.label in CLASSES and predicted in CLASSES:
                scored_pairs.append((trial.label, predicted))

        print(f"Database: {args.database.resolve()}")
        print(f"Boundary guard: {args.boundary_guard_seconds:g} seconds at both ends")
        print("Per-trial results:")
        report_rows = []
        for trial in trials:
            scored_start, scored_end = trial.scored_bounds(guard)
            sensor_count = sum(scored_start <= value < scored_end for value in readings)
            assigned = trial_predictions[trial.trial_id]
            core_predictions = [
                row for row, include in assigned
                if include and str(row["activity"]) in CLASSES
            ]
            counts = Counter(str(row["activity"]) for row in core_predictions)
            correct = sum(str(row["activity"]) == trial.label for row in core_predictions)
            accuracy = (
                f"{correct / len(core_predictions):.3f} ({correct}/{len(core_predictions)})"
                if trial.label in CLASSES and core_predictions
                else "not scored"
            )
            print(
                f"  {trial.trial_id}: {trial.label}; sensor readings={sensor_count}; "
                f"predictions in core interval={len(core_predictions)}; Walking={counts['Walking']}; "
                f"Running={counts['Running']}; trial accuracy={accuracy}"
            )
            if trial.label == "Stationary":
                print("    Stationary is outside the trained classes; prediction counts are behavior only.")
            for row, include in assigned:
                report_rows.append({
                    "trial_id": trial.trial_id,
                    "ground_truth": trial.label,
                    "prediction_id": row["id"],
                    "timestamp_utc": row["timestamp"],
                    "predicted_activity": row["activity"],
                    "confidence_percent": row["confidence"],
                    "included_in_walking_running_metrics": (
                        "yes" if include and trial.label in CLASSES and row["activity"] in CLASSES else "no"
                    ),
                    "excluded_reason": (
                        "" if include and trial.label in CLASSES and row["activity"] in CLASSES
                        else "boundary guard" if not include
                        else "stationary is outside the trained classes" if trial.label == "Stationary"
                        else "prediction is outside Walking/Running"
                    ),
                })

        metrics(scored_pairs)
        print(f"Predictions outside all trial intervals: {unassigned_predictions}")
        if args.predictions_out:
            args.predictions_out.parent.mkdir(parents=True, exist_ok=True)
            columns = [
                "trial_id", "ground_truth", "prediction_id", "timestamp_utc",
                "predicted_activity", "confidence_percent",
                "included_in_walking_running_metrics", "excluded_reason",
            ]
            with args.predictions_out.open("w", newline="", encoding="utf-8") as target:
                writer = csv.DictWriter(target, fieldnames=columns)
                writer.writeheader()
                writer.writerows(report_rows)
            print(f"Per-prediction report written to: {args.predictions_out.resolve()}")
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"Evaluation error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
