import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const gate = path.join(root, "tools", "sales-cutover-snapshot-gate.py");
const rehearsalId = "abcdef123456";
const cutoverId = `rehearsal-${rehearsalId}`;
const hex = (character: string) => character.repeat(64);
const retirementTombstoneViews = [
  "sales_import_upload_chunks",
  "sales_import_uploads",
  "sales_order_lines",
  "sales_import_batches",
  "sales_overview_response_cache",
  "sales_overview_cache_state",
  "sales_projection_outbox",
  "sales_projection_source_state",
  "sales_write_authority",
];
const sharedImportRetirementGuards = [
  "sales_retired_fingerprints_insert_guard",
  "sales_retired_fingerprints_update_guard",
  "sales_retired_fingerprints_delete_guard",
  "sales_retired_attempts_insert_guard",
  "sales_retired_attempts_update_guard",
  "sales_retired_attempts_delete_guard",
  "sales_retired_scope_heads_insert_guard",
  "sales_retired_scope_heads_update_guard",
  "sales_retired_scope_heads_delete_guard",
];

function pythonExecutable(): string {
  for (const candidate of [process.env.PYTHON, "python3", "python"].filter(Boolean) as string[]) {
    const probe = spawnSync(candidate, ["--version"], { encoding: "utf8" });
    if (probe.status === 0) return candidate;
  }
  throw new Error("Python runtime is required for snapshot gate tests");
}

const python = pythonExecutable();

test("live capture reuses the deployed v4 reader without a PostgreSQL command or migration run", async () => {
  const source = await readFile(gate, "utf8");
  assert.match(source, /_complete_source_snapshot/);
  assert.match(source, /_open_source/);
  assert.match(source, /TERUISI_DJANGO_ENVIRONMENT"] = "test"/);
  assert.match(source, /pop\("TERUISI_DJANGO_DATABASE_URL", None\)/);
  assert.match(source, /pop\("TERUISI_DJANGO_ERP_DATABASE_URL", None\)/);
  assert.doesNotMatch(source, /SalesMigrationRun/);
  assert.doesNotMatch(source, /call_command/);
  assert.doesNotMatch(source, /target_connection/);
});

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map(
    (key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`,
  ).join(",")}}`;
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function snapshot() {
  return {
    canonicalFormatVersion: "sales-projection-v4",
    sourceRevision: "8:5",
    sourceCounts: { erp_product_master: 2, sales_order_lines: 3 },
    sourceDigests: { erp_product_master: hex("a"), sales_order_lines: hex("b") },
  };
}

async function fixture() {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "teruisi-snapshot-gate-"));
  const rehearsalRoot = path.join(temporary, "rehearsals", rehearsalId);
  const cutoverAudit = path.join(rehearsalRoot, "audit", "cutover");
  await mkdir(cutoverAudit, { recursive: true });
  const statePath = path.join(cutoverAudit, "state.json");
  const value = snapshot();
  const state = {
    version: "sales-local-cutover-v1",
    cutoverId,
    sourcePathDigest: hex("c"),
    createdAt: "2026-08-29T01:00:00.000Z",
    updatedAt: "2026-08-29T02:00:00.000Z",
    status: "completed",
    steps: [{
      name: "sales_snapshot_dry_run",
      completedAt: "2026-08-29T01:30:00.000Z",
      result: {
        status: "dry_run_completed",
        runId: "d".repeat(32),
        ...value,
      },
    }],
  };
  await writeFile(statePath, `${JSON.stringify(state)}\n`, "utf8");
  const livePath = path.join(temporary, "live.json");
  const backupPath = path.join(temporary, "backup.json");
  await writeFile(livePath, `${JSON.stringify(value)}\n`, "utf8");
  await writeFile(backupPath, `${JSON.stringify(value)}\n`, "utf8");
  return { temporary, rehearsalRoot, statePath, livePath, backupPath, value, state };
}

function verifyEvidence(input: {
  rehearsalRoot: string;
  statePath: string;
  livePath: string;
  backupPath: string;
}) {
  return spawnSync(python, [
    gate,
    "verify-evidence",
    "--live-snapshot", input.livePath,
    "--backup-snapshot", input.backupPath,
    "--rehearsal-state", input.statePath,
    "--rehearsal-root", input.rehearsalRoot,
    "--rehearsal-id", rehearsalId,
  ], { encoding: "utf8" });
}

test("canonical snapshot gate accepts only unchanged live, backup, and rehearsal evidence", async () => {
  const item = await fixture();
  try {
    const result = verifyEvidence(item);
    assert.equal(result.status, 0, result.stderr);
    const payload = JSON.parse(result.stdout);
    assert.equal(payload.status, "verified");
    assert.equal(payload.canonicalFormatVersion, "sales-projection-v4");
    assert.equal(payload.sourceRevision, "8:5");
    assert.match(payload.snapshotSha256, /^[0-9a-f]{64}$/);
  } finally {
    await rm(item.temporary, { recursive: true, force: true });
  }
});

test("same counts and revision with one changed business digest fails closed", async () => {
  const item = await fixture();
  try {
    const changed = snapshot();
    changed.sourceDigests.sales_order_lines = hex("e");
    await writeFile(item.livePath, `${JSON.stringify(changed)}\n`, "utf8");
    const result = verifyEvidence(item);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /实时 D1 canonical snapshot 已偏离成功演练材料/);
  } finally {
    await rm(item.temporary, { recursive: true, force: true });
  }
});

test("missing/duplicate digests and duplicate dry-run steps fail closed", async () => {
  const item = await fixture();
  try {
    const missing = snapshot();
    delete (missing.sourceDigests as Partial<typeof missing.sourceDigests>).sales_order_lines;
    await writeFile(item.livePath, `${JSON.stringify(missing)}\n`, "utf8");
    let result = verifyEvidence(item);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /counts\/digests 字段集合不一致/);

    const value = snapshot();
    const duplicateJson = `{"canonicalFormatVersion":"sales-projection-v4","sourceRevision":"8:5",`
      + `"sourceCounts":${JSON.stringify(value.sourceCounts)},"sourceDigests":{`
      + `"erp_product_master":"${hex("a")}","sales_order_lines":"${hex("b")}",`
      + `"sales_order_lines":"${hex("e")}"}}\n`;
    await writeFile(item.livePath, duplicateJson, "utf8");
    result = verifyEvidence(item);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /证据 JSON 包含重复字段/);

    await writeFile(item.livePath, `${JSON.stringify(snapshot())}\n`, "utf8");
    const duplicateStepState = structuredClone(item.state);
    duplicateStepState.steps.push(structuredClone(duplicateStepState.steps[0]));
    await writeFile(item.statePath, `${JSON.stringify(duplicateStepState)}\n`, "utf8");
    result = verifyEvidence(item);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /rehearsal step 缺失、重复或无效/);
  } finally {
    await rm(item.temporary, { recursive: true, force: true });
  }
});

test("retirement audit must self-hash and match every rehearsal result identity", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "teruisi-retirement-gate-"));
  try {
    const auditPath = path.join(temporary, "sales-retirement.json");
    const preservedEvidence = { schemaObjectCount: 7, schemaSha256: hex("1") };
    const preservedSha = sha256(canonicalJson(preservedEvidence));
    const attestationSha = hex("2");
    const smokeSha = hex("3");
    const planId = hex("4");
    const core = {
      version: "sales-d1-retirement-v4",
      cutoverId,
      sourcePathSha256: hex("5"),
      auditOutputPathSha256: sha256(path.resolve(auditPath)),
      approvedPlanId: planId,
      sourceCoreEvidenceSha256: hex("7"),
      recordedAt: "2026-08-29T02:00:00.000Z",
      attestation: { payloadSha256: attestationSha },
      smokeReceipt: { fileSha256: smokeSha },
      postgresqlPreflight: {
        status: "verified",
        planId,
        cutoverId,
        attestationPayloadSha256: attestationSha,
        smokeReceiptSha256: smokeSha,
      },
      migration: {},
      authority: { cutoverId },
      retiredEvidence: {},
      preservedEvidence,
      result: {
        retiredTablesAbsent: retirementTombstoneViews,
        retirementTombstoneViewsPresent: retirementTombstoneViews,
        retiredTriggersAbsent: [],
        sharedSalesRowsDeleted: [],
        sharedImportRetirementGuardsPresent: sharedImportRetirementGuards,
        preservedEvidenceSha256: preservedSha,
      },
    };
    const auditId = sha256(canonicalJson(core));
    await writeFile(auditPath, `${JSON.stringify({ ...core, auditId })}\n`, "utf8");
    const arguments_ = [
      gate,
      "verify-retirement-audit",
      "--audit", auditPath,
      "--rehearsal-id", rehearsalId,
      "--audit-id", auditId,
      "--preserved-evidence-sha256", preservedSha,
      "--attestation-payload-sha256", attestationSha,
      "--smoke-receipt-sha256", smokeSha,
    ];
    const result = spawnSync(python, arguments_, { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).auditId, auditId);

    const assertMismatch = (flag: string, value: string) => {
      const changed = [...arguments_];
      changed[changed.indexOf(flag) + 1] = value;
      const rejected = spawnSync(python, changed, { encoding: "utf8" });
      assert.notEqual(rejected.status, 0, `${flag} mismatch unexpectedly passed`);
      assert.match(rejected.stderr, /retirement audit 与 result 终态证据不一致/);
    };
    assertMismatch("--audit-id", hex("8"));
    assertMismatch("--preserved-evidence-sha256", hex("9"));
    assertMismatch("--attestation-payload-sha256", hex("a"));
    assertMismatch("--smoke-receipt-sha256", hex("b"));

    const legacyCore = { ...core, result: { preservedEvidenceSha256: preservedSha } };
    const legacyAuditId = sha256(canonicalJson(legacyCore));
    await writeFile(auditPath, `${JSON.stringify({ ...legacyCore, auditId: legacyAuditId })}\n`, "utf8");
    const legacyArguments = [...arguments_];
    legacyArguments[legacyArguments.indexOf("--audit-id") + 1] = legacyAuditId;
    const legacy = spawnSync(python, legacyArguments, { encoding: "utf8" });
    assert.notEqual(legacy.status, 0, "v3-shaped audit relabeled as v4 unexpectedly passed");
    assert.match(legacy.stderr, /rehearsal retirement audit result 字段集合无效/);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});
