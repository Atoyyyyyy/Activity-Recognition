# Step 89: Synchronized 60-Second Stationary Test

**Result: PASS** against the requested stability criteria for this single run.

## Test interval and setup

- Start UTC: `2026-10-21T02:39:15.545381Z`
- End UTC: `2026-10-21T02:40:15.571243Z`
- Measured duration: `60.025862` seconds
- RT-Spark was flat and stationary on a safe nonconductive surface, powered by laptop USB, as confirmed by the user.
- The existing MQTT subscriber, Mosquitto service, Flask processes, and isolated database were left running. No duplicate subscriber was started. COM3 was not opened. The user reported the subscriber was receiving before the test; during the interval, database IDs and Flask status were sampled every five seconds.
- Database: `%TEMP%\ActivityRecognitionLab_Step78_20261021_084701.sqlite`, inspected read-only.

## Observed data

The database stores UTC `CURRENT_TIMESTAMP` values with one-second precision. There were **264 readings definitely inside** the interval, with IDs `17716`–`17979`. **Five more readings** have timestamp `02:40:15`, the end-boundary second, and may have occurred before or after the precise end. No reading was stamped in the start-boundary second. Thus the interval contained **264–269** readings, corresponding to approximately **4.40–4.48 Hz** over 60.025862 seconds.

The first in-interval timestamp was `02:39:19`, about 3.5–4.5 seconds after the precise start. The largest difference between successive definite timestamp labels was **3 seconds**; because labels are truncated to whole seconds, the actual gap may approach 4 seconds. No in-interval gap approached the 10-second stale threshold.

Flask `/api/status` was sampled at elapsed seconds 0, 5, ..., 55. All 12 samples reported `live`; sensor age ranged from about **0.56 to 2.62 seconds**. Sensor and prediction IDs advanced throughout. There were **26 predictions** timestamped definitely inside the interval (IDs `1772`–`1797`): **25 Walking** and **1 Running**. A further Walking prediction (ID `1798`) was timestamped in the ambiguous end second and is excluded from the definite count. The subscriber only saves a prediction after filling a ten-reading window and clears the buffer after each inference. The first in-interval prediction may include pre-start readings; the **25 subsequent windows** were formed after that in-interval completion and therefore from post-start readings, subject to the database's lack of explicit window-to-row links.

The Running output occurred while the board was stationary. Stationary is outside the trained classes, so this is an out-of-class model response and is not Walking/Running accuracy evidence.

## Diagnostics and limitations

- Directly observed: 264 definite raw rows, up to five boundary rows, advancing prediction IDs, all sampled Flask statuses `live`, and continued database/API updates after the capture.
- The existing subscriber's console was not directly captured by this agent. The user confirmed it was receiving before the test; SQLite inserts and Flask status provide independent evidence of continued delivery during the test.
- COM3 UART output was unavailable and was not opened. Firmware sample/publish counts, `ERR_MEM`, request occupancy, TCP release counters, and UART reconnect messages are therefore unavailable for this interval.
- No timestamped Mosquitto or subscriber connection log was available to correlate a reconnect. A changed RT-Spark TCP source port was seen between separate connection snapshots, but those snapshots do not establish that a reconnect happened during this 60-second interval.
- SQLite's second-level timestamps limit gap precision and do not identify whether a future interruption originates in firmware publishing, Wi-Fi/TCP/MQTT, or subscriber scheduling.

## Evaluation

This run **passes**: readings arrived steadily, no observed gap exceeded 10 seconds, Flask remained live at every five-second sample, and repeated ten-reading inference outputs were recorded. The measured rate is close to the nominal 5 Hz sensor cadence and improves on the Step 87 stationary capture. This demonstrates one stable minute; it does not establish that the earlier intermittent fault is resolved permanently or identify its cause. No software, firmware, MQTT, firewall, or router changes were made.
