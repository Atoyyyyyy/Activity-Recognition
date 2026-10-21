const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync(
    require("node:path").join(__dirname, "../vercel_dashboard/index.html"),
    "utf8"
);
const inlineScript = html.match(/<script>([\s\S]*?)<\/script>/)[1];

test("dashboard waits instead of showing history when no current reading exists", async () => {
    const elements = new Map();
    const makeElement = () => ({
        textContent: "",
        className: "",
        style: {},
        children: [],
        appendChild(child) { this.children.push(child); },
        replaceChildren() { this.children = []; }
    });
    const document = {
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, makeElement());
            return elements.get(id);
        },
        createElement: makeElement
    };
    const context = {
        document,
        window: { location: { origin: "https://dashboard.example" } },
        navigator: {},
        setInterval() {},
        fetch: async url => ({
            ok: true,
            json: async () => url.endsWith("/api/status")
                ? {
                    stream_state: "waiting",
                    sensor_age_seconds: null,
                    latest_prediction: null
                }
                : [{
                    id: 7,
                    activity: "Walking",
                    confidence: 85,
                    source: "random_forest",
                    timestamp: new Date().toISOString()
                }]
        })
    };

    vm.runInNewContext(inlineScript, context);
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(elements.get("activity").textContent, "Waiting for connection");
    assert.equal(elements.get("prediction-method").textContent, "No current prediction");
});
