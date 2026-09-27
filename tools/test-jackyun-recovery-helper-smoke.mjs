// Real immutable helper/Worker routing, synthetic n8n and profile, mocked health.
// No production database, browser, credential, export or import is accessed.
import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { createServer } from "node:net";
import { DatabaseSync } from "node:sqlite";
import { createHash } from "node:crypto";

if (process.platform !== "win32") { console.log(JSON.stringify({ skipped: true, reason: "Windows runtime fixture" })); process.exit(0); }
const sourceRoot = process.cwd(), temp = await mkdtemp(path.join(tmpdir(), "jackyun-recovery-helper-"));
const root = path.join(temp, "mirror"), n8nUserRoot = path.join(temp, "user"), profile = path.join(root, "profile");
for (const p of [path.join(root, "config"), path.join(n8nUserRoot, ".n8n"), path.join(profile, "Default"), path.join(root, "outputs/jackyun-export-first")]) await mkdir(p, { recursive: true });
await writeFile(path.join(profile, "Local State"), "{}");
await symlink(path.join(sourceRoot, "node_modules"), path.join(temp, "node_modules"), "junction");
const registry = JSON.parse(await readFile(path.join(sourceRoot, "config/tmall-store-accounts.json"), "utf8"));
for (const [i, store] of registry.stores.entries()) {
  store.loginMode = "manual";
  store.browser = { executablePath: path.join(temp, "NO-BROWSER.exe"), userDataDir: path.join(root, store.storeKey), profileName: "Default", profileDir: path.join(root, store.storeKey, "Default"), debugPort: 25000 + i, downloadDir: path.join(root, "downloads", store.storeKey) };
}
await writeFile(path.join(root, "config/tmall-store-accounts.json"), JSON.stringify(registry));
const now = Date.now(), iso = offset => new Date(now + offset).toISOString(), sqliteTime = offset => iso(offset).replace("T", " ").replace("Z", "");
const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(now - 125000));
const offsetDay = n => new Date(Date.parse(day + "T00:00:00Z") + n * 86400000).toISOString().slice(0, 10);
const old = { version: 2, protocol: "2026-09-06.export-first.1", executionId: "99000", runId: "n8n-export-first-99000", runDate: day, asOfDate: offsetDay(-1), salesStartDate: offsetDay(-45), baseUrl: "http://localhost:3000", createdAt: iso(-125000), phase: "exporting", exports: {}, exportTransport: "session_api_v1" };
const pipeline = path.join(root, "outputs/jackyun-export-first"), oldBytes = JSON.stringify(old);
await writeFile(path.join(pipeline, old.runId + ".json"), oldBytes);
await writeFile(path.join(pipeline, "active.json"), JSON.stringify({ runId: old.runId, executionId: old.executionId }));
await writeFile(path.join(root, "config/jackyun-export-first-policy.json"), JSON.stringify({ version: old.protocol, browser: { downloadDirectory: path.join(root, "downloads"), allowedDownloadHosts: [], controller: { profileDirectory: profile } } }));
const database = path.join(n8nUserRoot, ".n8n/database.sqlite"), db = new DatabaseSync(database);
db.exec("CREATE TABLE execution_entity(id INTEGER PRIMARY KEY,workflowId TEXT,status TEXT,mode TEXT,startedAt TEXT,stoppedAt TEXT,retrySuccessId TEXT,deletedAt TEXT); CREATE TABLE execution_data(executionId INTEGER,data TEXT)");
const insert = db.prepare("INSERT INTO execution_entity VALUES(?,?,?,?,?,?,?,?)");
insert.run(99000, "J8kY2mQ5vR7sT4pN", "error", "trigger", sqliteTime(-130000), sqliteTime(-120000), null, null);
insert.run(99001, "J8kY2mQ5vR7sT4pN", "running", "trigger", sqliteTime(-10000), null, null, null);
const nodes = ["每天本机时间 00:10", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", "B·接口校验与五表下载"];
db.prepare("INSERT INTO execution_data VALUES(?,?)").run(99000, JSON.stringify([{ resultData: "1" }, { error: "2", runData: "3", lastNodeExecuted: "4" }, { description: "5", httpCode: "6", node: "7" }, Object.fromEntries(nodes.map(n => [n, []])), nodes[4], "API_LOGIN_PAGE_NOT_UNIQUE: no_context", "500", { parameters: "8" }, { url: "9" }, "http://127.0.0.1:5791/jackyun/export-first/export-all"])); db.close();
const beforeDb = await readFile(database), shim = path.join(temp, "mock-health.mjs"), counter = path.join(temp, "health-reads.txt");
await writeFile(shim, `import {appendFileSync} from 'node:fs'; const original=globalThis.fetch; globalThis.fetch=async (input,options)=>{const u=new URL(typeof input==='string'?input:input.url??input); if(u.href==='http://localhost:3000/api/sales/data-health'){appendFileSync(${JSON.stringify(counter)},'health\\n');return Response.json({status:'available'});} if(u.hostname==='127.0.0.1'&&!['3000','5791','5678','5432'].includes(u.port)&&['/health','/coordination/claim','/jackyun/export-first/plan-api'].includes(u.pathname))return original(input,options);throw new Error('Synthetic smoke disallows other network access');};`);
const bundle = path.join(temp, "helper.mjs");
await promisify(execFile)(process.execPath, ["tools/build-worker-helper.mjs", "--source-root", sourceRoot, "--output", bundle], { cwd: sourceRoot, windowsHide: true, timeout: 120000 });
const server = createServer(); await new Promise(resolve => server.listen(0, "127.0.0.1", resolve)); const port = server.address().port; await new Promise(resolve => server.close(resolve));
const env = Object.fromEntries(["SystemRoot", "WINDIR", "PATH", "TEMP", "TMP"].flatMap(key => process.env[key] ? [[key, process.env[key]]] : []));
Object.assign(env, { USERPROFILE: n8nUserRoot, LOCALAPPDATA: path.join(temp, "local"), APPDATA: path.join(temp, "roaming"), TERUISI_HELPER_MUTABLE_ROOT: root });
const child = spawn(process.execPath, ["--import", pathToFileURL(shim).href, bundle, "serve", "--port", String(port)], { cwd: root, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
let output = ""; child.stdout.on("data", part => { output = (output + part.toString()).slice(-16000); }); child.stderr.resume(); const exited = new Promise(resolve => child.once("exit", resolve));
async function call(route) { const r = await fetch(`http://127.0.0.1:${port}${route}`, { method: "POST", headers: { "X-TERUISI-WORKFLOW-KEY": "jackyun", "X-TERUISI-N8N-EXECUTION-ID": "99001" }, signal: AbortSignal.timeout(40000) }); return { status: r.status, body: await r.json() }; }
try {
  const deadline = Date.now() + 30000;
  while (!output.includes('"stage":"serve"')) { if (child.exitCode !== null || Date.now() > deadline) throw new Error("Synthetic helper did not start"); await new Promise(resolve => setTimeout(resolve, 100)); }
  assert.equal((await call("/coordination/claim")).body.coordinationStatus, "granted");
  const result = await call("/jackyun/export-first/plan-api"); assert.equal(result.status, 200, JSON.stringify(result.body)); assert.equal(result.body.runId, "n8n-export-first-99001");
  const receipt = JSON.parse(await readFile(path.join(pipeline, "preflight-closures/n8n-export-first-99000.json"), "utf8"));
  assert.equal(receipt.version, 3); assert.equal(receipt.replacementExecutionId, "99001");
  assert.equal(await readFile(path.join(pipeline, old.runId + ".json"), "utf8"), oldBytes); assert.deepEqual(await readFile(database), beforeDb);
  assert.equal((await readFile(counter, "utf8")).split("health").length - 1, 1);
  console.log(JSON.stringify({ ok: true, realWorkerRecovery: true, syntheticN8nUnchanged: true, oldPlanUnchanged: true, healthReadMocked: true, businessActions: 0, helperSha256: createHash("sha256").update(await readFile(bundle)).digest("hex"), mirror: temp }));
} finally { child.kill(); await exited; }
