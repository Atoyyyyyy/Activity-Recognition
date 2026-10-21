const {
    STALE_AFTER_SECONDS,
    supabaseRequest,
    sendJson,
    methodNotAllowed,
    handleDatabaseError
} = require("../lib/supabase");

module.exports = async function handler(req, res) {
    if (req.method !== "GET") return methodNotAllowed(res, "GET");
    try {
        const rows = await supabaseRequest(
            "activity_predictions?select=id,activity,confidence,source,timestamp,sensor_timestamp&order=id.desc&limit=1",
            {}
        );
        const latest = Array.isArray(rows) && rows.length ? rows[0] : null;
        let ageSeconds = null;
        if (latest && latest.sensor_timestamp) {
            const sensorMs = Date.parse(latest.sensor_timestamp);
            if (!Number.isFinite(sensorMs)) {
                return sendJson(res, 502, { error: "invalid_stored_timestamp" });
            }
            ageSeconds = Math.max(0, (Date.now() - sensorMs) / 1000);
        }
        const streamState = !latest
            ? "waiting"
            : ageSeconds === null || ageSeconds > STALE_AFTER_SECONDS
                ? "stale"
                : "live";
        const latestPrediction = latest ? {
            id: latest.id,
            activity: latest.activity,
            confidence: latest.confidence,
            source: latest.source,
            timestamp: latest.timestamp
        } : null;
        return sendJson(res, 200, {
            api: "ok",
            stream_state: streamState,
            sensor_stale_after_seconds: STALE_AFTER_SECONDS,
            last_sensor_timestamp: latest ? latest.sensor_timestamp : null,
            sensor_age_seconds: ageSeconds,
            latest_prediction: latestPrediction
        });
    } catch (error) {
        return handleDatabaseError(res, error);
    }
};
