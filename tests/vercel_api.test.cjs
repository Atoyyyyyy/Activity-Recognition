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
    const row = {
        id: 42,
        activity: "Walking",
        confidence: 81.25,
        source: "random_forest",
        timestamp: new Date().toISOString(),
        sensor_timestamp: new Date(Date.now() - 1500).toISOString()
    };
    mockRows([row]);
    const response = await invoke(statusHandler, "GET");
    assert.equal(response.statusCode, 200);
    assert.equal(response.body.stream_state, "live");
    assert.equal(response.body.latest_prediction.id, 42);
    assert.ok(response.body.sensor_age_seconds < 10);
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
    const rows = [{
        id: 8, activity: "Running", confidence: 92, source: "random_forest",
        timestamp: new Date().toISOString()
    }];
    mockRows(rows);
    const response = await invoke(activitiesHandler, "GET");
    assert.equal(response.statusCode, 200);
    assert.deepEqual(response.body, rows);
});

test("activities returns an empty list when no records exist", async () => {
    configureTestEnvironment();
    mockRows([]);
    const response = await invoke(activitiesHandler, "GET");
    assert.equal(response.statusCode, 200);
    assert.deepEqual(response.body, []);
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
    assert.match(requestOptions.url, /activity_predictions\?on_conflict=id$/);
    assert.match(requestOptions.options.headers.Prefer, /ignore-duplicates/);
    assert.equal(JSON.parse(requestOptions.options.body).id, 50);
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
    const response = await invoke(activitiesHandler, "GET");
    assert.equal(response.statusCode, 503);
    assert.deepEqual(response.body, { error: "database_not_configured" });
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
