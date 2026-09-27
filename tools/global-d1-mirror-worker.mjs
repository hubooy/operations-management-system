// Runs the built Worker with private R2, no D1, no cron and loopback-only egress.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir, writeFile, realpath } from "node:fs/promises";
import path from "node:path";
import { Miniflare } from "miniflare";

const root = path.resolve(import.meta.dirname, "..");
const runRoot = path.resolve(process.env.TERUISI_D1_MIRROR_RUN_ROOT ?? "");
assert.equal(path.dirname(runRoot), path.join(root, ".runtime"));
assert.match(path.basename(runRoot), /^global-d1-mirror-[0-9a-f]{12}$/);
assert.equal(await realpath(runRoot), runRoot);
assert.notEqual(root.toLowerCase(), "d:\\运营管理系统");
assert.equal(process.env.TERUISI_D1_MIRROR_HTTP_OFFSET, "10000");
const secret = process.env.TERUISI_D1_MIRROR_INTERNAL_SECRET;
assert.match(secret ?? "", /^[0-9a-f]{64}$/);
const domains = ["SALES", "FINANCE", "NETSHOP", "MARKET", "PRODUCTS", "INVENTORY", "WORKFLOW", "CUSTOMER_SERVICE", "BI", "ERP", "ACCESS_CONTROL", "AI"];
const bindings = { TERUISI_RUNTIME_ENV: "development", TERUISI_LOCAL_DIRECT_ACCESS: "true",
  VITE_TERUISI_LOCAL_BUILD: "true", TERUISI_DJANGO_INTERNAL_SECRET: secret };
const allowedPorts = new Set();
domains.forEach((domain, index) => {
  for (const [offset, role] of domain === "BI" ? [[1, "READER"]] : [[1, "READER"], [2, "WRITER"]]) {
    const port = 18000 + index * 10 + offset;
    allowedPorts.add(String(port));
    bindings[`TERUISI_DJANGO_${domain}_${role}_BASE_URL`] = `http://127.0.0.1:${port}`;
  }
  bindings[`TERUISI_DJANGO_${domain}_MODE`] = "django";
});
const serverRoot = path.join(root, "dist/server");
const config = JSON.parse(await readFile(path.join(serverRoot, "wrangler.json"), "utf8"));
assert.deepEqual(config.d1_databases, []);
const modules = (await readdir(serverRoot, { recursive: true })).filter(file => /\.[cm]?js$/.test(file));
modules.sort((a, b) => a === "index.js" ? -1 : b === "index.js" ? 1 : a.localeCompare(b));
const moduleHashes = await Promise.all(modules.map(async file => ({ file: file.replaceAll("\\", "/"),
  sha256: createHash("sha256").update(await readFile(path.join(serverRoot, file))).digest("hex") })));
const builtWorkerSha256 = createHash("sha256").update(JSON.stringify(moduleHashes)).digest("hex");
const options = { modules: modules.map(file => ({ type: "ESModule", path: path.join(serverRoot, file) })), modulesRoot: serverRoot,
  compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags, bindings,
  r2Buckets: ["SALES_IMPORT_FILES"], r2Persist: path.join(runRoot, "r2"), cf: false, port: 0, host: "127.0.0.1",
  outboundService: async (request) => {
    const url = new URL(request.url);
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !allowedPorts.has(url.port)) {
      throw new Error("mirror_external_egress_rejected");
    }
    return fetch(request.url, { method: request.method, headers: request.headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer(), redirect: "error" });
  },
};
const runtime = new Miniflare(options);
const checks = [];
try {
  for (const [route, expected] of [
    ["/_teruisi/local/health/live", 200], ["/_teruisi/local/health/ready", 200], ["/", 200],
    ["/api/sales/data-health", 200], ["/api/finance/targets?view=items&pageSize=1", 200],
    ["/api/finance/targets?view=options", 200], ["/api/imports/finance?pageSize=1", 200],
    ["/api/market/annotations?view=workspace_fast", 200], ["/api/ai/models", 200],
    ["/api/netshop/products?pageSize=1", 200], ["/api/products/summary?pageSize=1", 200],
    ["/api/inventory/overview?pageSize=1", 200], ["/api/customer-service/conversations?pageSize=1", 200],
    ["/api/finance/targets?view=invalid", 400], ["/api/search?q=mirror-nonexistent-sku&group=products&pageSize=1", 200],
  ]) {
    const started = Date.now();
    const response = await runtime.dispatchFetch("http://127.0.0.1" + route, { headers: { "x-teruisi-local-health": "1" } });
    const bytes = new Uint8Array(await response.arrayBuffer());
    checks.push({ route, status: response.status, elapsedMs: Date.now() - started, responseBytes: bytes.length,
      responseSha256: createHash("sha256").update(bytes).digest("hex") });
    if (response.status !== expected) {
      const failure = JSON.parse(new TextDecoder().decode(bytes));
      throw new Error(`mirror_worker_api_failed:${route}:${response.status}:${failure.code ?? "unknown"}`);
    }
  }
  const unsigned = await fetch("http://127.0.0.1:18011/api/finance/targets?view=items");
  assert.equal(unsigned.status, 401);
  checks.push({ route: "django-finance-unsigned", status: unsigned.status });
} finally {
  await runtime.dispose();
  await writeFile(path.join(runRoot, "worker-result.json"), JSON.stringify({ version: "teruisi-global-d1-mirror-worker-v1",
    builtWorkerSha256, d1Bindings: 0, privateR2: true, externalEgressBlocked: true, checks }, null, 2) + "\n");
}
