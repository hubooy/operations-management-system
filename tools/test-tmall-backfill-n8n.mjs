// Isolated n8n integration rehearsal. No production DB, browser, helper or
// external business endpoints: every HTTP node must be rebound to this mock.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const n8nRoot = process.argv[2];
if (!n8nRoot || !path.isAbsolute(n8nRoot)) throw new Error("Provide an absolute installed n8n package directory");
const require = createRequire(path.join(n8nRoot, "package.json"));
const sqlite = require("sqlite3");
const flatted = require("flatted");
const root = await mkdtemp(path.join(tmpdir(), "teruisi-tmall-backfill-test-"));
const cli = path.join(n8nRoot, "bin", "n8n");
// Start from a small allowlist: never inherit production DB, credentials,
// webhooks, proxy, registration, env-file or workflow variables.
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  ["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE"].includes(key.toUpperCase())));
Object.assign(env, {
  N8N_USER_FOLDER: root, DB_TYPE: "sqlite", DB_SQLITE_DATABASE: path.join(root, "test.sqlite"),
  N8N_DIAGNOSTICS_ENABLED: "false", N8N_VERSION_NOTIFICATIONS_ENABLED: "false",
  N8N_TEMPLATES_ENABLED: "false", N8N_RUNNERS_ENABLED: "false", N8N_BLOCK_ENV_ACCESS_IN_NODE: "false",
  N8N_RUNNERS_BROKER_PORT: "0", N8N_RUNNERS_BROKER_LISTEN_ADDRESS: "127.0.0.1",
  N8N_ENCRYPTION_KEY: "isolated-rehearsal-not-a-production-key", GENERIC_TIMEZONE: "Asia/Shanghai",
});
const run = async (args, label, expectedFailure = false) => {
  let output = "";
  const child = spawn(process.execPath, [cli, ...args], { cwd: root, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  child.stdout.on("data", bytes => { output += bytes; });
  child.stderr.on("data", bytes => { output += bytes; });
  const timer = setTimeout(() => child.kill(), 90_000);
  const code = await new Promise((resolve, reject) => { child.on("error", reject); child.on("exit", resolve); });
  clearTimeout(timer);
  await writeFile(path.join(root, `${label}.log`), output);
  if (code !== 0 && !(expectedFailure && code === 1)) throw new Error(`${label}: n8n exit ${code}; see ${root}`);
};
const files = ["tmall-yijiu-direct-pm-candidate", "tmall-lili-sycm-cookie-daily", "tmall-tuofeng-sycm-cookie-daily",
  "tmall-cuizhiwang-sycm-cookie-daily", "tmall-masitu-sycm-cookie-daily", "tmall-yiyong-direct-pm-candidate"];
let scenario;
const calls = [];
let nextIndex = 0;
const server = createServer((request, response) => {
  const endpoint = request.url;
  calls.push(endpoint);
  const reply = value => { response.writeHead(200, { "Content-Type": "application/json" }); response.end(JSON.stringify(value)); };
  assert.equal(request.headers["x-teruisi-tmall-store-key"], scenario.storeKey);
  assert.match(request.headers["x-teruisi-n8n-execution-id"] ?? "", /^\d+$/);
  if (endpoint === "/coordination/claim") return reply({ ok: true, coordinationStatus: "granted" });
  if (endpoint === "/next-day") {
    assert.equal(request.headers["x-teruisi-tmall-backfill-cycle"], String(nextIndex));
    nextIndex++;
    return reply({ ok: true, continueBackfill: nextIndex < scenario.days });
  }
  if (endpoint.startsWith("/product-master")) return reply({ ok: true, browserClosure: { ok: true, status: "closed" },
    dailyBackfill: { status: scenario.budget ? "budget_exhausted" : "completed", remainingDates: scenario.budget ? ["2026-09-08"] : [] } });
  if (!["/plan-backfill", "/fetch", "/import", "/promotion", "/promotion-direct-v1"].includes(endpoint)) {
    response.writeHead(404); return response.end();
  }
  reply({ ok: true, coverageConfirmed: true });
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;
const results = [];
try {
  for (let i = 0; i <= files.length; i++) {
    const workflow = JSON.parse(await readFile(new URL(`../automation/n8n/${files[i % files.length]}.workflow.json`, import.meta.url), "utf8"));
    workflow.id = `IsolatedBackfill${i}`;
    workflow.active = false;
    for (const node of workflow.nodes) {
      if (node.type === "n8n-nodes-base.httpRequest") {
        const url = new URL(node.parameters.url);
        assert.equal(url.origin, "http://127.0.0.1:5791");
        node.parameters.url = `http://127.0.0.1:${port}${url.pathname}`;
        node.parameters.options.timeout = 5_000;
      } else assert.ok(["n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.manualTrigger", "n8n-nodes-base.if",
        "n8n-nodes-base.wait", "n8n-nodes-base.stickyNote", "n8n-nodes-base.stopAndError"].includes(node.type));
    }
    assert.equal(JSON.stringify(workflow.nodes).includes("http://127.0.0.1:5791"), false);
    scenario = { storeKey: workflow.nodes.find(n => n.name.startsWith("A·")).parameters.headerParameters.parameters.find(h => h.name === "X-TERUISI-TMALL-STORE-KEY").value,
      days: i === 0 ? 3 : i === 1 ? 1 : 2, budget: i === files.length };
    calls.length = 0; nextIndex = 0;
    const file = path.join(root, `workflow-${i}.json`);
    await writeFile(file, JSON.stringify(workflow));
    await run(["import:workflow", `--input=${file}`], `import-${i}`);
    await run(["execute", `--id=${workflow.id}`], `execute-${i}`, scenario.budget);
    const db = new sqlite.Database(path.join(root, "test.sqlite"), sqlite.OPEN_READONLY);
    const record = await new Promise((resolve, reject) => db.get("SELECT e.status,d.data FROM execution_entity e JOIN execution_data d ON d.executionId=e.id WHERE e.workflowId=? ORDER BY e.id DESC LIMIT 1", [workflow.id], (e, row) => e ? reject(e) : resolve(row)));
    await new Promise(resolve => db.close(resolve));
    assert.equal(record?.status, scenario.budget ? "error" : "success");
    const data = flatted.parse(record.data).resultData;
    assert.equal(data.lastNodeExecuted, scenario.budget ? "补缺未完成·已释放店铺资源" : "全部缺失日已补齐？");
    for (const endpoint of ["/plan-backfill", "/coordination/claim"]) assert.equal(calls.filter(v => v === endpoint).length, 1);
    for (const endpoint of ["/fetch", "/import", "/next-day"]) assert.equal(calls.filter(v => v === endpoint).length, scenario.days);
    assert.equal(calls.filter(v => v.startsWith("/promotion")).length, scenario.days);
    assert.equal(calls.filter(v => v.startsWith("/product-master")).length, 1);
    results.push({ ...scenario, status: record.status, calls: [...calls] });
    console.log(JSON.stringify(results.at(-1)));
  }
  await writeFile(path.join(root, "evidence.json"), JSON.stringify({ productionTouched: false, results }, null, 2));
  console.log(JSON.stringify({ ok: true, isolatedDirectory: root, scenarios: results.length }));
} finally { await new Promise(resolve => server.close(resolve)); }
