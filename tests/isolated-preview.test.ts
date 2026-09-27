import assert from "node:assert/strict";
import test from "node:test";
import { createPreviewFetch } from "../tools/preview/fetch-policy.mjs";
import { cleanEnvironment, validatePorts, validateSnapshot } from "../tools/preview/launcher.mjs";
test("preview child environment never inherits production credentials or execution hooks", () => {
    const env = cleanEnvironment({ Path: "node-path", SYSTEMROOT: "Windows", TERUISI_DJANGO_DATABASE_URL: "production", DJANGO_SECRET_KEY: "production", CLOUDFLARE_API_TOKEN: "production", NODE_OPTIONS: "--import malicious.mjs", PYTHONPATH: "production", HTTP_PROXY: "production" });
    assert.deepEqual(env, { Path: "node-path", SYSTEMROOT: "Windows" });
});
test("restore requires a matching synthetic snapshot manifest and digest", () => {
    const name = "snapshot-123-abcdef01.sqlite3";
    const manifest = { file: name, fixture: "synthetic-v1", sha256: "a".repeat(64) };
    assert.doesNotThrow(() => validateSnapshot(name, manifest, "a".repeat(64)));
    assert.throws(() => validateSnapshot(name, manifest, "b".repeat(64)));
    assert.throws(() => validateSnapshot(name, { ...manifest, fixture: "production" }, "a".repeat(64)));
    assert.throws(() => validateSnapshot("../" + name, manifest, "a".repeat(64)));
});
test("preview rejects production ports and assigns distinct service/control ports", () => {
    for (const port of [3000, 5432, 8001, 8101, NaN, 3100.5, 4000])
        assert.throws(() => validatePorts(port));
    const ports = validatePorts(3100);
    assert.equal(new Set(Object.values(ports)).size, 4);
    assert.deepEqual(ports, { port: 3100, reader: 18100, writer: 18101, control: 13100 });
});

test("preview fetch blocks production and external origins before network access", async () => {
    let calls = 0;
    const guarded = createPreviewFetch(async () => { calls++; return new Response("ok"); }, () => ["http://127.0.0.1:18100"]);
    for (const origin of ["http://127.0.0.1:3000", "http://127.0.0.1:8001", "https://example.com", "http://localhost:18100"]) {
        await assert.rejects(guarded(origin), /preview_external_request_blocked/);
    }
    assert.equal(calls, 0);
    assert.equal((await guarded("http://127.0.0.1:18100/health/live")).status, 200);
    assert.equal(calls, 1);
});

test("preview uses workerd-compatible manual redirect handling without following Location", async () => {
    let calls = 0;
    const guarded = createPreviewFetch(async (_input: unknown, init?: RequestInit) => {
        calls++;
        assert.equal(init?.redirect, "manual");
        return new Response(null, { status: 302, headers: { location: "http://127.0.0.1:3000" } });
    }, () => ["http://127.0.0.1:18100"]);
    await assert.rejects(guarded("http://127.0.0.1:18100/redirect"), /preview_redirect_blocked/);
    assert.equal(calls, 1);
});
