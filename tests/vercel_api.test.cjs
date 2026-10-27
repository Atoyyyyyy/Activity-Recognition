const test = require("node:test");
const assert = require("node:assert/strict");

const statusHandler = require("../vercel_dashboard/api/status");
const activitiesHandler = require("../vercel_dashboard/api/activities");
const ingestHandler = require("../vercel_dashboard/api/ingest");

const originalFetch = global.fetch;
const originalEnv = {
    SUPABASE_URL: process.env.SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY: process.env.SUPABASE_SERVICE_ROLE_KEY,
    INGEST_TOKEN: process.env.INGEST_TOKEN
};

function configureTestEnvironment() {
    process.env.SUPABASE_URL = "https://test-project.supabase.co";
    process.env.SUPABASE_SERVICE_ROLE_KEY = "test-service-role-key-only";
    process.env.INGEST_TOKEN = "test-ingestion-token-only";
}

function invoke(handler, method, body, headers) {
    const res = {
        headers: {},
        statusCode: 200,
        setHeader(name, value) { this.headers[name] = value; return this; },
        status(code) { this.statusCode = code; return this; },
        json(value) { this.body = value; return this; }
    };
    return Promise.resolve(handler({ method, body, headers: headers || {} }, res))
        .then(() => res);
}

function mockRows(rows) {
    global.fetch = async () => new Response(JSON.stringify(rows), {
        status: 200,
        headers: { "Content-Type": "application/json" }
    });
}

async function captureConfigLogs(run) {
    const originalError = console.error;
    const messages = [];
    console.error = (...args) => messages.push(args.join(" "));
    try {
        return { result: await run(), messages };
    } finally {
        console.error = originalError;
    }
}

test("status returns waiting when Supabase has no predictions", async () => {
    configureTestEnvironment();
    mockRows([]);
    const response = await invoke(statusHandler, "GET");
    assert.equal(response.statusCode, 200);
    assert.equal(response.body.stream_state, "waiting");
    assert.equal(response.body.last_sensor_timestamp, null);
    assert.equal(response.body.sensor_age_seconds, null);
    assert.equal(response.body.latest_prediction, null);
});

test("new Supabase secret keys use apikey without a bearer JWT header", async () => {
    process.env.SUPABASE_URL = "https://test-project.supabase.co";
    process.env.SUPABASE_SERVICE_ROLE_KEY = "sb_secret_test-only";
    let requestHeaders;
    global.fetch = async (_url, options) => {
        requestHeaders = options.headers;
        return new Response("[]", {
            status: 200,
            headers: { "Content-Type": "application/json" }
        });
    };

    const response = await invoke(activitiesHandler, "GET");
    assert.equal(response.statusCode, 200);
    assert.equal(requestHeaders.apikey, "sb_secret_test-only");
    assert.equal(Object.hasOwn(requestHeaders, "Authorization"), false);
});

test("status reports a fresh sensor timestamp as live", async () => {
    configureTestEnvironment();
    let requestedUrl;
    const row = {
        id: 42,
        activity: "Walking",
        confidence: 81.25,
        source: "random_forest",
        timestamp: new Date().toISOString(),
        sensor_timestamp: new Date(Date.now() - 1500).toISOString()
    };
    global.fetch = async url => {
        requestedUrl = String(url);
        return new Response(JSON.stringify([row]), {
            status: 200,
            headers: { "Content-Type": "application/json" }
        });
    };
    const response = await invoke(statusHandler, "GET");
    assert.equal(response.statusCode, 200);
    assert.equal(response.body.stream_state, "live");
    assert.equal(response.body.latest_prediction.id, 42);
    assert.ok(response.body.sensor_age_seconds < 10);
    assert.match(requestedUrl, /order=timestamp\.desc,id\.desc&limit=1$/);
});

test("status reports old sensor data as stale", async () => {
    configureTestEnvironment();
    const row = {
        id: 43,
        activity: "Idle",
        confidence: null,
        source: "motion_heuristic",
        timestamp: new Date(Date.now() - 30000).toISOString(),
        sensor_timestamp: new Date(Date.now() - 30000).toISOString()
    };
    mockRows([row]);
    const response = await invoke(statusHandler, "GET");
    assert.equal(response.body.stream_state, "stale");
    assert.equal(response.body.latest_prediction.confidence, null);
});

test("status treats a prediction without a sensor timestamp as stale", async () => {
    configureTestEnvironment();
    mockRows([{
        id: 44, activity: "Running", confidence: 85, source: "random_forest",
        timestamp: new Date().toISOString(), sensor_timestamp: null
    }]);
    const response = await invoke(statusHandler, "GET");
    assert.equal(response.statusCode, 200);
    assert.equal(response.body.stream_state, "stale");
    assert.equal(response.body.sensor_age_seconds, null);
});

test("activities returns latest API records", async () => {
    configureTestEnvironment();
    let requestedUrl;
    const rows = [{
        id: 8, activity: "Running", confidence: 92, source: "random_forest",
        timestamp: new Date().toISOString()
    }];
    global.fetch = async url => {
        requestedUrl = String(url);
        return new Response(JSON.stringify(rows), {
            status: 200,
            headers: { "Content-Type": "application/json" }
        });
    };
    const response = await invoke(activitiesHandler, "GET");
    assert.equal(response.statusCode, 200);
    assert.deepEqual(response.body, rows);
    assert.match(requestedUrl, /order=timestamp\.desc,id\.desc&limit=50$/);
});

test("activities returns an empty list when no records exist", async () => {
    configureTestEnvironment();
    mockRows([]);
    const response = await invoke(activitiesHandler, "GET");
    assert.equal(response.statusCode, 200);
    assert.deepEqual(response.body, []);
});

test("ingest GET probe matches deployed method contract", async () => {
    let called = false;
    global.fetch = async () => { called = true; throw new Error("GET must not access Supabase"); };
    const response = await invoke(ingestHandler, "GET", {});
    assert.equal(response.statusCode, 405);
    assert.equal(response.headers.Allow, "POST");
    assert.deepEqual(response.body, { error: "method_not_allowed" });
    assert.equal(called, false);
});

test("ingest rejects an absent or incorrect token before database access", async () => {
    configureTestEnvironment();
    let called = false;
    global.fetch = async () => { called = true; throw new Error("should not fetch"); };
    const response = await invoke(ingestHandler, "POST", {}, {
        authorization: "Bearer wrong-test-token"
    });
    assert.equal(response.statusCode, 401);
    assert.equal(called, false);
});

test("ingest reports missing server token configuration", async () => {
    delete process.env.INGEST_TOKEN;
    const response = await invoke(ingestHandler, "POST", {});
    assert.equal(response.statusCode, 503);
    assert.deepEqual(response.body, { error: "ingestion_not_configured" });
});

test("ingest accepts an authenticated prediction idempotently", async () => {
    configureTestEnvironment();
    process.env.SUPABASE_SERVICE_ROLE_KEY = "sb_secret_test-only";
    let requestOptions;
    global.fetch = async (url, options) => {
        requestOptions = { url: String(url), options };
        return new Response(null, { status: 204 });
    };
    const prediction = {
        id: 50,
        activity: "Walking",
        confidence: 76,
        source: "random_forest",
        timestamp: new Date().toISOString(),
        sensor_timestamp: new Date().toISOString()
    };
    const response = await invoke(ingestHandler, "POST", prediction, {
        authorization: "Bearer test-ingestion-token-only"
    });
    assert.equal(response.statusCode, 202);
    assert.deepEqual(response.body, { accepted: true });
    assert.match(requestOptions.url, /activity_predictions\?on_conflict=local_prediction_id,timestamp$/);
    assert.match(requestOptions.options.headers.Prefer, /ignore-duplicates/);
    assert.equal(JSON.parse(requestOptions.options.body).local_prediction_id, 50);
    assert.equal(Object.hasOwn(JSON.parse(requestOptions.options.body), "id"), false);
    assert.equal(requestOptions.options.headers.apikey, "sb_secret_test-only");
    assert.equal(Object.hasOwn(requestOptions.options.headers, "Authorization"), false);
    assert.equal(JSON.stringify(response.body).includes("sb_secret_test-only"), false);
});

test("ingest preserves null confidence for heuristic Idle", async () => {
    configureTestEnvironment();
    let stored;
    global.fetch = async (url, options) => {
        stored = JSON.parse(options.body);
        return new Response(null, { status: 204 });
    };
    const response = await invoke(ingestHandler, "POST", {
        id: 51,
        activity: "Idle",
        confidence: null,
        source: "motion_heuristic",
        timestamp: new Date().toISOString(),
        sensor_timestamp: new Date().toISOString()
    }, { authorization: "Bearer test-ingestion-token-only" });
    assert.equal(response.statusCode, 202);
    assert.equal(stored.confidence, null);
});

test("ingest accepts the observed live Idle payload shape without a sensor ID", async () => {
    configureTestEnvironment();
    let stored;
    global.fetch = async (url, options) => {
        assert.match(String(url), /activity_predictions\?on_conflict=local_prediction_id,timestamp$/);
        stored = JSON.parse(options.body);
        return new Response(null, { status: 204 });
    };
    const response = await invoke(ingestHandler, "POST", {
        id: 3788,
        activity: "Idle",
        confidence: null,
        source: "motion_heuristic",
        timestamp: "2026-10-27T04:39:21.000Z",
        sensor_timestamp: "2026-10-27T04:39:22.000Z"
    }, { authorization: "Bearer test-ingestion-token-only" });
    assert.equal(response.statusCode, 202);
    assert.deepEqual(response.body, { accepted: true });
    assert.equal(stored.local_prediction_id, 3788);
    assert.equal(Object.hasOwn(stored, "id"), false);
    assert.equal(stored.confidence, null);
    assert.equal(Object.hasOwn(stored, "sensor_id"), false);
});

test("downstream Supabase HTTP 400 is translated to API HTTP 502", async () => {
    configureTestEnvironment();
    global.fetch = async () => new Response(
        JSON.stringify({ code: "23514", message: "constraint failure" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
    );
    const response = await invoke(ingestHandler, "POST", {
        id: 54,
        activity: "Walking",
        confidence: 80,
        source: "random_forest",
        timestamp: new Date().toISOString(),
        sensor_timestamp: new Date().toISOString()
    }, { authorization: "Bearer test-ingestion-token-only" });
    assert.equal(response.statusCode, 502);
    assert.deepEqual(response.body, { error: "database_unavailable" });
});

test("ingest rejects heuristic probabilities and unknown labels", async () => {
    configureTestEnvironment();
    global.fetch = async () => { throw new Error("invalid rows must not be stored"); };
    const base = {
        id: 52, activity: "Idle", confidence: 50, source: "motion_heuristic",
        timestamp: new Date().toISOString(), sensor_timestamp: new Date().toISOString()
    };
    const badHeuristic = await invoke(ingestHandler, "POST", base, {
        authorization: "Bearer test-ingestion-token-only"
    });
    assert.equal(badHeuristic.statusCode, 400);
    const badLabel = await invoke(ingestHandler, "POST", {
        ...base, id: 53, activity: "Sitting", confidence: null
    }, { authorization: "Bearer test-ingestion-token-only" });
    assert.equal(badLabel.statusCode, 400);
});

test("missing database configuration is reported without exposing secrets", async () => {
    delete process.env.SUPABASE_URL;
    delete process.env.SUPABASE_SERVICE_ROLE_KEY;
    const { result: response, messages } = await captureConfigLogs(
        () => invoke(activitiesHandler, "GET")
    );
    assert.equal(response.statusCode, 503);
    assert.deepEqual(response.body, { error: "database_not_configured" });
    assert.match(messages.join(" "), /SUPABASE_URL/);
    assert.match(messages.join(" "), /SUPABASE_SERVICE_ROLE_KEY/);
    assert.equal(messages.join(" ").includes("test-project"), false);
});

test("invalid URL formatting is logged by category without exposing its contents", async () => {
    process.env.SUPABASE_URL = "not-a-url-private-value";
    process.env.SUPABASE_SERVICE_ROLE_KEY = "sb_secret_never-log-this";
    const { result: response, messages } = await captureConfigLogs(
        () => invoke(statusHandler, "GET")
    );
    const logs = messages.join(" ");
    assert.equal(response.statusCode, 503);
    assert.deepEqual(response.body, { error: "database_not_configured" });
    assert.match(logs, /invalid SUPABASE_URL formatting: URL could not be parsed/);
    assert.equal(logs.includes("not-a-url-private-value"), false);
    assert.equal(logs.includes("sb_secret_never-log-this"), false);
    assert.equal(JSON.stringify(response.body).includes("private-value"), false);
});

test("unsupported methods return 405", async () => {
    const response = await invoke(statusHandler, "POST");
    assert.equal(response.statusCode, 405);
    assert.equal(response.headers.Allow, "GET");
});

test.after(() => {
    global.fetch = originalFetch;
    for (const [name, value] of Object.entries(originalEnv)) {
        if (value === undefined) delete process.env[name];
        else process.env[name] = value;
    }
});
