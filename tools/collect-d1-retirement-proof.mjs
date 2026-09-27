// One-time adoption only; never imported by the launch guard or supervisor.
import { DatabaseSync } from "node:sqlite";
import { lstat, readFile, realpath } from "node:fs/promises";
import path from "node:path";
import { canonicalProofJson, d1ProofVersion, d1RetiredDomains, proofBytesHash, proofHash, sealProof, validateD1RetirementProof } from "./d1-retirement-proof.mjs";

export const retirementMigrations = Object.freeze([
  "0092_sales_domain_retirement.sql", "0093_finance_write_authority.sql", "0096_netshop_domain_retirement.sql",
  "0098_market_domain_retirement.sql", "0100_product_domain_retirement.sql", "0102_inventory_domain_retirement.sql",
  "0104_workflow_launch_domain_retirement.sql", "0106_workflow_operations_domain_retirement.sql",
  "0108_customer_service_domain_retirement.sql", "0110_erp_reference_domain_retirement.sql",
  "0112_access_control_domain_retirement.sql", "0114_ai_domain_retirement.sql",
]);
const receiptHashFields = ["attestation_sha256", "smoke_receipt_sha256", "preflight_evidence_sha256", "migration_sha256", "preserved_evidence_sha256"];
const hex = /^[0-9a-f]{64}$/;
export const normalizeRetirementSql = (sql) => sql.replace(/\bIF\s+NOT\s+EXISTS\s+/gi, "").trim().replace(/;\s*$/, "").replace(/\s+/g, " ");
export function retirementReceiptGuardContracts(migrations) {
  const names = ["domain_retirement_receipts_insert_guard", "domain_retirement_receipts_transition_guard", "domain_retirement_receipts_no_delete"];
  const contracts = new Map(names.map((name) => [name, new Set()]));
  for (const migration of migrations) {
    for (const statement of migration.toString("utf8").split(/--> statement-breakpoint\s*/)) {
      const definition = statement.replace(/^(?:\s*--[^\n]*(?:\n|$))*/, "").trim();
      const match = /^CREATE\s+TRIGGER\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"]?([A-Za-z_][A-Za-z0-9_]*)[`"]?/i.exec(definition);
      if (match && contracts.has(match[1])) contracts.get(match[1]).add(normalizeRetirementSql(definition));
    }
  }
  if ([...contracts.values()].some((variants) => variants.size === 0)) throw new Error("Missing immutable receipt guard contract");
  return contracts;
}
export function expectedRetirementObjects(sql) {
  const objects = [];
  for (const statement of sql.split(/--> statement-breakpoint\s*/)) {
    const definition = statement.replace(/^(?:\s*--[^\n]*(?:\n|$))*/, "").trim();
    const match = /^CREATE\s+(VIEW|TRIGGER)\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"]?([A-Za-z_][A-Za-z0-9_]*)[`"]?/i.exec(definition);
    if (!match || match[2].startsWith("domain_retirement_receipts_")) continue;
    // Preflight objects are explicitly dropped in the same operator migration.
    if (new RegExp(`DROP\\s+${match[1]}\\s+(?:IF\\s+EXISTS\\s+)?[\\x60"]?${match[2]}[\\x60"]?\\s*;`, "i").test(sql.slice(sql.indexOf(definition) + definition.length))) continue;
    objects.push({ type: match[1].toLowerCase(), name: match[2], sql: normalizeRetirementSql(definition) });
  }
  return objects.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
}
export function finalRetirementContracts(migrations) {
  const texts = migrations.map((value) => value.toString("utf8"));
  const contracts = texts.map(expectedRetirementObjects);
  return contracts.map((objects, index) => {
    const final = new Map();
    for (let expected of objects) {
      for (let later = index + 1; later < contracts.length; later++) {
        const replacement = contracts[later].find((item) => item.name === expected.name);
        if (replacement) expected = replacement;
        if (expected.type !== "trigger") continue;
        const target = /\bON\s+[`"]?([A-Za-z_][A-Za-z0-9_]*)/.exec(expected.sql)?.[1];
        const view = contracts[later].find((item) => item.type === "view" && item.name === target);
        if (view && new RegExp(`DROP\\s+(?:TABLE|VIEW)\\s+(?:IF\\s+EXISTS\\s+)?[\\x60"]?${target}[\\x60"]?\\s*;`, "i").test(texts[later])) {
          // Later domains can retire a formerly shared table (ERP batches,
          // workflow records). Its exact terminal view supersedes old guards.
          expected = view;
        }
      }
      final.set(expected.name, expected);
    }
    return [...final.values()].sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
  });
}
async function safeRead(target, maxBytes = 16 * 1024 * 1024) {
  const absolute = path.resolve(target);
  let cursor = path.parse(absolute).root;
  for (const part of absolute.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    if ((await lstat(cursor)).isSymbolicLink()) throw new Error("Retirement adoption source contains a reparse point");
  }
  const before = await lstat(absolute);
  if (!before.isFile() || before.size > maxBytes || path.resolve(await realpath(absolute)).toLowerCase() !== absolute.toLowerCase()) {
    throw new Error("Retirement adoption source is not an ordinary bounded file");
  }
  const raw = await readFile(absolute);
  const after = await lstat(absolute);
  if (before.ino !== after.ino || before.size !== after.size || before.mtimeMs !== after.mtimeMs) throw new Error("Retirement source changed while reading");
  return raw;
}
export async function inspectGlobalD1Retirement(sourceD1Path, sourceRoot) {
  // The source path is derived from the verified predecessor, never a CLI override.
  const absolute = path.resolve(sourceD1Path);
  let cursor = path.parse(absolute).root;
  for (const part of absolute.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    if ((await lstat(cursor)).isSymbolicLink()) throw new Error("D1 adoption source contains a reparse point");
  }
  if (!(await lstat(absolute)).isFile()) throw new Error("D1 adoption source is not a file");
  const migrations = await Promise.all(retirementMigrations.map((name) => safeRead(path.join(sourceRoot, "drizzle", name))));
  const contracts = finalRetirementContracts(migrations);
  const approvedTriggers = new Set(contracts.flat().filter((item) => item.type === "trigger").map((item) => item.name));
  const db = new DatabaseSync(absolute, { readOnly: true });
  try {
    db.exec("PRAGMA query_only=ON; BEGIN");
    const schema = db.prepare("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name LIMIT 5001").all();
    if (schema.length > 5000) throw new Error("D1 adoption schema exceeds audit bound");
    const receiptGuards = retirementReceiptGuardContracts(migrations);
    for (const [name, variants] of receiptGuards) {
      const actual = schema.find((row) => row.name === name);
      if (!actual || actual.type !== "trigger" || actual.tbl_name !== "domain_retirement_receipts" || !variants.has(normalizeRetirementSql(actual.sql))) {
        throw new Error(`D1 immutable receipt guard mismatch: ${name}`);
      }
    }
    if (schema.some((row) => row.type === "trigger" && row.tbl_name === "domain_retirement_receipts" && !receiptGuards.has(row.name))) {
      throw new Error("D1 retirement receipts have an unapproved trigger");
    }
    const domains = d1RetiredDomains.map((domain, index) => {
      let migrationSha256 = proofBytesHash(migrations[index]);
      // Historical Windows operators used both LF and CRLF checkouts. Accept
      // only these exact byte variants; retain the applied receipt's digest.
      const lfMigration = migrations[index].toString("utf8").replaceAll("\r\n", "\n");
      const migrationDigests = new Set([migrationSha256, proofBytesHash(lfMigration), proofBytesHash(lfMigration.replaceAll("\n", "\r\n"))]);
      const objects = contracts[index];
      if (!objects.length) throw new Error(`No terminal object contract for ${domain}`);
      for (const expected of objects) {
        const actual = schema.find((row) => row.name === expected.name);
        if (!actual || actual.type !== expected.type || normalizeRetirementSql(actual.sql) !== expected.sql) {
          throw new Error(`D1 terminal object mismatch: ${domain}/${expected.name}`);
        }
        if (expected.type === "view" && db.prepare(`SELECT COUNT(*) AS n FROM "${expected.name}"`).get().n !== 0) {
          throw new Error(`D1 tombstone is not empty: ${domain}/${expected.name}`);
        }
        if (expected.type === "view" && schema.some((row) => row.type === "trigger" && row.tbl_name === expected.name && !approvedTriggers.has(row.name))) {
          throw new Error(`D1 tombstone has an unapproved write trigger: ${domain}/${expected.name}`);
        }
      }
      let receipt;
      if (domain === "finance") {
        const rows = db.prepare("SELECT id,owner,epoch,cutover_id FROM finance_write_authority LIMIT 2").all();
        if (rows.length !== 1 || rows[0].id !== 1 || rows[0].owner !== "postgresql") throw new Error("Finance D1 authority is not terminal PostgreSQL");
        receipt = rows[0];
      } else {
        const rows = db.prepare("SELECT * FROM domain_retirement_receipts WHERE domain=? LIMIT 2").all(domain);
        if (rows.length !== 1 || rows[0].status !== "completed" || !rows[0].completed_at
            || rows[0].version !== `${domain}-domain-retirement-receipt-v1`
            || !migrationDigests.has(rows[0].migration_sha256) || receiptHashFields.some((key) => !hex.test(rows[0][key] ?? ""))) {
          throw new Error(`D1 terminal receipt mismatch: ${domain}`);
        }
        receipt = rows[0];
        migrationSha256 = receipt.migration_sha256;
      }
      return { domain, cutoverId: receipt.cutover_id, migrationSha256, objectsSha256: proofHash(objects), receiptSha256: proofHash(receipt),
        viewCount: objects.filter((row) => row.type === "view").length, guardCount: objects.filter((row) => row.type === "trigger").length };
    });
    return { domains, sourceSchemaSha256: proofHash(schema) };
  } finally { db.close(); }
}
const postgresDomains = [
  [8001, "", null], [8011, "finance", null], [8021, "netshop", null], [8031, "market", null],
  [8041, "products", null], [8051, "inventory", null], [8061, "workflow", null], [8071, "customerService", null],
  [8091, "erpReference", null], [8101, "accessControl", null], [8111, "ai", "ai"], [8081, "bi", "readonly"],
];
const postgresEvidenceFiles = ["app/deployment.json", "service.json", "netshop-service-enabled.json", "market-service-enabled.json",
  "products-service-enabled.json", "inventory-service-enabled.json", "workflow-service-enabled.json", "customer-service-enabled.json",
  "erp-reference-enabled.json", "access-control-enabled.json", "ai-enabled.json", "bi-service-enabled.json"];
export async function inspectPostgresRetirementReadiness(runtimeRoot, { fetchImpl = fetch } = {}) {
  const filesBefore = await Promise.all(postgresEvidenceFiles.map(async (file) => ({ file, sha256: proofBytesHash(await safeRead(path.join(runtimeRoot, file))) })));
  for (const [port, field, kind] of postgresDomains) {
    for (const [offset, role] of (kind === "readonly" ? [[0, "reader"]] : [[0, "reader"], [1, "writer"]])) {
      const response = await fetchImpl(`http://127.0.0.1:${port + offset}/health/ready`, {
        method: "GET", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(4000),
      });
      if (response.status !== 200 || !response.headers.get("content-type")?.startsWith("application/json")) throw new Error("PostgreSQL adoption readiness failed");
      const reader = response.body?.getReader();
      if (!reader) throw new Error("PostgreSQL adoption readiness body missing");
      const chunks = []; let bytes = 0;
      try {
        while (true) { const chunk = await reader.read(); if (chunk.done) break; bytes += chunk.value.byteLength;
          if (bytes > 16384) throw new Error("PostgreSQL readiness response too large"); chunks.push(Buffer.from(chunk.value)); }
      } finally { await reader.cancel().catch(() => {}); }
      const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      const roleField = field ? `${field}${role === "reader" ? "Reader" : "Writer"}` : role;
      if (payload.status !== "ready" || payload.service !== "teruisi-django" || payload.database !== "ready"
          || (kind === "ai" ? payload.processRole !== `ai_${role}` || payload.authority !== "postgres" : payload[roleField] !== "ready")) {
        throw new Error("PostgreSQL adoption service role or authority mismatch");
      }
    }
  }
  const filesAfter = await Promise.all(postgresEvidenceFiles.map(async (file) => ({ file, sha256: proofBytesHash(await safeRead(path.join(runtimeRoot, file))) })));
  if (canonicalProofJson(filesBefore) !== canonicalProofJson(filesAfter)) throw new Error("PostgreSQL deployment changed during retirement adoption");
  return proofHash(filesAfter);
}
export async function collectD1RetirementProof({ chain, sourceRoot, djangoRuntimeRoot, retainedEvidence, now = new Date() }) {
  const predecessor = chain.headManifest;
  const snapshot = await inspectGlobalD1Retirement(predecessor.runtime.sourceD1Path, sourceRoot);
  const postgresEvidenceSha256 = await inspectPostgresRetirementReadiness(djangoRuntimeRoot);
  const proof = sealProof({ version: d1ProofVersion, verifiedAt: now.toISOString(),
    runtimeRootPathSha256: predecessor.runtime.runtimeRootPathSha256, sourceD1PathSha256: predecessor.runtime.sourceD1PathSha256,
    persistRootPathSha256: predecessor.runtime.persistRootPathSha256, bootstrapAuthoritySha256: chain.bootstrap.authoritySha256,
    adoptionPredecessorManifestSha256: chain.head.manifestSha256, ...snapshot,
    postgresEvidenceSha256, retainedEvidenceSha256: proofHash(retainedEvidence) }, "proofSha256");
  return validateD1RetirementProof(proof);
}
export function sameAdoptionEvidence(first, second) {
  const stable = (proof) => Object.fromEntries(Object.entries(proof).filter(([key]) => !["verifiedAt", "proofSha256"].includes(key)));
  return canonicalProofJson(stable(first)) === canonicalProofJson(stable(second));
}
