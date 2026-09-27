import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import { auditedImport891 as a, assertImportRecoveryEvidence, inspectImportRecovery, publishImportRecovery,
  claimImportRecovery, assertExactFailedImportRetry } from "../lib/jackyun/import-recovery";
import { recoverySha, jackyunWorkflowId, type PreflightEvidence } from "../lib/jackyun/preflight-recovery";
import { jackyunModuleOrder } from "../lib/jackyun/post-download";
import { jackyunExportFirstPolicyVersion } from "../lib/jackyun/run-contract";
import { assertExportFirstAction, type JackyunExportFirstPlan } from "../tools/jackyun-export-first-pipeline";
import { createXlsxWorkbookBytes } from "../lib/imports/xlsx-write";
import { runJackyunDownload, type JackyunDownloadRunOptions } from "../tools/jackyun-download-runner";

const now = "2026-09-08T02:30:00.000Z";
const evidence: PreflightEvidence = { executionId: a.executionId, startedAt: a.startedAt, stoppedAt: a.stoppedAt,
  executionDataSha256: a.executionDataSha256, error: a.error, workflowId: jackyunWorkflowId, status: "error", activeExecutions: 0,
  retrySuccessId: null, lastNode: "D·统一导入运营管理系统", httpCode: "500", requestUrl: "http://127.0.0.1:5791/jackyun/export-first/import",
  runNodes: ["手动运行", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", "B·网页校验后 HTTP 导出五表", "C·五表完整校验和导入演练", "D·统一导入运营管理系统"] };
async function fixture() {
  const root = await mkdtemp(path.join(tmpdir(), "jackyun-import-recovery-")), id = a.runId;
  const base = path.join(root, "outputs/jackyun-export-first"), formal = path.join(root, "outputs/jackyun-import-runs", id);
  const validation = path.join(root, "outputs/jackyun-export-first-validation", id), events = path.join(root, "outputs/jackyun-browser-events", id);
  const downloads = path.join(root, "downloads"), files: string[] = [];
  const save = async (file: string, value: unknown) => { await mkdir(path.dirname(file), { recursive: true }); await writeFile(file, JSON.stringify(value)); };
  const exports: Record<string, unknown> = {}, states: Record<string, unknown> = {}, prepared: Record<string, unknown> = {};
  for (const [index, module] of jackyunModuleOrder.entries()) {
    const file = path.join(downloads, "jackyun", id, module, "original.xlsx"), raw = Buffer.from("synthetic " + module);
    await mkdir(path.dirname(file), { recursive: true }); await writeFile(file, raw); files.push(file);
    const event = { runId: id, module, filePath: file }, eventFile = path.join(events, `${String(index + 1).padStart(2, "0")}-${module}.json`);
    await save(eventFile, event);
    exports[module] = { handoffSha256: recoverySha(JSON.stringify(event)), fileSha256: recoverySha(raw), bytes: raw.length };
    states[module] = { status: "handed_off", filePath: file };
    prepared[module] = { module, status: "prepared", batchId: null, sourcePath: file, sourceSha256: recoverySha(raw), inputContractHash: "c".repeat(64) };
  }
  const plan = { version: 1, protocol: jackyunExportFirstPolicyVersion, executionId: "890", runId: id,
    createdAt: "2026-09-08T00:52:43.000Z", phase: "importing", exportTransport: "web_prepared_http_v1",
    runDate: "2026-09-08", asOfDate: "2026-09-07", baseUrl: "http://localhost:3000", exports };
  const failed = { ...(prepared.products as { sourceSha256: string; inputContractHash: string }), status: "failed", startedAt: "2026-09-08T01:21:27.163Z" };
  const audit = { version: 1, runId: id, module: "products", status: "failed", source: { sha256: recoverySha(await readFile(files[0])) },
    timings: { failedAt: "2026-09-08T01:21:34.457Z" }, error: { stage: "chunk_upload_and_import", message: a.error, details: { httpStatus: 503, status: "rejected" } } };
  const planPath = path.join(base, id + ".json"), manifestPath = path.join(formal, "run-manifest.json"), auditPath = path.join(formal, "audit/products.json");
  await save(planPath, plan);
  await save(path.join(base, "active.json"), { runId: id, executionId: "890" });
  await save(path.join(base, "http-resumptions", id + ".json"), { version: 1, originalExecutionId: "890", executionId: "891", permitSha256: "f".repeat(64) });
  await save(path.join(root, "config/jackyun-export-first-policy.json"), { version: plan.protocol, browser: { downloadDirectory: downloads } });
  await save(path.join(formal, "browser-controller-state.json"), { runId: id, policyVersion: plan.protocol, exportTransport: plan.exportTransport, modules: states });
  await save(manifestPath, { version: 1, runId: id, strictOrder: jackyunModuleOrder, modules: { products: failed } });
  await save(path.join(validation, "run-manifest.json"), { version: 1, runId: id, strictOrder: jackyunModuleOrder, modules: prepared });
  await save(auditPath, audit);
  await mkdir(path.join(formal, "processed"), { recursive: true });
  await mkdir(path.join(formal, "raw"), { recursive: true });
  await writeFile(path.join(formal, "raw/products-original.xlsx"), await readFile(files[0]));
  return { root, base, formal, validation, files, plan, planPath, manifestPath, auditPath, failed, audit, save };
}
test("only the diagnosed n8n import failure is eligible; uncertain outcomes stay closed", () => {
  assert.doesNotThrow(() => assertImportRecoveryEvidence(evidence, now));
  for (const change of [{ executionId: "892" }, { executionDataSha256: "0".repeat(64) }, { error: "timeout" }, { status: "success" },
    { activeExecutions: 1 }, { retrySuccessId: "892" }, { requestUrl: "https://other/import" }, { runNodes: evidence.runNodes.slice(1) }]) {
    assert.throws(() => assertImportRecoveryEvidence({ ...evidence, ...change }, now));
  }
  assert.throws(() => assertImportRecoveryEvidence(evidence, "invalid"));
  assert.throws(() => assertImportRecoveryEvidence(evidence, a.startedAt));
});
test("one new execution claims the import recovery; original failures and files are preserved", async () => {
  const f = await fixture(), before = await Promise.all([f.planPath, f.manifestPath, f.auditPath, ...f.files].map(file => readFile(file)));
  const permit = await inspectImportRecovery(f.root, evidence, now);
  await publishImportRecovery(permit, evidence, recoverySha(JSON.stringify(permit)));
  await assert.rejects(claimImportRecovery(f.root, "890", "892", "import", now));
  const results = await Promise.allSettled(["892", "893"].map(id => claimImportRecovery(f.root, "890", id, "plan-direct-http", now)));
  assert.equal(results.filter(r => r.status === "fulfilled").length, 1);
  const winner = results.find(r => r.status === "fulfilled")!;
  assert.equal(winner.status, "fulfilled");
  if (winner.status !== "fulfilled") return;
  const binding = winner.value;
  assert.deepEqual(await Promise.all([f.planPath, f.manifestPath, f.auditPath, ...f.files].map(file => readFile(file))), before);
  assert.deepEqual(await readFile(path.join(f.base, "import-resume-originals/891/products.failed.json")), before[2]);
  assert.deepEqual(await claimImportRecovery(f.root, "890", binding.executionId, "import", now), binding);
  assert.doesNotThrow(() => assertExactFailedImportRetry({ runId: a.runId, module: "products", sourceSha256: f.failed.sourceSha256,
    inputContractHash: f.failed.inputContractHash, prior: f.failed, auditRaw: before[2], binding }));
  for (const patch of [{ module: "sales" }, { sourceSha256: "0".repeat(64) }, { inputContractHash: "0".repeat(64) }, { auditRaw: Buffer.from("changed") }, { prior: { ...f.failed, status: "completed" } }]) {
    assert.throws(() => assertExactFailedImportRetry({ runId: a.runId, module: "products", sourceSha256: f.failed.sourceSha256,
      inputContractHash: f.failed.inputContractHash, prior: f.failed, auditRaw: before[2], binding, ...patch }));
  }
  await writeFile(f.files[2], "changed after claim");
  await assert.rejects(claimImportRecovery(f.root, "890", binding.executionId, "import", now), /证据变化/);
});
test("tampering, partial imports, different policy and changed approval fail before claim", async () => {
  for (const fault of ["workbook", "phase", "transport", "partial-success", "audit", "contract", "policy", "validation", "hash", "extra-attempt"]) {
    const f = await fixture();
    if (fault === "workbook") await writeFile(f.files[1], "changed");
    if (fault === "phase") await f.save(f.planPath, { ...f.plan, phase: "exporting" });
    if (fault === "transport") await f.save(f.planPath, { ...f.plan, exportTransport: "web_session_batch_v1" });
    if (fault === "partial-success") await f.save(f.manifestPath, { version: 1, runId: a.runId, strictOrder: jackyunModuleOrder, modules: { products: { ...f.failed, status: "completed", batchId: "other" } } });
    if (fault === "audit") await f.save(f.auditPath, { ...f.audit, import: { batch: { id: "other" } } });
    if (fault === "contract") await f.save(f.manifestPath, { version: 1, runId: a.runId, strictOrder: jackyunModuleOrder, modules: { products: { ...f.failed, inputContractHash: "a".repeat(64) } } });
    if (fault === "policy") await f.save(path.join(f.root, "config/jackyun-export-first-policy.json"), { version: "other" });
    if (fault === "validation") await f.save(path.join(f.validation, "run-manifest.json"), {});
    if (fault === "extra-attempt") await f.save(path.join(f.formal, "audit/products.attempt-failed.json"), {});
    if (fault === "hash") { const permit = await inspectImportRecovery(f.root, evidence, now); await assert.rejects(publishImportRecovery(permit, evidence, "0".repeat(64))); }
    else await assert.rejects(inspectImportRecovery(f.root, evidence, now), fault);
  }
});
test("unclaimed permits expire and cannot cross date or be reused by failed executions", async () => {
  const f = await fixture(), permit = await inspectImportRecovery(f.root, evidence, now);
  await publishImportRecovery(permit, evidence, recoverySha(JSON.stringify(permit)));
  for (const [id, at] of [["1", now], ["890", now], ["891", now], ["892", "2026-09-08T02:29:59Z"], ["892", "2026-09-08T03:01:00Z"], ["892", "2026-09-08T16:00:00Z"]]) {
    await assert.rejects(claimImportRecovery(f.root, "890", id, "plan-direct-http", at));
  }
  await writeFile(f.files[0], "changed after permit");
  await assert.rejects(claimImportRecovery(f.root, "890", "892", "plan-direct-http", now));
});
test("stage reuse is explicit and never rewinds importing state or enables incomplete exports", async () => {
  const f = await fixture(), plan = f.plan as JackyunExportFirstPlan, before = JSON.stringify(plan);
  for (const action of ["export-all", "validate"]) {
    assert.throws(() => assertExportFirstAction(plan, "890", action));
    assert.doesNotThrow(() => assertExportFirstAction(plan, "890", action, true));
    assert.throws(() => assertExportFirstAction({ ...plan, exports: {} }, "890", action, true));
  }
  assert.throws(() => assertExportFirstAction(plan, "890", "verify", true));
  assert.equal(JSON.stringify(plan), before);
});

test("actual download runner retries the same failed product contract and reuses uploaded chunks", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jackyun-product-retry-"));
  const downloadDirectory = path.join(root, "downloads"), outputRoot = path.join(root, "runs");
  const filePath = path.join(downloadDirectory, "jackyun", a.runId, "products", "synthetic.xlsx");
  const raw = createXlsxWorkbookBytes([{ name: "货品", rows: [["货品编号", "货品名称", "固定成本价", "基础单位"],
    ...Array.from({ length: 5000 }, (_, index) => [`SKU-${index}`, "合成测试货品", 10, "台"])] }]);
  await mkdir(path.dirname(filePath), { recursive: true }); await writeFile(filePath, raw);
  const rawHash = recoverySha(raw), at = (second: number) => `2026-09-08T01:00:0${second}.000Z`;
  const options: JackyunDownloadRunOptions = { module: "products", filePath, runId: a.runId, outputRoot, downloadDirectory,
    policyVersion: jackyunExportFirstPolicyVersion, exportStart: at(4), expectedSourceRows: 5000, baseUrl: "http://localhost:3000", dryRun: false,
    handoffEvidence: { navigationIntentAt: at(1), queryIntentAt: at(2), tableStableAt: at(3), exportIntentAt: at(4), downloadEventAt: at(5) },
    downloadProvenance: { runId: a.runId, module: "products", policyVersion: jackyunExportFirstPolicyVersion,
      method: "browser_event", downloadId: "synthetic-products", originalFileName: "synthetic.xlsx", completedAt: at(5), sha256: rawHash, bytes: raw.length } };
  const priorFetch = globalThis.fetch, received = new Set<number>(); let fail = true, puts = 0, completes = 0;
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), "http://localhost:3000/api/imports/erp/chunks", "no real network or different domain is allowed");
    if (init?.method === "PUT") { puts++; received.add(Number(new Headers(init.headers).get("x-chunk-index"))); return Response.json({ ok: true }); }
    const payload = JSON.parse(String(init?.body));
    if (payload.action === "init") return Response.json({ upload: { id: "synthetic-upload", receivedChunkIndexes: [...received] } });
    assert.equal(payload.action, "complete"); completes++;
    if (fail) return Response.json({ message: a.error, status: "rejected" }, { status: 503 });
    return Response.json({ ok: true, status: "imported", batch: { id: "synthetic-batch", sourceKey: "products", status: "completed",
      rowCount: 5000, snapshotDate: null, totals: { rawFileHash: rawHash, contentHash: "d".repeat(64) } } });
  };
  try {
    await assert.rejects(runJackyunDownload(options), /Django ERP/);
    const directory = path.join(outputRoot, a.runId), auditRaw = await readFile(path.join(directory, "audit/products.json"));
    const manifest = JSON.parse(await readFile(path.join(directory, "run-manifest.json"), "utf8")), prior = manifest.modules.products;
    const binding = { version: 1 as const, originalExecutionId: "890", failedExecutionId: "891", executionId: "892", permitSha256: "a".repeat(64),
      failedModuleSha256: recoverySha(JSON.stringify(prior)), failedAuditSha256: recoverySha(auditRaw) };
    // Equivalent archive is created by the pipeline's claim gate in production.
    await writeFile(path.join(root, "original-failure.json"), auditRaw, { flag: "wx" });
    const previousPuts = puts;
    await assert.rejects(runJackyunDownload({ ...options, importRecovery: { ...binding, failedModuleSha256: "0".repeat(64) } }));
    assert.equal(completes, 1, "a mismatched recovery cannot reach upload");
    fail = false;
    assert.equal((await runJackyunDownload({ ...options, importRecovery: binding })).status, "completed");
    assert.equal(puts, previousPuts, "already received chunks are not uploaded again");
    assert.equal(completes, 2);
    assert.equal((await runJackyunDownload({ ...options, importRecovery: binding })).status, "duplicate_ignored");
    assert.equal(completes, 2, "local verified completion does not submit another import");
    assert.deepEqual(await readFile(path.join(root, "original-failure.json")), auditRaw);
  } finally { globalThis.fetch = priorFetch; }
});
