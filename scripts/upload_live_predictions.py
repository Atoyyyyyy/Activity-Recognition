#!/usr/bin/env python3
"""Forward only fresh predictions created after this process starts."""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen


FRESHNESS_SECONDS = 10
POLL_SECONDS = 1.0
MAX_BATCH = 100
ALLOWED_ACTIVITIES = {"Idle", "Walking", "Running"}
ALLOWED_SOURCES = {"motion_heuristic", "random_forest"}
SAFE_ERROR_CODES = {
    "invalid_prediction", "unauthorized", "database_unavailable",
    "database_not_configured", "ingestion_not_configured", "method_not_allowed",
    "BAD_REQUEST", "NOT_FOUND", "DEPLOYMENT_NOT_FOUND",
    "FUNCTION_INVOCATION_FAILED", "FUNCTION_INVOCATION_TIMEOUT",
    "INTERNAL_SERVER_ERROR"
}
SAFE_FIELD_NAMES = {
    "id", "activity", "confidence", "source", "timestamp", "sensor_timestamp"
}
SAFE_RESPONSE_HEADERS = ("x-vercel-id", "x-vercel-error", "x-vercel-cache", "server")
SAFE_HEADER_VALUE = re.compile(r"^[A-Za-z0-9_.:/ -]{1,160}$")
MAX_ERROR_BODY_BYTES = 65536
DEFAULT_DB = Path(__file__).resolve().parents[1] / "backend" / "activities.db"


class IngestRejectedError(RuntimeError):
    """A deterministic client error that should not be retried."""


def _safe_error_code(payload):
    if not isinstance(payload, dict):
        return "unavailable"
    candidate = payload.get("error", payload.get("code"))
    if isinstance(candidate, dict):
        candidate = candidate.get("code")
    return candidate if isinstance(candidate, str) and candidate in SAFE_ERROR_CODES else "unavailable"


def _safe_error_fields(payload):
    if not isinstance(payload, dict):
        return []
    candidate_fields = payload.get("invalid_fields", payload.get("fields", []))
    if isinstance(candidate_fields, dict):
        candidate_fields = list(candidate_fields)
    if not isinstance(candidate_fields, list):
        return []
    return sorted({
        field for field in candidate_fields
        if isinstance(field, str) and field in SAFE_FIELD_NAMES
    })


def _safe_url(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if not host:
            return "unavailable"
        if ":" in host:
            host = "[" + host + "]"
        if parsed.port:
            host += ":" + str(parsed.port)
        # Do not print URL user info, query values, or fragments.
        path = parsed.path if parsed.path.endswith("/api/ingest") else "/<redacted-path>"
        return urlunsplit((parsed.scheme, host, path, "", ""))
    except (TypeError, ValueError):
        return "unavailable"


def _body_type(body, content_type):
    if not body:
        return "empty"
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    sample = body.lstrip()[:64].lower()
    if "json" in media_type or sample.startswith((b"{", b"[")):
        try:
            json.loads(body.decode("utf-8"))
            return "json"
        except (UnicodeError, json.JSONDecodeError):
            return "invalid_json"
    if "html" in media_type or sample.startswith((b"<!doctype html", b"<html")):
        return "html"
    if media_type.startswith("text/") or not media_type:
        return "plain_text"
    return "other"


def summarize_http_error(error, request_url, request_method="POST"):
    """Describe an HTTP error without logging body text or private headers."""
    response_headers = error.headers or getattr(error, "hdrs", None)
    content_type = response_headers.get("Content-Type") if response_headers else None
    content_type = content_type.split(";", 1)[0].strip().lower() if content_type else "unknown"
    content_length_header = response_headers.get("Content-Length") if response_headers else None
    try:
        declared_length = int(content_length_header) if content_length_header is not None else None
    except (TypeError, ValueError):
        declared_length = None
    body = b""
    try:
        body = error.read(MAX_ERROR_BODY_BYTES + 1)
    except (AttributeError, OSError):
        pass
    truncated = len(body) > MAX_ERROR_BODY_BYTES
    body = body[:MAX_ERROR_BODY_BYTES]
    parsed_body = None
    if _body_type(body, content_type) == "json":
        try:
            parsed_body = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            pass

    body_type = _body_type(body, content_type)
    code = _safe_error_code(parsed_body)
    fields = _safe_error_fields(parsed_body)
    response_url = error.geturl() if hasattr(error, "geturl") else request_url
    if declared_length is not None and declared_length >= 0:
        length = "response_length=" + str(declared_length)
    elif truncated:
        length = "response_length_at_least=" + str(len(body))
    else:
        length = "response_length=" + str(len(body))
    parts = [
        "HTTP diagnostic:",
        "method=" + request_method,
        "url=" + _safe_url(response_url),
        "status=" + str(error.code),
        "content_type=" + (content_type or "unknown"),
        length,
        "body_type=" + body_type,
        "api_error_code=" + code,
    ]
    for header in SAFE_RESPONSE_HEADERS:
        value = response_headers.get(header) if response_headers else None
        if value:
            value = value.strip()
            safe_value = value if SAFE_HEADER_VALUE.fullmatch(value) else "present_redacted"
            parts.append(header.replace("-", "_") + "=" + safe_value)
    if fields:
        parts.append("fields=" + ",".join(fields))
    if body_type == "html":
        parts.append("summary=HTML error response; body omitted")
    elif body_type == "plain_text":
        parts.append("summary=plain-text error response; body omitted")
    elif body_type == "empty":
        parts.append("summary=empty response body")
    elif body_type == "invalid_json":
        parts.append("summary=invalid JSON response; body omitted")
    elif code == "unavailable":
        parts.append("summary=JSON body had no recognized safe API error code")
    return "; ".join(parts)


def build_prediction(row, sensor_timestamp):
    source = row["source"]
    return {
        "id": int(row["id"]),
        "activity": row["activity"],
        "confidence": None if source == "motion_heuristic" else row["confidence"],
        "source": source,
        "timestamp": iso_utc(row["timestamp"]),
        "sensor_timestamp": iso_utc(sensor_timestamp),
    }


def parse_utc(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value):
    parsed = parse_utc(value)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") if parsed else None


def open_readonly_database(path):
    if not path.is_file():
        raise FileNotFoundError("SQLite database does not exist: " + str(path))
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def current_high_water_marks(connection):
    prediction = connection.execute("SELECT COALESCE(MAX(id), 0) FROM activities").fetchone()[0]
    try:
        sensor = connection.execute(
            "SELECT id FROM sensor_readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError as error:
        raise RuntimeError("SQLite sensor_readings table is unavailable") from error
    return int(prediction or 0), int(sensor[0]) if sensor else 0


def latest_sensor(connection):
    row = connection.execute(
        "SELECT id, timestamp FROM sensor_readings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return (int(row["id"]), row["timestamp"]) if row else (0, None)


def activity_source_expression(connection):
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(activities)")
    }
    # The Flask app's legacy schema predates the additive source column.
    return "source" if "source" in columns else "'random_forest'"


def fresh_enough(timestamp, now):
    parsed = parse_utc(timestamp)
    if parsed is None:
        return False
    age = (now - parsed).total_seconds()
    return -5 <= age <= FRESHNESS_SECONDS


def post_prediction(api_url, token, prediction):
    request = Request(
        api_url.rstrip("/") + "/api/ingest",
        data=json.dumps(prediction, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        if response.status not in (200, 201, 202):
            raise RuntimeError("ingest endpoint returned HTTP " + str(response.status))


def validate_configuration(api_url, token):
    parsed = urlparse(api_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("ACTIVITY_API_URL must be an HTTPS Vercel origin without credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("ACTIVITY_API_URL must not contain a query or fragment.")
    if not token:
        raise ValueError("The ingestion token is required.")
    # RFC 6750 bearer tokens use this visible ASCII alphabet. Checking before
    # constructing the header prevents pasted whitespace/control characters
    # from producing a malformed request. Never include the token in errors.
    if token.lower().startswith("bearer "):
        raise ValueError("Enter the ingestion token only; do not include the 'Bearer ' prefix.")
    if not re.fullmatch(r"[A-Za-z0-9._~+/-]+={0,}", token):
        raise ValueError("The ingestion token contains characters invalid in a Bearer token.")


def safe_token_diagnostic(token):
    """Return token shape metadata only; never include token contents."""
    allowed = re.fullmatch(r"[A-Za-z0-9._~+/-]+={0,}", token) is not None
    unexpected = sum(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~+/-="
        for character in token
    )
    return (
        f"token_length={len(token)}; bearer_prefix={'yes' if token.lower().startswith('bearer ') else 'no'}; "
        f"allowed_bearer_format={'yes' if allowed else 'no'}; unexpected_characters={unexpected}; "
        f"edge_whitespace={'yes' if token != token.strip() else 'no'}"
    )


def run(once=False):
    api_url = os.environ.get("ACTIVITY_API_URL", "").strip().rstrip("/")
    token = os.environ.get("ACTIVITY_INGEST_TOKEN") or getpass("Vercel ingestion token: ")
    print("Safe token check: " + safe_token_diagnostic(token))
    db_path = Path(os.environ.get("ACTIVITY_DB_PATH", DEFAULT_DB)).expanduser()
    validate_configuration(api_url, token)

    connection = open_readonly_database(db_path)
    try:
        prediction_cursor, sensor_start_id = current_high_water_marks(connection)
        print("Live prediction uploader started; existing rows will be skipped.")
        print("SQLite is read-only. Waiting for new fresh classifier predictions.")
        while True:
            now = datetime.now(timezone.utc)
            sensor_id, sensor_timestamp = latest_sensor(connection)
            source_expression = activity_source_expression(connection)
            rows = connection.execute(
                "SELECT id, activity, confidence, " + source_expression + " AS source, timestamp "
                "FROM activities WHERE id > ? ORDER BY id ASC LIMIT ?",
                (prediction_cursor, MAX_BATCH),
            ).fetchall()

            for row in rows:
                prediction_id = int(row["id"])
                source = row["source"]
                sensor_time = parse_utc(sensor_timestamp)
                valid = (
                    row["activity"] in ALLOWED_ACTIVITIES
                    and source in ALLOWED_SOURCES
                    and (source != "motion_heuristic" or row["activity"] == "Idle")
                    and (source != "random_forest" or row["activity"] in {"Walking", "Running"})
                    and sensor_id > sensor_start_id
                    and sensor_time is not None
                    and fresh_enough(row["timestamp"], now)
                    and fresh_enough(sensor_timestamp, now)
                    and not (source == "motion_heuristic" and row["confidence"] is not None)
                )
                if not valid:
                    prediction_cursor = prediction_id
                    continue

                prediction = build_prediction(row, sensor_timestamp)
                try:
                    post_prediction(api_url, token, prediction)
                except HTTPError as error:
                    summary = summarize_http_error(
                        error, api_url.rstrip("/") + "/api/ingest", "POST"
                    )
                    if 400 <= error.code < 500:
                        raise IngestRejectedError(
                            "Prediction rejected; stopping to avoid retrying a deterministic client error: "
                            + summary
                        ) from None
                    print("Prediction upload failed; retrying later: " + summary)
                    break
                except (URLError, TimeoutError, OSError, RuntimeError):
                    print("Prediction upload failed. Retrying when the API is reachable.")
                    break
                prediction_cursor = prediction_id
                print("Forwarded new prediction ID " + str(prediction_id) + ".")
                if once:
                    return

            time.sleep(POLL_SECONDS)
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="upload only the next eligible fresh prediction, then exit",
    )
    arguments = parser.parse_args()
    try:
        run(once=arguments.once)
    except KeyboardInterrupt:
        print("Uploader stopped.")
    except IngestRejectedError as error:
        print("Uploader stopped: " + str(error), file=sys.stderr)
        raise SystemExit(1)
    except (FileNotFoundError, ValueError, RuntimeError, sqlite3.Error) as error:
        print("Uploader could not start: " + str(error), file=sys.stderr)
        raise SystemExit(1)
