const STALE_AFTER_SECONDS = 10;

class SupabaseConfigurationError extends Error {}

function getSupabaseConfig() {
    const rawUrl = process.env.SUPABASE_URL;
    const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
    if (!rawUrl || !serviceKey) {
        throw new SupabaseConfigurationError("Supabase server configuration is missing.");
    }

    let parsed;
    try {
        parsed = new URL(rawUrl);
    } catch {
        throw new SupabaseConfigurationError("Supabase server configuration is invalid.");
    }
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
        throw new SupabaseConfigurationError("Supabase server configuration is invalid.");
    }
    return { baseUrl: parsed.origin, serviceKey: serviceKey };
}

async function supabaseRequest(path, options) {
    const config = getSupabaseConfig();
    const headers = {
        apikey: config.serviceKey,
        Accept: "application/json"
    };
    // New Supabase secret keys are API keys, not JWTs. Sending one as a
    // bearer token can make the Data API reject it while parsing Authorization.
    if (!config.serviceKey.startsWith("sb_secret_")) {
        headers.Authorization = "Bearer " + config.serviceKey;
    }
    const response = await fetch(config.baseUrl + "/rest/v1/" + path, {
        method: options.method || "GET",
        headers: Object.assign(headers, options.headers || {}),
        body: options.body
    });
    if (!response.ok) {
        throw new Error("Supabase request failed with status " + response.status + ".");
    }
    if (response.status === 204) return null;
    const text = await response.text();
    return text ? JSON.parse(text) : null;
}

function sendJson(res, status, value) {
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    return res.status(status).json(value);
}

function methodNotAllowed(res, allowed) {
    res.setHeader("Allow", allowed);
    return sendJson(res, 405, { error: "method_not_allowed" });
}

function handleDatabaseError(res, error) {
    if (error instanceof SupabaseConfigurationError) {
        return sendJson(res, 503, { error: "database_not_configured" });
    }
    return sendJson(res, 502, { error: "database_unavailable" });
}

module.exports = {
    STALE_AFTER_SECONDS,
    SupabaseConfigurationError,
    getSupabaseConfig,
    supabaseRequest,
    sendJson,
    methodNotAllowed,
    handleDatabaseError
};
