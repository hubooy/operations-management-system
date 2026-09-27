// Production-shaped bundle + real Worker threads, against a synthetic mutable
// root only. No A/B/C/P/M business request, platform login or business import.
import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createServer } from "node:net";
import path from "node:path";
import { promisify } from "node:util";

const sourceRoot = process.cwd();
const temp = await mkdtemp(path.join(tmpdir(), "teruisi-tmall-slots-smoke-"));
const runtime = path.join(temp, "mirror");
await mkdir(path.join(runtime, "config"), { recursive: true });
await symlink(path.join(sourceRoot, "node_modules"), path.join(temp, "node_modules"), "junction");
const config = JSON.parse(await readFile(path.join(sourceRoot, "config/tmall-store-accounts.json"), "utf8"));
for (const [index, store] of config.stores.entries()) {
  store.loginMode = "manual";
  store.browser = {
    executablePath: path.join(temp, "NO-BROWSER.exe"),
    userDataDir: path.join(runtime, "profiles", store.storeKey),
    profileName: "Default", profileDir: path.join(runtime, "profiles", store.storeKey, "Default"),
    debugPort: 25000 + index,
    downloadDir: path.join(runtime, "downloads", store.storeKey),
  };
}
await writeFile(path.join(runtime, "config/tmall-store-accounts.json"), JSON.stringify(config));
const bundle = path.join(temp, "helper.mjs");
await promisify(execFile)(process.execPath, ["tools/build-worker-helper.mjs", "--source-root", sourceRoot, "--output", bundle], { cwd: sourceRoot, windowsHide: true, timeout: 120000 });
const reserved = createServer();
await new Promise(resolve => reserved.listen(0, "127.0.0.1", resolve));
const port = reserved.address().port;
await new Promise(resolve => reserved.close(resolve));
// Minimal environment: no inherited application credentials or production root.
const env = Object.fromEntries(["SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE"].flatMap(key => process.env[key] ? [[key, process.env[key]]] : []));
env.TERUISI_HELPER_MUTABLE_ROOT = runtime;
const child = spawn(process.execPath, [bundle, "serve", "--port", String(port)], { cwd: runtime, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
let output = "";
child.stdout.on("data", part => { output = (output + part.toString()).slice(-16000); });
child.stderr.resume();
const exited = new Promise(resolve => child.once("exit", resolve));
const origin = `http://127.0.0.1:${port}`;
async function call(route, execution, store, extras = {}) {
  const response = await fetch(origin + route, { method: "POST", headers: {
    "X-TERUISI-WORKFLOW-KEY": "tmall", "X-TERUISI-N8N-EXECUTION-ID": execution,
    "X-TERUISI-TMALL-STORE-KEY": store, ...extras,
  }, signal: AbortSignal.timeout(40000) });
  return { status: response.status, body: await response.json() };
}
try {
  const deadline = Date.now() + 30000;
  while (!output.includes('"stage":"serve"')) {
    if (child.exitCode !== null || Date.now() > deadline) throw new Error("isolated helper did not start");
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  const enabled = config.stores.filter(store => store.enabled);
  assert.equal(enabled.length, 6);
  const claims = await Promise.all(enabled.map(store => call("/coordination/claim", `synthetic-${store.storeKey.slice(6)}`, store.storeKey)));
  assert.ok(claims.every(result => result.status === 200 && result.body.coordinationStatus === "granted"));
  assert.equal((await call("/coordination/claim", "synthetic-lili-second", "tmall-lili")).body.coordinationStatus, "waiting");
  assert.equal((await call("/coordination/claim", "synthetic-lili", "tmall-yiyong")).status, 409);
  assert.equal((await call("/coordination/claim", "synthetic-disabled", "tmall-ledu")).status, 409);
  // Out-of-order guard only: this cannot reach an exporter or open a browser.
  const outOfOrder = await call("/product-master-direct-v1", "synthetic-lili", "tmall-lili", { "X-TERUISI-TMALL-CANDIDATE-PROTOCOL": "lili-direct-m-v1" });
  assert.equal(outOfOrder.status, 409);
  assert.equal(outOfOrder.body.error, "invalid_stage");
  const health = await fetch(origin + "/health", { signal: AbortSignal.timeout(10000) }).then(response => response.json());
  assert.equal(health.isolationProtocol, "tmall-store-isolation-v1");
  assert.equal(health.storeExecutions.length, 6);
  assert.ok(health.storeExecutions.every(slot => slot.status === "running"));
  console.log(JSON.stringify({ ok: true, realWorkerSlots: 6, sameStoreOverlapRejected: true, crossStoreOwnerRejected: true, disabledStoreRejected: true, outOfOrderRejected: true, businessActions: 0, mirror: temp }));
} finally {
  child.kill(); // Exact child created above; no platform browser was started.
  await exited;
  // Keep this synthetic build and its receipt for review; no recursive cleanup.
}
