import assert from "node:assert/strict";
import { link, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { canonicalProofJson, createD1RetirementReceipt, d1ReceiptRelativePath, d1ReceiptVersion,
  d1RetiredDomains, proofBytesHash, readD1RetirementReceipt, sealProof, validateD1RetirementProof } from "../tools/d1-retirement-proof.mjs";
import { finalRetirementContracts, inspectGlobalD1Retirement, inspectPostgresRetirementReadiness,
  retirementMigrations, retirementReceiptGuardContracts } from "../tools/collect-d1-retirement-proof.mjs";
import { syntheticD1Proof } from "./fixtures/d1-retirement-proof";

const bindings = Object.fromEntries(["runtimeRootPathSha256", "sourceD1PathSha256", "persistRootPathSha256",
  "bootstrapAuthoritySha256", "adoptionPredecessorManifestSha256"].map((key, index) => [key, String(index + 1).repeat(64)]));
async function sandbox(t: { after: (fn: () => Promise<void>) => void }) {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-d1-proof-test-"));
  t.after(async () => {
    assert.equal(path.dirname(root).toLowerCase(), path.resolve(tmpdir()).toLowerCase());
    assert.ok(path.basename(root).startsWith("teruisi-d1-proof-test-"));
    await rm(root, { recursive: true, force: true });
  });
  return root;
}

test("proof requires exact complete domain evidence and immutable bootstrap identity", () => {
  const proof = syntheticD1Proof(bindings);
  assert.equal(validateD1RetirementProof(proof), proof);
  for (const edit of [
    { domains: proof.domains.slice(1) }, { domains: [...proof.domains].reverse() },
    { extra: true }, { verifiedAt: "invalid" }, { sourceSchemaSha256: "invalid" }, { sourceSchemaSha256: ["a".repeat(64)] },
    { domains: proof.domains.map((row: { domain: string }) => ({ ...row, guardCount: -1 })) },
  ]) {
    const core = { ...proof };
    delete core.proofSha256;
    assert.throws(() => validateD1RetirementProof(sealProof({ ...core, ...edit }, "proofSha256")));
  }
  assert.throws(() => validateD1RetirementProof(proof, { bootstrapAuthoritySha256: "f".repeat(64) }), /binding/);
  assert.throws(() => validateD1RetirementProof({ ...proof, sourceSchemaSha256: "f".repeat(64) }), /digest/);
});

test("receipt validates without any D1 file and rejects replay, tampering, hardlinks and noncanonical bytes", async (t) => {
  const root = await sandbox(t);
  const manifest = { releaseId: "20260906T000000Z-1234567890abcdef", runtime: bindings,
    source: { sourceFingerprint: "a".repeat(64) }, build: { buildFingerprint: "b".repeat(64) }, artifacts: {} };
  const receipt = createD1RetirementReceipt(syntheticD1Proof(bindings), manifest);
  const target = path.join(root, ...d1ReceiptRelativePath.split("/"));
  await mkdir(path.dirname(target));
  async function save(raw = `${canonicalProofJson(receipt)}\n`) {
    await writeFile(target, raw);
    Object.assign(manifest.artifacts, { d1RetirementReceipt: {
      version: d1ReceiptVersion, relativePath: d1ReceiptRelativePath, sha256: proofBytesHash(raw),
    } });
  }
  await save();
  assert.equal((await readD1RetirementReceipt(manifest, root)).proofSha256, receipt.proof.proofSha256);
  await assert.rejects(readD1RetirementReceipt({ ...manifest, releaseId: "20260906T000001Z-1234567890abcdef" }, root), /release mismatch/);
  await assert.rejects(readD1RetirementReceipt({ ...manifest, runtime: { ...bindings, runtimeRootPathSha256: "f".repeat(64) } }, root), /binding/);
  await writeFile(target, "{}");
  await assert.rejects(readD1RetirementReceipt(manifest, root), /file digest/);
  await save(JSON.stringify(receipt, null, 2));
  await assert.rejects(readD1RetirementReceipt(manifest, root), /noncanonical/);
  await save();
  await link(target, path.join(root, "linked.json"));
  await assert.rejects(readD1RetirementReceipt(manifest, root), /invalid file/);
  await rm(target);
  await assert.rejects(readD1RetirementReceipt(manifest, root), /ENOENT/);
  await assert.rejects(readD1RetirementReceipt({ ...manifest, artifacts: {} }, root), /invalid fields/);
});

async function databaseFixture(root: string, crlf = false) {
  const sourceRoot = path.resolve(".");
  const migrations = await Promise.all(retirementMigrations.map((name: string) => readFile(path.join(sourceRoot, "drizzle", name))));
  const contracts = finalRetirementContracts(migrations);
  type TerminalObject = { name: string; sql: string; type: string };
  const objects = new Map<string, TerminalObject>(contracts.flat().map((item: TerminalObject) => [item.name, item] as const));
  const target = path.join(root, "fixture.sqlite");
  const db = new DatabaseSync(target);
  const columns = new Set(["id", "owner", "epoch", "cutover_id"]);
  for (const item of objects.values()) for (const match of item.sql.matchAll(/\b(?:NEW|OLD)\.([a-z_][a-z0-9_]*)/gi)) columns.add(match[1]);
  const tables = new Set(["finance_write_authority"]);
  for (const item of objects.values()) {
    const table = item.type === "trigger" ? /\bON\s+[`"]?([A-Za-z_][A-Za-z0-9_]*)/.exec(item.sql)?.[1] : null;
    if (table && !objects.has(table)) tables.add(table);
  }
  for (const table of tables) db.exec(`CREATE TABLE "${table}" (${[...columns].map((name) => `"${name}"`).join(",")})`);
  db.exec("INSERT INTO finance_write_authority(id,owner,epoch,cutover_id) VALUES(1,'postgresql',1,'finance-fixture-cutover')");
  const receiptFields = ["domain", "version", "status", "completed_at", "cutover_id", "attestation_sha256", "smoke_receipt_sha256",
    "preflight_evidence_sha256", "migration_sha256", "preserved_evidence_sha256"];
  db.exec(`CREATE TABLE domain_retirement_receipts (${[...receiptFields, "plan_id", "audit_id", "created_at"].join(",")})`);
  const insert = db.prepare(`INSERT INTO domain_retirement_receipts (${receiptFields.join(",")}) VALUES(${receiptFields.map(() => "?").join(",")})`);
  d1RetiredDomains.forEach((domain: string, index: number) => {
    if (domain === "finance") return;
    const raw = crlf ? migrations[index].toString("utf8").replaceAll("\r\n", "\n").replaceAll("\n", "\r\n") : migrations[index];
    insert.run(domain, `${domain}-domain-retirement-receipt-v1`, "completed", "2026-09-06T00:00:00Z", `${domain}-fixture-cutover`,
      "1".repeat(64), "2".repeat(64), "3".repeat(64), proofBytesHash(raw), "4".repeat(64));
  });
  const receiptGuards: Map<string, Set<string>> = retirementReceiptGuardContracts(migrations);
  for (const variants of receiptGuards.values()) db.exec([...variants][0]);
  for (const type of ["view", "trigger"]) for (const item of objects.values()) if (item.type === type) db.exec(item.sql);
  function corruptReceipt(statement: string) {
    db.exec("DROP TRIGGER domain_retirement_receipts_transition_guard");
    db.exec(statement);
    db.exec([...receiptGuards.get("domain_retirement_receipts_transition_guard")!][0]);
  }
  return { db, target, sourceRoot, contracts, receiptGuards, corruptReceipt };
}

test("collector verifies all terminal schemas including superseded shared tables and historical CRLF receipts", async (t) => {
  const fixture = await databaseFixture(await sandbox(t), true);
  try {
    const result = await inspectGlobalD1Retirement(fixture.target, fixture.sourceRoot);
    assert.deepEqual(result.domains.map((row: { viewCount: number; guardCount: number }) => [row.viewCount, row.guardCount]),
      [[9,9],[0,42],[15,9],[49,9],[3,18],[7,21],[2,0],[14,42],[5,18],[7,18],[2,6],[40,120]]);
    assert.throws(() => fixture.db.exec("UPDATE domain_retirement_receipts SET status='approved' WHERE domain='ai-assistant'"), /receipt_update_forbidden/);
    assert.throws(() => fixture.db.exec("DELETE FROM domain_retirement_receipts WHERE domain='ai-assistant'"), /receipt_delete_forbidden/);
    fixture.corruptReceipt("UPDATE domain_retirement_receipts SET status='pending' WHERE domain='ai-assistant'");
    await assert.rejects(inspectGlobalD1Retirement(fixture.target, fixture.sourceRoot), /receipt mismatch: ai-assistant/);
    fixture.corruptReceipt("UPDATE domain_retirement_receipts SET status='completed',migration_sha256='invalid' WHERE domain='ai-assistant'");
    await assert.rejects(inspectGlobalD1Retirement(fixture.target, fixture.sourceRoot), /receipt mismatch/);
  } finally { fixture.db.close(); }
});

test("collector rejects missing, weakened and unexpected shared receipt guards", async (t) => {
  const fixture = await databaseFixture(await sandbox(t));
  try {
    for (const [name, variants] of fixture.receiptGuards) {
      fixture.db.exec(`DROP TRIGGER "${name}"`);
      await assert.rejects(inspectGlobalD1Retirement(fixture.target, fixture.sourceRoot), /immutable receipt guard mismatch/);
      fixture.db.exec(`CREATE TRIGGER "${name}" BEFORE UPDATE ON domain_retirement_receipts BEGIN SELECT 1; END`);
      await assert.rejects(inspectGlobalD1Retirement(fixture.target, fixture.sourceRoot), /immutable receipt guard mismatch/);
      fixture.db.exec(`DROP TRIGGER "${name}"`);
      for (const variant of variants) {
        fixture.db.exec(variant);
        await inspectGlobalD1Retirement(fixture.target, fixture.sourceRoot);
        fixture.db.exec(`DROP TRIGGER "${name}"`);
      }
      fixture.db.exec([...variants][0]);
    }
    fixture.db.exec("CREATE TRIGGER unexpected_receipt_update BEFORE UPDATE ON domain_retirement_receipts BEGIN SELECT 1; END");
    await assert.rejects(inspectGlobalD1Retirement(fixture.target, fixture.sourceRoot), /unapproved trigger/);
  } finally { fixture.db.close(); }
});

test("collector rejects missing guards and additional tombstone write paths", async (t) => {
  const fixture = await databaseFixture(await sandbox(t));
  try {
    const guard = fixture.contracts[0].find((item: { type: string }) => item.type === "trigger");
    fixture.db.exec(`DROP TRIGGER "${guard.name}"`);
    await assert.rejects(inspectGlobalD1Retirement(fixture.target, fixture.sourceRoot), /terminal object mismatch/);
    fixture.db.exec(guard.sql);
    const view = fixture.contracts[0].find((item: { type: string }) => item.type === "view");
    fixture.db.exec(`CREATE TRIGGER unexpected_write INSTEAD OF INSERT ON "${view.name}" BEGIN SELECT 1; END`);
    await assert.rejects(inspectGlobalD1Retirement(fixture.target, fixture.sourceRoot), /unapproved write trigger/);
  } finally { fixture.db.close(); }
});

const evidenceFiles = ["app/deployment.json", "service.json", "netshop-service-enabled.json", "market-service-enabled.json",
  "products-service-enabled.json", "inventory-service-enabled.json", "workflow-service-enabled.json", "customer-service-enabled.json",
  "erp-reference-enabled.json", "access-control-enabled.json", "ai-enabled.json", "bi-service-enabled.json"];
test("adoption readiness checks all 23 identities and rejects changes to protected deployment evidence", async (t) => {
  const root = await sandbox(t);
  await mkdir(path.join(root, "app"));
  for (const file of evidenceFiles) await writeFile(path.join(root, file), "{}");
  const fields: Record<string, string> = { "800": "", "801": "finance", "802": "netshop", "803": "market", "804": "products",
    "805": "inventory", "806": "workflow", "807": "customerService", "808": "bi", "809": "erpReference", "810": "accessControl" };
  let calls = 0;
  const ready: typeof fetch = async (url) => {
    calls++;
    const port = new URL(String(url)).port, role = port.endsWith("1") ? "reader" : "writer", field = fields[port.slice(0,3)];
    return Response.json({ status: "ready", service: "teruisi-django", database: "ready",
      ...(port.startsWith("811") ? { processRole: `ai_${role}`, authority: "postgres" }
        : { [field ? `${field}${role[0].toUpperCase()}${role.slice(1)}` : role]: "ready" }) });
  };
  assert.match(await inspectPostgresRetirementReadiness(root, { fetchImpl: ready }), /^[a-f0-9]{64}$/);
  assert.equal(calls, 23);
  await assert.rejects(inspectPostgresRetirementReadiness(root, { fetchImpl: async () => Response.json({ status: "ready" }) }), /role or authority/);
  await assert.rejects(inspectPostgresRetirementReadiness(root, { fetchImpl: async (url) => {
    if (String(url).includes(":8111/")) return Response.json({ status: "ready", service: "teruisi-django", database: "ready", processRole: "ai_writer", authority: "postgres" });
    return ready(url);
  } }), /role or authority/);
  await assert.rejects(inspectPostgresRetirementReadiness(root, { fetchImpl: async (url) => {
    await writeFile(path.join(root, "service.json"), "{\"changed\":true}"); return ready(url);
  } }), /deployment changed/);
  await rm(path.join(root, "bi-service-enabled.json"));
  await assert.rejects(inspectPostgresRetirementReadiness(root, { fetchImpl: ready }), /ENOENT/);
});
