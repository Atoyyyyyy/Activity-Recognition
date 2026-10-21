"""Experimental motion gate for stationary windows (not a validated activity model)."""

from __future__ import annotations

from dataclasses import dataclass
import os

import numpy as np


@dataclass(frozen=True)
class IdleThresholds:
    # Rounded above the larger Step 87/89 observed stationary p95 metrics.
    accel_std_g: float = 0.008
    accel_mean_abs_diff_g: float = 0.005
    gyro_detrended_std_dps: float = 0.23
    gyro_mean_abs_diff_dps: float = 0.20

    @classmethod
    def from_environment(cls) -> "IdleThresholds":
        return cls(
            accel_std_g=float(os.getenv("ACTIVITY_IDLE_ACCEL_STD_G", "0.008")),
            accel_mean_abs_diff_g=float(os.getenv("ACTIVITY_IDLE_ACCEL_DIFF_G", "0.005")),
            gyro_detrended_std_dps=float(os.getenv("ACTIVITY_IDLE_GYRO_STD_DPS", "0.23")),
            gyro_mean_abs_diff_dps=float(os.getenv("ACTIVITY_IDLE_GYRO_DIFF_DPS", "0.20")),
        )


@dataclass(frozen=True)
class MotionMetrics:
    accel_max_axis_std_g: float
    accel_mean_abs_diff_g: float
    gyro_max_axis_detrended_std_dps: float
    gyro_mean_abs_diff_dps: float


def measure_motion(window: np.ndarray) -> MotionMetrics:
    """Measure variation and changes; the constant gravity vector is not counted as motion."""
    values = np.asarray(window, dtype=float)
    if values.ndim != 2 or values.shape[1] != 6 or values.shape[0] < 2:
        raise ValueError("sensor window must have at least two rows and six sensor columns")
    if not np.isfinite(values).all():
        raise ValueError("sensor window must contain only finite values")

    accel = values[:, :3]
    gyro = values[:, 3:]
    return MotionMetrics(
        accel_max_axis_std_g=float(np.max(np.std(accel, axis=0))),
        accel_mean_abs_diff_g=float(np.mean(np.abs(np.diff(accel, axis=0)))),
        # Remove each gyroscope axis's bias/constant turn rate before measuring variation.
        gyro_max_axis_detrended_std_dps=float(np.max(np.std(gyro, axis=0))),
        gyro_mean_abs_diff_dps=float(np.mean(np.abs(np.diff(gyro, axis=0)))),
    )


def is_idle_candidate(window: np.ndarray, thresholds: IdleThresholds | None = None) -> bool:
    """Return true only when every configured movement metric is below its limit."""
    limits = thresholds or IdleThresholds.from_environment()
    metrics = measure_motion(window)
    return (
        metrics.accel_max_axis_std_g <= limits.accel_std_g
        and metrics.accel_mean_abs_diff_g <= limits.accel_mean_abs_diff_g
        and metrics.gyro_max_axis_detrended_std_dps <= limits.gyro_detrended_std_dps
        and metrics.gyro_mean_abs_diff_dps <= limits.gyro_mean_abs_diff_dps
    )


def idle_detection_enabled() -> bool:
    return os.getenv("ACTIVITY_IDLE_ENABLED", "0").strip().casefold() in {"1", "true", "yes", "on"}
