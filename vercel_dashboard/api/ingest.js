const crypto = require("node:crypto");
const {
    supabaseRequest,
    sendJson,
    methodNotAllowed,
    handleDatabaseError
} = require("../lib/supabase");

const ACTIVITIES = new Set(["Idle", "Walking", "Running"]);
const SOURCES = new Set(["motion_heuristic", "random_forest"]);

function hasValidBearerToken(req) {
    const expected = process.env.INGEST_TOKEN;
    const header = req.headers && req.headers.authorization;
    if (!expected || typeof header !== "string" || !header.startsWith("Bearer ")) {
        return false;
    }
    const providedBytes = Buffer.from(header.slice(7));
    const expectedBytes = Buffer.from(expected);
    return providedBytes.length === expectedBytes.length
        && crypto.timingSafeEqual(providedBytes, expectedBytes);
}

function parseTimestamp(value) {
    if (typeof value !== "string") return null;
    const milliseconds = Date.parse(value);
    if (!Number.isFinite(milliseconds) || milliseconds > Date.now() + 60000) return null;
    return new Date(milliseconds).toISOString();
}

function validatePrediction(body) {
    if (!body || typeof body !== "object" || Array.isArray(body)) return null;
    if (!Number.isSafeInteger(body.id) || body.id < 1) return null;
    if (!ACTIVITIES.has(body.activity) || !SOURCES.has(body.source)) return null;
    if (body.source === "motion_heuristic"
        && (body.activity !== "Idle" || body.confidence !== null)) return null;
    if (body.source === "random_forest"
        && ((body.activity !== "Walking" && body.activity !== "Running")
            || typeof body.confidence !== "number"
            || !Number.isFinite(body.confidence)
            || body.confidence < 0
            || body.confidence > 100)) return null;
    const timestamp = parseTimestamp(body.timestamp);
    const sensorTimestamp = parseTimestamp(body.sensor_timestamp);
    if (!timestamp || !sensorTimestamp) return null;
    return {
        // Local SQLite IDs can restart or overlap records already in Supabase.
        // Keep the source ID as an idempotency key; Supabase owns `id`.
        local_prediction_id: body.id,
        activity: body.activity,
        confidence: body.confidence,
        source: body.source,
        timestamp: timestamp,
        sensor_timestamp: sensorTimestamp
    };
}

module.exports = async function handler(req, res) {
    if (req.method !== "POST") return methodNotAllowed(res, "POST");
    if (!process.env.INGEST_TOKEN) {
        return sendJson(res, 503, { error: "ingestion_not_configured" });
    }
    if (!hasValidBearerToken(req)) {
        return sendJson(res, 401, { error: "unauthorized" });
    }
    let body = req.body;
    if (typeof body === "string") {
        try { body = JSON.parse(body); } catch {
            return sendJson(res, 400, { error: "invalid_prediction" });
        }
    }
    const prediction = validatePrediction(body);
    if (!prediction) return sendJson(res, 400, { error: "invalid_prediction" });

    try {
        await supabaseRequest(
            "activity_predictions?on_conflict=local_prediction_id,timestamp", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Prefer: "resolution=ignore-duplicates,return=minimal"
                },
                body: JSON.stringify(prediction)
            }
        );
        return sendJson(res, 202, { accepted: true });
    } catch (error) {
        return handleDatabaseError(res, error);
    }
};
