import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  open,
  readFile,
  realpath,
  rename,
  rm,
  stat,
} from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";
import { DatabaseSync } from "node:sqlite";

import {
  inspectSalesD1WriteAuthority,
  type SalesD1AuthoritySnapshot,
// @ts-expect-error Node 24's native TypeScript loader requires the deployed .ts extension.
} from "./sales-d1-write-authority.ts";
// @ts-expect-error Node 24's native TypeScript loader requires the deployed .ts extension.
import { salesD1CoreEvidence } from "./sales-legacy-r2-cleanup.ts";

const RETIREMENT_VERSION = "sales-d1-retirement-v4" as const;
const RETIREMENT_PLAN_VERSION = "sales-d1-retirement-plan-v1" as const;
const RETIREMENT_RECEIPT_VERSION = "sales-domain-retirement-receipt-v1" as const;
const POSTGRESQL_PREFLIGHT_VERSION = "sales-retirement-preflight-v1" as const;
export const SALES_CUTOVER_ATTESTATION_VERSION = "sales-cutover-attestation-v2" as const;
const RETIREMENT_MIGRATION_FILE = "0092_sales_domain_retirement.sql";
export const RETIREMENT_MIGRATION_SHA256 =
  "f981a62efd0515a7f64dd9f174151b8cfeb0c4b071d8236c481b5459761a3b8f";
const AUTHORITY_SCHEMA_SHA256 =
  "8a0896d9f6b20c2b39eae8cbf1ab39faa21d4cb772e8990d40902e8b86d8af17";
const AUTHORITY_TRIGGER_COUNT = 36;
const POSTGRESQL_PREFLIGHT_MAX_TTL_MS = 10 * 60 * 1000;
const POSTGRESQL_PREFLIGHT_CLOCK_SKEW_MS = 30 * 1000;
const MANAGED_RUNTIME_ROOT = "D:\\teruisi-runtime\\django-sales";
const DEPLOYMENT_FINGERPRINT_ALGORITHM = "relative-path-file-sha256-ordinal-v2";

const POSTGRESQL_PREFLIGHT_CHECKS = [
  "writer_readiness",
  "sales_summary",
  "sales_category_analysis",
  "sales_category_analysis_detail",
  "sales_write_transaction_rollback_probe",
] as const;

const RETIREMENT_RECEIPT_TABLE = "domain_retirement_receipts";
const RETIREMENT_RECEIPT_SCHEMA_OBJECTS = [
  RETIREMENT_RECEIPT_TABLE,
  "domain_retirement_receipts_insert_guard",
  "domain_retirement_receipts_transition_guard",
  "domain_retirement_receipts_no_delete",
] as const;

export const RETIRED_SALES_TABLES = [
  "sales_import_upload_chunks",
  "sales_import_uploads",
  "sales_order_lines",
  "sales_import_batches",
  "sales_overview_response_cache",
  "sales_overview_cache_state",
  "sales_projection_outbox",
  "sales_projection_source_state",
  "sales_write_authority",
] as const;

export const RETIREMENT_TOMBSTONE_VIEWS = [...RETIRED_SALES_TABLES] as const;
const RETIREMENT_TOMBSTONE_VALUE = "sales-domain-retired-v1" as const;

const RETIRED_SALES_TRIGGERS = [
  "sales_authority_singleton_insert_guard",
  "sales_authority_singleton_delete_guard",
  "sales_authority_transition_guard",
  "sales_authority_order_lines_insert",
  "sales_authority_order_lines_update",
  "sales_authority_order_lines_delete",
  "sales_authority_batches_insert",
  "sales_authority_batches_update",
  "sales_authority_batches_delete",
  "sales_authority_uploads_insert",
  "sales_authority_uploads_update",
  "sales_authority_uploads_delete",
  "sales_authority_upload_chunks_insert",
  "sales_authority_upload_chunks_update",
  "sales_authority_upload_chunks_delete",
  "sales_authority_cache_insert",
  "sales_authority_cache_update",
  "sales_authority_cache_delete",
  "sales_authority_revision_update",
  "sales_authority_revision_insert",
  "sales_authority_revision_delete",
  "sales_authority_source_state_insert",
  "sales_authority_source_state_update",
  "sales_authority_source_state_delete",
  "sales_authority_outbox_insert",
  "sales_authority_outbox_update",
  "sales_authority_outbox_delete",
  "sales_authority_fingerprints_insert",
  "sales_authority_fingerprints_update",
  "sales_authority_fingerprints_delete",
  "sales_authority_attempts_insert",
  "sales_authority_attempts_update",
  "sales_authority_attempts_delete",
  "sales_authority_scope_heads_insert",
  "sales_authority_scope_heads_update",
  "sales_authority_scope_heads_delete",
  "market_monthly_summary_sales_insert",
  "market_monthly_summary_sales_update",
  "market_monthly_summary_sales_delete",
] as const;

const SHARED_IMPORT_TABLES = [
  "import_content_fingerprints",
  "import_content_attempts",
  "import_scope_heads",
] as const;

const SHARED_IMPORT_AUTHORITY_TRIGGERS = [
  "sales_authority_fingerprints_insert",
  "sales_authority_fingerprints_update",
  "sales_authority_fingerprints_delete",
  "sales_authority_attempts_insert",
  "sales_authority_attempts_update",
  "sales_authority_attempts_delete",
  "sales_authority_scope_heads_insert",
  "sales_authority_scope_heads_update",
  "sales_authority_scope_heads_delete",
] as const;

export const SHARED_IMPORT_RETIREMENT_GUARDS = [
  "sales_retired_fingerprints_insert_guard",
  "sales_retired_fingerprints_update_guard",
  "sales_retired_fingerprints_delete_guard",
  "sales_retired_attempts_insert_guard",
  "sales_retired_attempts_update_guard",
  "sales_retired_attempts_delete_guard",
  "sales_retired_scope_heads_insert_guard",
  "sales_retired_scope_heads_update_guard",
  "sales_retired_scope_heads_delete_guard",
] as const;

const ERP_DATA_TABLES = [
  "erp_product_master",
  "erp_reference_import_batches",
  "erp_reference_projection_source_state",
  "erp_product_projection_state",
  "erp_reference_projection_outbox",
] as const;

const REQUIRED_ERP_SCHEMA_OBJECTS = [
  "erp_product_master",
  "erp_reference_projection_source_state",
  "erp_product_projection_state",
  "erp_reference_projection_outbox",
  "erp_reference_projection_outbox_event_id_uq",
  "erp_reference_projection_source_no_update",
  "erp_reference_projection_source_no_delete",
  "erp_product_projection_state_guard",
  "erp_product_projection_state_no_delete",
  "erp_reference_projection_outbox_guard",
  "erp_reference_projection_outbox_no_update",
  "erp_reference_projection_outbox_no_delete",
  "erp_product_import_requires_projection_event",
] as const;

const ERP_BRIDGE_SCHEMA_OBJECTS = REQUIRED_ERP_SCHEMA_OBJECTS.filter(
  (name) => name !== "erp_product_master",
);
const ERP_BRIDGE_SCHEMA_SHA256 =
  "dd201a4d13d5279b70acfde4eb80a4b4bfa3a28dfede1e9597cfa4e99ffbea9e";

const ATTESTATION_BLOCKERS = [
  "processingBatches",
  "activeUploads",
  "uploadChunks",
  "processingFingerprints",
  "processingAttempts",
  "processingScopeHeads",
] as const;

const AUTHORITY_BLOCKERS = [
  "processingBatches",
  "activeUploads",
  "invalidUploadExpiries",
  "uploadChunks",
  "processingFingerprints",
  "processingScopeHeads",
  "processingAttempts",
] as const;

const ATTESTATION_TARGET_SNAPSHOT_KEYS = [
  "sales_import_batches",
  "erp_product_master",
  "sales_order_lines",
  "sales_query_projection",
  "import_content_fingerprints",
  "import_content_attempts",
  "import_scope_heads",
  "sales_import_uploads",
  "sales_import_upload_chunks",
] as const;

type JsonRecord = Record<string, unknown>;
type TableEvidence = { rowCount: number; sha256: string };
type PreservationEvidence = {
  schemaObjectCount: number;
  schemaSha256: string;
  erpTables: Record<string, TableEvidence>;
  sharedNonSalesRows: Record<string, TableEvidence>;
};
type RetiredEvidence = {
  salesTables: Record<string, TableEvidence>;
  sharedSalesRows: Record<string, TableEvidence>;
};

type RetirementReceiptRow = {
  domain: string;
  version: string;
  status: string;
  cutover_id: string;
  plan_id: string;
  attestation_sha256: string;
  smoke_receipt_sha256: string;
  preflight_evidence_sha256: string;
  migration_sha256: string;
  audit_id: string;
  preserved_evidence_sha256: string;
  created_at: string;
  completed_at: string | null;
};

type VerifiedAttestation = {
  payloadSha256: string;
  fileSha256: string;
  pathSha256: string;
  observedAt: string;
  authorityEpoch: number;
  authorityUpdatedAt: string;
  sourcePathSha256: string;
  sourceFileIdentitySha256: string;
  authoritySchemaSha256: string;
  sourceSizeBytes: number;
  migrationApplyRunId: string;
  migrationVerifyRunId: string;
  cleanupManifestId: string;
  cleanupManifestSha256: string;
  cleanupCoreEvidenceSha256: string;
};

export type SalesPostgresqlRetirementPreflight = {
  version: typeof POSTGRESQL_PREFLIGHT_VERSION;
  status: "verified";
  planId: string;
  cutoverId: string;
  attestationPayloadSha256: string;
  pgAuthorityStatus: "active";
  pgAuthorityEpoch: string;
  migrationVerifyRunId: string;
  requiredChecks: Array<(typeof POSTGRESQL_PREFLIGHT_CHECKS)[number]>;
  checkedAt: string;
  expiresAt: string;
  smokeReceiptSha256: string;
  evidenceSha256: string;
};

type VerifiedSmokeReceipt = {
  fileSha256: string;
  pathSha256: string;
};

type VerifiedMigration = {
  fileSha256: string;
  pathSha256: string;
  statements: string[];
};

export type SalesD1RetirementInput = {
  source: string;
  cutoverId: string;
  attestationPath: string;
  attestationSha256: string;
  auditOutput: string;
  repositoryRoot?: string;
};

export type SalesD1RetirementAudit = {
  version: typeof RETIREMENT_VERSION;
  auditId: string;
  cutoverId: string;
  sourcePathSha256: string;
  auditOutputPathSha256: string;
  approvedPlanId: string;
  sourceCoreEvidenceSha256: string;
  recordedAt: string;
  attestation: VerifiedAttestation;
  smokeReceipt: VerifiedSmokeReceipt;
  postgresqlPreflight: SalesPostgresqlRetirementPreflight;
  migration: Omit<VerifiedMigration, "statements"> & { statementCount: number };
  authority: {
    owner: "postgresql";
    epoch: number;
    cutoverId: string;
    updatedAt: string;
    blockers: SalesD1AuthoritySnapshot["blockers"];
  };
  retiredEvidence: RetiredEvidence;
  preservedEvidence: PreservationEvidence;
  result: {
    retiredTablesAbsent: readonly string[];
    retirementTombstoneViewsPresent: readonly string[];
    retiredTriggersAbsent: readonly string[];
    sharedSalesRowsDeleted: readonly string[];
    sharedImportRetirementGuardsPresent: readonly string[];
    preservedEvidenceSha256: string;
  };
};

export type SalesD1RetirementPlan = {
  status: "planned" | "already_completed" | "recovery_required";
  cutoverId: string;
  sourcePathSha256: string;
  migrationSha256: string;
  attestationSha256: string;
  planId?: string;
  authorityEpoch?: number;
  blockers?: SalesD1AuthoritySnapshot["blockers"];
  salesTableCounts?: Record<string, number>;
  sharedSalesRowCounts?: Record<string, number>;
  preservedEvidenceSha256?: string;
  auditId?: string;
};

export type RetirementDependencies = {
  now?: () => Date;
  verifyPostgresqlPreflight?: (input: {
    planId: string;
    cutoverId: string;
    attestationPayloadSha256: string;
    migrationVerifyRunId: string;
    smokeReceiptPath: string;
    smokeReceiptSha256: string;
  }) => Promise<unknown>;
  afterStatement?: (
    index: number,
    statement: string,
    database: DatabaseSync,
  ) => void;
};

type ResolvedInput = {
  source: string;
  cutoverId: string;
  attestationPath: string;
  attestationSha256: string;
  auditOutput: string;
  preparedAudit: string;
  migrationPath: string;
  sourcePathSha256: string;
};

function sha256(value: string | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    const encoded = JSON.stringify(value);
    if (encoded === undefined) throw new Error("规范 JSON 不允许 undefined");
    return encoded;
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map(
    (key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`,
  ).join(",")}}`;
}

function jsonObject(value: unknown, label: string): JsonRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} 必须是 JSON 对象`);
  }
  return value as JsonRecord;
}

function exactObject(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
): JsonRecord {
  const record = jsonObject(value, label);
  if (Object.keys(record).sort().join("\0") !== [...expectedKeys].sort().join("\0")) {
    throw new Error(`${label} 字段集合无效`);
  }
  return record;
}

function safeInteger(value: unknown, label: string, minimum = 0): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) {
    throw new Error(`${label} 无效`);
  }
  return value;
}

function safeCutoverId(value: string): string {
  const normalized = String(value ?? "").trim();
  if (!/^[A-Za-z0-9._:-]{8,128}$/.test(normalized)) {
    throw new Error("cutoverId 必须是 8 到 128 位安全标识");
  }
  return normalized;
}

function timestamp(value: unknown, label: string): string {
  const raw = String(value ?? "");
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)
    ? `${raw.replace(" ", "T")}Z`
    : raw;
  if (!normalized || !Number.isFinite(Date.parse(normalized))) throw new Error(`${label} 无效`);
  return new Date(normalized).toISOString();
}

function utcSecondsTimestamp(value: unknown, label: string): string {
  const raw = String(value ?? "");
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(raw)
    || !Number.isFinite(Date.parse(raw))) {
    throw new Error(`${label} 无效`);
  }
  return raw;
}

function validatePostgresqlPreflight(
  value: unknown,
  expected: {
    planId: string;
    cutoverId: string;
    attestationPayloadSha256: string;
    migrationVerifyRunId: string;
    smokeReceiptSha256: string;
  },
  options: { now?: Date; requireFresh: boolean },
): SalesPostgresqlRetirementPreflight {
  const record = exactObject(value, [
    "status", "version", "planId", "cutoverId", "attestationPayloadSha256",
    "pgAuthorityStatus", "pgAuthorityEpoch", "migrationVerifyRunId",
    "requiredChecks", "checkedAt", "expiresAt", "smokeReceiptSha256",
    "evidenceSha256",
  ], "PostgreSQL retirement preflight");
  const evidenceSha256 = String(record.evidenceSha256 ?? "");
  const core = { ...record };
  delete core.evidenceSha256;
  const requiredChecks = record.requiredChecks;
  if (record.status !== "verified"
    || record.version !== POSTGRESQL_PREFLIGHT_VERSION
    || record.planId !== expected.planId
    || record.cutoverId !== expected.cutoverId
    || record.attestationPayloadSha256 !== expected.attestationPayloadSha256
    || record.pgAuthorityStatus !== "active"
    || typeof record.pgAuthorityEpoch !== "string"
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(record.pgAuthorityEpoch)
    || record.migrationVerifyRunId !== expected.migrationVerifyRunId
    || !Array.isArray(requiredChecks)
    || canonicalJson(requiredChecks) !== canonicalJson(POSTGRESQL_PREFLIGHT_CHECKS)
    || record.smokeReceiptSha256 !== expected.smokeReceiptSha256
    || !/^[0-9a-f]{64}$/.test(evidenceSha256)
    || sha256(canonicalJson(core)) !== evidenceSha256) {
    throw new Error("PostgreSQL retirement preflight 身份、检查项或摘要无效");
  }
  const checkedAt = utcSecondsTimestamp(record.checkedAt, "preflight checkedAt");
  const expiresAt = utcSecondsTimestamp(record.expiresAt, "preflight expiresAt");
  const checkedMs = Date.parse(checkedAt);
  const expiresMs = Date.parse(expiresAt);
  if (expiresMs <= checkedMs || expiresMs - checkedMs > POSTGRESQL_PREFLIGHT_MAX_TTL_MS) {
    throw new Error("PostgreSQL retirement preflight TTL 无效");
  }
  if (options.requireFresh) {
    const nowMs = (options.now ?? new Date()).getTime();
    if (checkedMs > nowMs + POSTGRESQL_PREFLIGHT_CLOCK_SKEW_MS || nowMs >= expiresMs) {
      throw new Error("PostgreSQL retirement preflight 已过期或来自未来");
    }
  }
  return record as unknown as SalesPostgresqlRetirementPreflight;
}

async function verifiedSmokeReceipt(
  smokeReceiptPath: string,
  smokeReceiptSha256: string,
): Promise<{ path: string; evidence: VerifiedSmokeReceipt }> {
  if (!path.isAbsolute(smokeReceiptPath) || path.extname(smokeReceiptPath).toLowerCase() !== ".json") {
    throw new Error("smoke receipt 必须是精确的绝对 JSON 文件路径");
  }
  if (!/^[0-9a-f]{64}$/.test(smokeReceiptSha256)) {
    throw new Error("smoke receipt SHA-256 无效");
  }
  const resolved = await realpath(smokeReceiptPath);
  const before = await stat(resolved, { bigint: true });
  if (!before.isFile() || before.size < BigInt(1) || before.size > BigInt(64 * 1024)) {
    throw new Error("smoke receipt 文件无效");
  }
  const bytes = await readFile(resolved);
  const after = await stat(resolved, { bigint: true });
  if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size
    || before.mtimeNs !== after.mtimeNs || BigInt(bytes.length) !== before.size) {
    throw new Error("读取 smoke receipt 期间文件发生变化");
  }
  if (sha256(bytes) !== smokeReceiptSha256) throw new Error("smoke receipt 文件摘要不匹配");
  return {
    path: resolved,
    evidence: { fileSha256: smokeReceiptSha256, pathSha256: sha256(resolved) },
  };
}

function quoteIdentifier(name: string): string {
  return `"${name.replaceAll('"', '""')}"`;
}

const RETIREMENT_TOMBSTONE_VIEW_SCHEMA_STATEMENTS = RETIREMENT_TOMBSTONE_VIEWS.map(
  (name) => `CREATE VIEW \`${name}\` AS
  SELECT '${RETIREMENT_TOMBSTONE_VALUE}' AS \`retirement_tombstone\`
  WHERE 0`,
);

const SHARED_IMPORT_RETIREMENT_GUARD_SPECS = [
  { table: "import_content_fingerprints", name: "fingerprints" },
  { table: "import_content_attempts", name: "attempts" },
  { table: "import_scope_heads", name: "scope_heads" },
] as const;

const SHARED_IMPORT_RETIREMENT_GUARD_STATEMENTS = SHARED_IMPORT_RETIREMENT_GUARD_SPECS.flatMap(
  ({ table, name }) => [
    `CREATE TRIGGER \`sales_retired_${name}_insert_guard\`
BEFORE INSERT ON \`${table}\`
WHEN NEW.\`domain\` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END`,
    `CREATE TRIGGER \`sales_retired_${name}_update_guard\`
BEFORE UPDATE ON \`${table}\`
WHEN OLD.\`domain\` = 'sales' OR NEW.\`domain\` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END`,
    `CREATE TRIGGER \`sales_retired_${name}_delete_guard\`
BEFORE DELETE ON \`${table}\`
WHEN OLD.\`domain\` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END`,
  ],
);

const RETIREMENT_RECEIPT_SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS domain_retirement_receipts (
    domain TEXT PRIMARY KEY NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('approved', 'completed')),
    cutover_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    attestation_sha256 TEXT NOT NULL,
    smoke_receipt_sha256 TEXT NOT NULL,
    preflight_evidence_sha256 TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL,
    audit_id TEXT NOT NULL,
    preserved_evidence_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK (
      (status = 'approved' AND completed_at IS NULL)
      OR (status = 'completed' AND completed_at IS NOT NULL)
    )
  )`,
  `CREATE TRIGGER IF NOT EXISTS domain_retirement_receipts_insert_guard
    BEFORE INSERT ON domain_retirement_receipts
    WHEN NEW.status <> 'approved'
      OR NEW.completed_at IS NOT NULL
      OR EXISTS (
        SELECT 1 FROM domain_retirement_receipts WHERE domain = NEW.domain
      )
    BEGIN SELECT RAISE(ABORT, 'domain_retirement_receipt_insert_forbidden'); END`,
  `CREATE TRIGGER IF NOT EXISTS domain_retirement_receipts_transition_guard
    BEFORE UPDATE ON domain_retirement_receipts
    WHEN NOT (
      OLD.status = 'approved'
      AND NEW.status = 'completed'
      AND OLD.domain = NEW.domain
      AND OLD.version = NEW.version
      AND OLD.cutover_id = NEW.cutover_id
      AND OLD.plan_id = NEW.plan_id
      AND OLD.attestation_sha256 = NEW.attestation_sha256
      AND OLD.smoke_receipt_sha256 = NEW.smoke_receipt_sha256
      AND OLD.preflight_evidence_sha256 = NEW.preflight_evidence_sha256
      AND OLD.migration_sha256 = NEW.migration_sha256
      AND OLD.audit_id = NEW.audit_id
      AND OLD.preserved_evidence_sha256 = NEW.preserved_evidence_sha256
      AND OLD.created_at = NEW.created_at
      AND OLD.completed_at IS NULL
      AND NEW.completed_at IS NOT NULL
    )
    BEGIN SELECT RAISE(ABORT, 'domain_retirement_receipt_update_forbidden'); END`,
  `CREATE TRIGGER IF NOT EXISTS domain_retirement_receipts_no_delete
    BEFORE DELETE ON domain_retirement_receipts
    BEGIN SELECT RAISE(ABORT, 'domain_retirement_receipt_delete_forbidden'); END`,
] as const;

function ensureRetirementReceiptSchema(database: DatabaseSync): void {
  for (const statement of RETIREMENT_RECEIPT_SCHEMA_STATEMENTS) database.exec(statement);
}

function tableExists(database: DatabaseSync, name: string): boolean {
  return Boolean(database.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
  ).get(name));
}

function triggerExists(database: DatabaseSync, name: string): boolean {
  return Boolean(database.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=? LIMIT 1",
  ).get(name));
}

function retirementReceiptSchemaRows(database: DatabaseSync): Array<{
  type: string;
  name: string;
  table: string;
  sql: string;
}> {
  const placeholders = RETIREMENT_RECEIPT_SCHEMA_OBJECTS.map(() => "?").join(",");
  return (database.prepare(
    `SELECT type, name, tbl_name AS "table", COALESCE(sql, '') AS sql
     FROM sqlite_master WHERE name IN (${placeholders})
     ORDER BY type COLLATE BINARY, name COLLATE BINARY`,
  ).all(...RETIREMENT_RECEIPT_SCHEMA_OBJECTS) as Array<{
    type: string;
    name: string;
    table: string;
    sql: string;
  }>).map((row) => ({
    ...row,
    sql: row.sql.replace(/\s+/g, " ").trim(),
  }));
}

function assertRetirementReceiptSchema(database: DatabaseSync): void {
  const expectedDatabase = new DatabaseSync(":memory:");
  let expected: ReturnType<typeof retirementReceiptSchemaRows>;
  try {
    ensureRetirementReceiptSchema(expectedDatabase);
    expected = retirementReceiptSchemaRows(expectedDatabase);
  } finally {
    expectedDatabase.close();
  }
  const actual = retirementReceiptSchemaRows(database);
  if (actual.length !== RETIREMENT_RECEIPT_SCHEMA_OBJECTS.length
    || canonicalJson(actual) !== canonicalJson(expected)) {
    throw new Error("D1 通用退役 receipt schema 不完整或语义不一致");
  }
}

function retirementTombstoneViewRows(database: DatabaseSync): Array<{
  type: string;
  name: string;
  table: string;
  sql: string;
}> {
  return (database.prepare(
    `SELECT type, name, tbl_name AS "table", COALESCE(sql, '') AS sql
     FROM sqlite_master
     WHERE type='view' AND name GLOB 'sales_*'
     ORDER BY name COLLATE BINARY`,
  ).all() as Array<{ type: string; name: string; table: string; sql: string }>).map((row) => ({
    ...row,
    sql: row.sql.replace(/\s+/g, " ").trim(),
  }));
}

function assertRetirementTombstoneViews(database: DatabaseSync): void {
  const expectedDatabase = new DatabaseSync(":memory:");
  let expected: ReturnType<typeof retirementTombstoneViewRows>;
  try {
    for (const statement of RETIREMENT_TOMBSTONE_VIEW_SCHEMA_STATEMENTS) {
      expectedDatabase.exec(statement);
    }
    expected = retirementTombstoneViewRows(expectedDatabase);
  } finally {
    expectedDatabase.close();
  }
  const actual = retirementTombstoneViewRows(database);
  if (actual.length !== RETIREMENT_TOMBSTONE_VIEWS.length
    || canonicalJson(actual) !== canonicalJson(expected)) {
    throw new Error("D1 销售退役 tombstone view 不完整或语义不一致");
  }
  const placeholders = RETIREMENT_TOMBSTONE_VIEWS.map(() => "?").join(",");
  const attachedTrigger = database.prepare(
    `SELECT name FROM sqlite_master
     WHERE type='trigger' AND tbl_name IN (${placeholders})
     LIMIT 1`,
  ).get(...RETIREMENT_TOMBSTONE_VIEWS) as { name?: string } | undefined;
  if (attachedTrigger?.name) {
    throw new Error(`D1 销售退役 tombstone view 存在可写 trigger：${attachedTrigger.name}`);
  }
  for (const view of RETIREMENT_TOMBSTONE_VIEWS) {
    const count = safeInteger((database.prepare(
      `SELECT COUNT(*) AS count FROM ${quoteIdentifier(view)}`,
    ).get() as { count?: number } | undefined)?.count, `${view} tombstone rows`);
    if (count !== 0) throw new Error(`D1 销售退役 tombstone view 非空：${view}`);
  }
}

function sharedImportTriggerRows(database: DatabaseSync): Array<{
  type: string;
  name: string;
  table: string;
  sql: string;
}> {
  const placeholders = SHARED_IMPORT_TABLES.map(() => "?").join(",");
  return (database.prepare(
    `SELECT type, name, tbl_name AS "table", COALESCE(sql, '') AS sql
     FROM sqlite_master
     WHERE type='trigger' AND tbl_name IN (${placeholders})
     ORDER BY name COLLATE BINARY`,
  ).all(...SHARED_IMPORT_TABLES) as Array<{
    type: string;
    name: string;
    table: string;
    sql: string;
  }>).map((row) => ({
    ...row,
    sql: row.sql.replace(/\s+/g, " ").trim(),
  }));
}

function assertSharedImportRetirementGuards(database: DatabaseSync): void {
  const expectedDatabase = new DatabaseSync(":memory:");
  let expected: ReturnType<typeof sharedImportTriggerRows>;
  try {
    for (const table of SHARED_IMPORT_TABLES) {
      expectedDatabase.exec(`CREATE TABLE ${quoteIdentifier(table)} (domain TEXT NOT NULL)`);
    }
    for (const statement of SHARED_IMPORT_RETIREMENT_GUARD_STATEMENTS) {
      expectedDatabase.exec(statement);
    }
    expected = sharedImportTriggerRows(expectedDatabase);
  } finally {
    expectedDatabase.close();
  }
  const actual = sharedImportTriggerRows(database);
  if (actual.length !== SHARED_IMPORT_RETIREMENT_GUARDS.length
    || canonicalJson(actual) !== canonicalJson(expected)) {
    throw new Error("D1 销售退役 shared-import guard 不完整或语义不一致");
  }
}

function retirementReceipt(database: DatabaseSync): RetirementReceiptRow | null {
  if (!tableExists(database, RETIREMENT_RECEIPT_TABLE)) return null;
  const row = database.prepare(
    `SELECT domain, version, status, cutover_id, plan_id, attestation_sha256,
            smoke_receipt_sha256, preflight_evidence_sha256, migration_sha256,
            audit_id, preserved_evidence_sha256, created_at, completed_at
     FROM domain_retirement_receipts WHERE domain='sales' LIMIT 1`,
  ).get() as RetirementReceiptRow | undefined;
  return row ?? null;
}

function assertReceiptMatchesAudit(
  database: DatabaseSync,
  audit: SalesD1RetirementAudit,
  expectedStatus: "approved" | "completed",
): RetirementReceiptRow {
  assertRetirementReceiptSchema(database);
  const row = retirementReceipt(database);
  const preservedSha = sha256(canonicalJson(audit.preservedEvidence));
  if (!row
    || row.domain !== "sales"
    || row.version !== RETIREMENT_RECEIPT_VERSION
    || row.status !== expectedStatus
    || row.cutover_id !== audit.cutoverId
    || row.plan_id !== audit.approvedPlanId
    || row.attestation_sha256 !== audit.attestation.payloadSha256
    || row.smoke_receipt_sha256 !== audit.smokeReceipt.fileSha256
    || row.preflight_evidence_sha256 !== audit.postgresqlPreflight.evidenceSha256
    || row.migration_sha256 !== audit.migration.fileSha256
    || row.audit_id !== audit.auditId
    || row.preserved_evidence_sha256 !== preservedSha
    || timestamp(row.created_at, "retirement receipt createdAt") !== audit.recordedAt
    || (expectedStatus === "approved" && row.completed_at !== null)
    || (expectedStatus === "completed" && row.completed_at === null)) {
    throw new Error("D1 通用退役 receipt 与不可变审计不匹配");
  }
  if (row.completed_at !== null) timestamp(row.completed_at, "retirement receipt completedAt");
  return row;
}

function insertApprovedRetirementReceipt(
  database: DatabaseSync,
  audit: SalesD1RetirementAudit,
): void {
  if (retirementReceipt(database)) throw new Error("D1 已存在 sales 退役 receipt，拒绝重复出票");
  database.prepare(
    `INSERT INTO domain_retirement_receipts (
       domain, version, status, cutover_id, plan_id, attestation_sha256,
       smoke_receipt_sha256, preflight_evidence_sha256, migration_sha256,
       audit_id, preserved_evidence_sha256, created_at, completed_at
     ) VALUES ('sales', ?, 'approved', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)`,
  ).run(
    RETIREMENT_RECEIPT_VERSION,
    audit.cutoverId,
    audit.approvedPlanId,
    audit.attestation.payloadSha256,
    audit.smokeReceipt.fileSha256,
    audit.postgresqlPreflight.evidenceSha256,
    audit.migration.fileSha256,
    audit.auditId,
    sha256(canonicalJson(audit.preservedEvidence)),
    audit.recordedAt,
  );
  assertReceiptMatchesAudit(database, audit, "approved");
}

function completeRetirementReceipt(
  database: DatabaseSync,
  audit: SalesD1RetirementAudit,
  completedAt: string,
): void {
  const result = database.prepare(
    `UPDATE domain_retirement_receipts SET status='completed', completed_at=?
     WHERE domain='sales' AND status='approved' AND audit_id=?`,
  ).run(completedAt, audit.auditId);
  if (Number(result.changes) !== 1) throw new Error("D1 通用退役 receipt 完成 CAS 失败");
  assertReceiptMatchesAudit(database, audit, "completed");
}

function sourceRetirementState(database: DatabaseSync): "present" | "retired" | "partial" {
  const present = RETIRED_SALES_TABLES.filter((name) => tableExists(database, name));
  if (present.length === RETIRED_SALES_TABLES.length) return "present";
  if (present.length === 0) return "retired";
  return "partial";
}

function sqlReferencesIdentifier(sql: string, identifier: string): boolean {
  const escaped = identifier.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(^|[^a-z0-9_])${escaped}([^a-z0-9_]|$)`, "i").test(sql);
}

function assertNoInboundRetirementForeignKeys(database: DatabaseSync): void {
  const protectedTargets = new Set<string>([
    ...RETIRED_SALES_TABLES,
    ...SHARED_IMPORT_TABLES,
  ]);
  const tables = database.prepare(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name COLLATE BINARY",
  ).all() as Array<{ name: string }>;
  for (const { name } of tables) {
    const foreignKeys = database.prepare(
      `PRAGMA foreign_key_list(${quoteIdentifier(name)})`,
    ).all() as Array<{ table?: string }>;
    for (const foreignKey of foreignKeys) {
      const target = String(foreignKey.table ?? "").toLowerCase();
      if (protectedTargets.has(target)) {
        throw new Error(`D1 边界外表 ${name} 存在指向退役或 shared-delete 表 ${target} 的外键`);
      }
    }
  }
}

function assertNoBoundaryObjectReferences(database: DatabaseSync): void {
  const allowedTriggers = new Set<string>(RETIRED_SALES_TRIGGERS);
  const rows = database.prepare(
    `SELECT type, name, COALESCE(sql, '') AS sql
     FROM sqlite_master
     WHERE type IN ('view', 'trigger')
     ORDER BY type COLLATE BINARY, name COLLATE BINARY`,
  ).all() as Array<{ type: string; name: string; sql: string }>;
  for (const row of rows) {
    if (row.type === "trigger" && allowedTriggers.has(row.name)) continue;
    const referenced = RETIRED_SALES_TABLES.find((table) => sqlReferencesIdentifier(row.sql, table));
    if (referenced) {
      throw new Error(`D1 边界外 ${row.type} ${row.name} 引用退役销售表 ${referenced}`);
    }
  }
}

function assertSalesSchemaBoundary(database: DatabaseSync): void {
  const tables = (database.prepare(
    "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'sales_*' ORDER BY name COLLATE BINARY",
  ).all() as Array<{ name: string }>).map((row) => row.name);
  if (tables.join("\0") !== [...RETIRED_SALES_TABLES].sort().join("\0")) {
    throw new Error("D1 销售表集合超出或缺少 0092 既定退役边界");
  }
  const triggers = (database.prepare(
    `SELECT name FROM sqlite_master
     WHERE type='trigger' AND name IN (${RETIRED_SALES_TRIGGERS.map(() => "?").join(",")})
     ORDER BY name COLLATE BINARY`,
  ).all(...RETIRED_SALES_TRIGGERS) as Array<{ name: string }>).map((row) => row.name);
  if (triggers.join("\0") !== [...RETIRED_SALES_TRIGGERS].sort().join("\0")) {
    throw new Error("D1 销售 trigger 集合缺少 0092 既定退役对象");
  }
  const unexpected = database.prepare(
    `SELECT name FROM sqlite_master
     WHERE type='trigger'
       AND tbl_name IN (${RETIRED_SALES_TABLES.map(() => "?").join(",")})
       AND name NOT IN (${RETIRED_SALES_TRIGGERS.map(() => "?").join(",")})
     LIMIT 1`,
  ).get(...RETIRED_SALES_TABLES, ...RETIRED_SALES_TRIGGERS) as { name?: string } | undefined;
  if (unexpected?.name) {
    throw new Error(`D1 存在 0092 边界外的销售 trigger：${unexpected.name}`);
  }
  const sharedTriggers = sharedImportTriggerRows(database).map((row) => row.name);
  if (sharedTriggers.join("\0") !== [...SHARED_IMPORT_AUTHORITY_TRIGGERS].sort().join("\0")) {
    throw new Error("D1 shared-import trigger 集合超出或缺少 0090 既定 authority 边界");
  }
  assertNoInboundRetirementForeignKeys(database);
  assertNoBoundaryObjectReferences(database);
}

function canonicalSqlValue(value: unknown): unknown {
  if (value === null || typeof value === "string" || typeof value === "number") return value;
  if (typeof value === "bigint") return { bigint: value.toString() };
  if (value instanceof Uint8Array) return { blobHex: Buffer.from(value).toString("hex") };
  throw new Error("D1 证据包含不支持的 SQLite 值类型");
}

function tableEvidence(
  database: DatabaseSync,
  table: string,
  where = "",
): TableEvidence {
  if (!tableExists(database, table)) throw new Error(`D1 缺少证据表 ${table}`);
  const columns = (database.prepare(`PRAGMA table_info(${quoteIdentifier(table)})`).all() as Array<{
    name: string;
  }>).map((row) => row.name);
  if (columns.length === 0) throw new Error(`D1 证据表 ${table} 没有字段`);
  const tableSql = String((database.prepare(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
  ).get(table) as { sql?: string } | undefined)?.sql ?? "");
  const primary = (database.prepare(`PRAGMA table_info(${quoteIdentifier(table)})`).all() as Array<{
    name: string;
    pk: number;
  }>).filter((row) => Number(row.pk) > 0).sort((left, right) => left.pk - right.pk);
  const order = /WITHOUT\s+ROWID/i.test(tableSql)
    ? primary.map((row) => quoteIdentifier(row.name)).join(", ")
    : "rowid";
  if (!order) throw new Error(`D1 WITHOUT ROWID 证据表 ${table} 缺少主键`);
  const selected = columns.map(quoteIdentifier).join(", ");
  const digest = createHash("sha256");
  digest.update(`${canonicalJson({ format: "sales-d1-table-evidence-v1", table, columns, where })}\n`);
  let rowCount = 0;
  const statement = database.prepare(
    `SELECT ${selected} FROM ${quoteIdentifier(table)} ${where} ORDER BY ${order}`,
  );
  for (const row of statement.iterate() as Iterable<Record<string, unknown>>) {
    digest.update(`${canonicalJson(columns.map((column) => canonicalSqlValue(row[column])))}\n`);
    rowCount += 1;
  }
  return { rowCount, sha256: digest.digest("hex") };
}

function assertErpSchemaBoundary(database: DatabaseSync): void {
  for (const name of REQUIRED_ERP_SCHEMA_OBJECTS) {
    if (!database.prepare("SELECT 1 FROM sqlite_master WHERE name=? LIMIT 1").get(name)) {
      throw new Error(`D1 ERP bridge/master schema 缺少 ${name}`);
    }
  }
  const erpPlaceholders = ERP_BRIDGE_SCHEMA_OBJECTS.map(() => "?").join(",");
  const erpSchema = (database.prepare(
    `SELECT type, name, sql FROM sqlite_master
     WHERE name IN (${erpPlaceholders})
     ORDER BY type COLLATE BINARY, name COLLATE BINARY`,
  ).all(...ERP_BRIDGE_SCHEMA_OBJECTS) as Array<{
    type: string;
    name: string;
    sql: string | null;
  }>).map((row) => [row.type, row.name, row.sql ?? ""]);
  if (erpSchema.length !== ERP_BRIDGE_SCHEMA_OBJECTS.length
    || sha256(canonicalJson(erpSchema)) !== ERP_BRIDGE_SCHEMA_SHA256) {
    throw new Error("D1 ERP bridge schema 不完整或语义与 0091 不一致");
  }
  for (const table of ERP_DATA_TABLES) {
    if (!tableExists(database, table)) throw new Error(`D1 缺少 ERP 保留表 ${table}`);
  }
}

function preservationEvidence(database: DatabaseSync): PreservationEvidence {
  assertErpSchemaBoundary(database);
  const retiredTableSet = new Set<string>(RETIRED_SALES_TABLES);
  const retiredTriggerSet = new Set<string>(RETIRED_SALES_TRIGGERS);
  const retirementGuardSet = new Set<string>(SHARED_IMPORT_RETIREMENT_GUARDS);
  const receiptObjectSet = new Set<string>(RETIREMENT_RECEIPT_SCHEMA_OBJECTS);
  const schemaRows = (database.prepare(
    `SELECT type, name, tbl_name, COALESCE(sql, '') AS sql
     FROM sqlite_master
     WHERE name NOT LIKE 'sqlite_%'
     ORDER BY type COLLATE BINARY, name COLLATE BINARY`,
  ).all() as Array<{ type: string; name: string; tbl_name: string; sql: string }>).filter(
    (row) => !retiredTableSet.has(row.tbl_name)
      && !retiredTriggerSet.has(row.name)
      && !retirementGuardSet.has(row.name)
      && !receiptObjectSet.has(row.name),
  );
  const erpTables: Record<string, TableEvidence> = {};
  for (const table of ERP_DATA_TABLES) erpTables[table] = tableEvidence(database, table);
  const sharedNonSalesRows: Record<string, TableEvidence> = {};
  for (const table of SHARED_IMPORT_TABLES) {
    sharedNonSalesRows[table] = tableEvidence(database, table, "WHERE domain IS NOT 'sales'");
  }
  return {
    schemaObjectCount: schemaRows.length,
    schemaSha256: sha256(canonicalJson(schemaRows)),
    erpTables,
    sharedNonSalesRows,
  };
}

function retiredEvidence(database: DatabaseSync): RetiredEvidence {
  const salesTables: Record<string, TableEvidence> = {};
  for (const table of RETIRED_SALES_TABLES) salesTables[table] = tableEvidence(database, table);
  const sharedSalesRows: Record<string, TableEvidence> = {};
  for (const table of SHARED_IMPORT_TABLES) {
    sharedSalesRows[table] = tableEvidence(database, table, "WHERE domain = 'sales'");
  }
  return { salesTables, sharedSalesRows };
}

function sourceCoreEvidenceSha256(database: DatabaseSync): string {
  return sha256(canonicalJson(salesD1CoreEvidence(database)));
}

function evidenceEqual(left: unknown, right: unknown): boolean {
  return canonicalJson(left) === canonicalJson(right);
}

function authorityTriggerEvidence(database: DatabaseSync): { count: number; sha256: string } {
  const rows = (database.prepare(
    `SELECT name, sql FROM sqlite_master
     WHERE type='trigger' AND name LIKE 'sales_authority_%'
     ORDER BY name COLLATE BINARY`,
  ).all() as Array<{ name: string; sql: string | null }>).map(
    (row) => [row.name, row.sql ?? ""],
  );
  return { count: rows.length, sha256: sha256(canonicalJson(rows)) };
}

async function resolveInput(input: SalesD1RetirementInput): Promise<ResolvedInput> {
  const cutoverId = safeCutoverId(input.cutoverId);
  if (!path.isAbsolute(input.source) || path.extname(input.source).toLowerCase() !== ".sqlite") {
    throw new Error("--source 必须是精确的绝对 .sqlite 路径");
  }
  const source = await realpath(input.source);
  if (!(await stat(source)).isFile()) throw new Error("--source 不是文件");
  if (!path.isAbsolute(input.attestationPath) || path.extname(input.attestationPath).toLowerCase() !== ".json") {
    throw new Error("--attestation 必须是精确的绝对 JSON 文件路径");
  }
  const attestationPath = await realpath(input.attestationPath);
  if (!(await stat(attestationPath)).isFile()) throw new Error("--attestation 不是文件");
  if (!/^[0-9a-f]{64}$/.test(input.attestationSha256)) {
    throw new Error("--attestation-sha256 必须是精确 SHA-256");
  }
  if (!path.isAbsolute(input.auditOutput) || path.extname(input.auditOutput).toLowerCase() !== ".json") {
    throw new Error("--audit-output 必须是精确的绝对 JSON 文件路径");
  }
  const auditParent = await realpath(path.dirname(input.auditOutput));
  if (!(await stat(auditParent)).isDirectory()) throw new Error("--audit-output 父目录无效");
  const auditOutput = path.join(auditParent, path.basename(input.auditOutput));
  const repositoryRoot = await realpath(
    input.repositoryRoot ?? path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."),
  );
  const migrationPath = await realpath(path.join(repositoryRoot, "drizzle", RETIREMENT_MIGRATION_FILE));
  if (!(await stat(migrationPath)).isFile()) throw new Error("D1 0092 迁移文件不存在");
  if (new Set([source, attestationPath, auditOutput, migrationPath]).size !== 4) {
    throw new Error("source、attestation、audit 与 migration 路径必须相互隔离");
  }
  return {
    source,
    cutoverId,
    attestationPath,
    attestationSha256: input.attestationSha256,
    auditOutput,
    preparedAudit: `${auditOutput}.prepared`,
    migrationPath,
    sourcePathSha256: sha256(source),
  };
}

async function verifiedAttestation(input: ResolvedInput): Promise<VerifiedAttestation> {
  const before = await stat(input.attestationPath, { bigint: true });
  const bytes = await readFile(input.attestationPath);
  const after = await stat(input.attestationPath, { bigint: true });
  if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size
    || before.mtimeNs !== after.mtimeNs) {
    throw new Error("读取 attestation 期间文件发生变化");
  }
  const envelope = exactObject(
    JSON.parse(bytes.toString("utf8")),
    ["schemaVersion", "payload", "payloadSha256"],
    "attestation envelope",
  );
  const payload = exactObject(envelope.payload, [
    "schemaVersion",
    "cutoverId",
    "observedAt",
    "d1Authority",
    "d1Blockers",
    "source",
    "postgresqlMigration",
    "legacyCleanup",
  ], "attestation payload");
  const payloadSha256 = sha256(canonicalJson(payload));
  if (envelope.schemaVersion !== SALES_CUTOVER_ATTESTATION_VERSION
    || envelope.payloadSha256 !== payloadSha256
    || input.attestationSha256 !== payloadSha256
    || payload.schemaVersion !== SALES_CUTOVER_ATTESTATION_VERSION
    || payload.cutoverId !== input.cutoverId) {
    throw new Error("attestation 文件、摘要或 cutoverId 不匹配");
  }
  const authority = exactObject(
    payload.d1Authority,
    ["owner", "epoch", "updatedAt"],
    "attestation d1Authority",
  );
  const blockers = exactObject(payload.d1Blockers, ATTESTATION_BLOCKERS, "attestation d1Blockers");
  const source = exactObject(
    payload.source,
    ["pathSha256", "fileIdentitySha256", "sizeBytes", "authoritySchemaSha256"],
    "attestation source",
  );
  const migration = exactObject(payload.postgresqlMigration, [
    "applyRunId",
    "verifyRunId",
    "canonicalFormatVersion",
    "sourceRevision",
    "targetCounts",
    "targetDigests",
  ], "attestation postgresqlMigration");
  const targetCounts = exactObject(
    migration.targetCounts,
    ATTESTATION_TARGET_SNAPSHOT_KEYS,
    "attestation targetCounts",
  );
  const targetDigests = exactObject(
    migration.targetDigests,
    ATTESTATION_TARGET_SNAPSHOT_KEYS,
    "attestation targetDigests",
  );
  const cleanup = exactObject(payload.legacyCleanup, [
    "manifestId",
    "manifestSha256",
    "sessionCount",
    "objectCount",
    "coreEvidenceSha256",
    "lockedVerifyRunId",
    "completedAt",
  ], "attestation legacyCleanup");
  const hex64 = (value: unknown) => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
  const runId = (value: unknown) => typeof value === "string" && /^[0-9a-f]{32,64}$/.test(value);
  const sourceRevision = String(migration.sourceRevision ?? "");
  if (authority.owner !== "postgresql"
    || source.pathSha256 !== input.sourcePathSha256
    || !hex64(source.fileIdentitySha256)
    || source.authoritySchemaSha256 !== AUTHORITY_SCHEMA_SHA256
    || safeInteger(source.sizeBytes, "attestation source sizeBytes", 1) < 1
    || ATTESTATION_BLOCKERS.some((name) => safeInteger(blockers[name], `attestation blocker ${name}`) !== 0)
    || !runId(migration.applyRunId)
    || !runId(migration.verifyRunId)
    || migration.canonicalFormatVersion !== "sales-projection-v4"
    || !/^\d+:\d+$/.test(sourceRevision)
    || ATTESTATION_TARGET_SNAPSHOT_KEYS.some(
      (name) => safeInteger(targetCounts[name], `attestation target count ${name}`) < 0
        || !hex64(targetDigests[name]),
    )
    || !hex64(cleanup.manifestId)
    || !hex64(cleanup.manifestSha256)
    || !hex64(cleanup.coreEvidenceSha256)
    || cleanup.lockedVerifyRunId !== migration.verifyRunId
    || safeInteger(cleanup.sessionCount, "attestation cleanup sessionCount") < 0
    || safeInteger(cleanup.objectCount, "attestation cleanup objectCount") < 0) {
    throw new Error("attestation D1 terminal 证明无效或不属于当前 source");
  }
  timestamp(cleanup.completedAt, "attestation cleanup completedAt");
  return {
    payloadSha256,
    fileSha256: sha256(bytes),
    pathSha256: sha256(input.attestationPath),
    observedAt: timestamp(payload.observedAt, "attestation observedAt"),
    authorityEpoch: safeInteger(authority.epoch, "attestation authority epoch", 1),
    authorityUpdatedAt: timestamp(authority.updatedAt, "attestation authority updatedAt"),
    sourcePathSha256: String(source.pathSha256),
    sourceFileIdentitySha256: String(source.fileIdentitySha256),
    authoritySchemaSha256: String(source.authoritySchemaSha256),
    migrationApplyRunId: String(migration.applyRunId),
    migrationVerifyRunId: String(migration.verifyRunId),
    cleanupManifestId: String(cleanup.manifestId),
    cleanupManifestSha256: String(cleanup.manifestSha256),
    cleanupCoreEvidenceSha256: String(cleanup.coreEvidenceSha256),
    sourceSizeBytes: safeInteger(source.sizeBytes, "attestation source sizeBytes", 1),
  };
}

function splitMigrationStatements(sql: string): string[] {
  return sql.split("--> statement-breakpoint").map((part) => part
    .split(/\r?\n/)
    .map((line) => line.replace(/--.*$/, ""))
    .join("\n")
    .trim())
    .filter(Boolean);
}

function normalizedSqlStatement(sql: string): string {
  return sql.replace(/\s+/g, " ").trim().replace(/;$/, "");
}

export function validateSalesD1RetirementSql(sql: string): string[] {
  if (sha256(sql) !== RETIREMENT_MIGRATION_SHA256) {
    throw new Error("D1 0092 文件身份或内容摘要不匹配");
  }
  const statements = splitMigrationStatements(sql);
  const triggers = new Set<string>();
  const tables = new Set<string>();
  const sharedDeletes = new Set<string>();
  const receiptSchemaObjects = new Set<string>();
  const tombstoneViews = new Set<string>();
  const sharedRetirementGuards = new Set<string>();
  const guardByStatement = new Map(
    SHARED_IMPORT_RETIREMENT_GUARD_STATEMENTS.map((statement, index) => [
      normalizedSqlStatement(statement),
      SHARED_IMPORT_RETIREMENT_GUARDS[index],
    ]),
  );
  let assertionCount = 0;
  for (const statement of statements) {
    let match = /^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+domain_retirement_receipts\b[\s\S]*\);?$/i.exec(statement);
    if (match) {
      receiptSchemaObjects.add(RETIREMENT_RECEIPT_TABLE);
      continue;
    }
    match = /^CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+(domain_retirement_receipts_(?:insert_guard|transition_guard|no_delete))\b[\s\S]*\bEND;?$/i.exec(statement);
    if (match) {
      receiptSchemaObjects.add(match[1]);
      continue;
    }
    const sharedRetirementGuard = guardByStatement.get(normalizedSqlStatement(statement));
    if (sharedRetirementGuard) {
      sharedRetirementGuards.add(sharedRetirementGuard);
      continue;
    }
    match = /^CREATE\s+VIEW\s+`([^`]+)`\s+AS\s+SELECT\s+'sales-domain-retired-v1'\s+AS\s+`retirement_tombstone`\s+WHERE\s+0;?$/i.exec(statement);
    if (match) {
      tombstoneViews.add(match[1]);
      continue;
    }
    if (/^SELECT\s+CASE\b[\s\S]*\bEND;?$/i.test(statement)) {
      assertionCount += 1;
      continue;
    }
    match = /^DROP\s+TRIGGER\s+IF\s+EXISTS\s+`([^`]+)`;?$/i.exec(statement);
    if (match) {
      triggers.add(match[1]);
      continue;
    }
    match = /^DELETE\s+FROM\s+`([^`]+)`\s+WHERE\s+`domain`\s*=\s*'sales';?$/i.exec(statement);
    if (match) {
      sharedDeletes.add(match[1]);
      continue;
    }
    match = /^DROP\s+TABLE\s+IF\s+EXISTS\s+`([^`]+)`;?$/i.exec(statement);
    if (match) {
      tables.add(match[1]);
      continue;
    }
    throw new Error("D1 0092 包含超出销售域退役允许范围的 SQL");
  }
  const exact = (actual: Set<string>, expected: readonly string[]) =>
    [...actual].sort().join("\0") === [...expected].sort().join("\0");
  if (assertionCount !== 1
    || !exact(receiptSchemaObjects, RETIREMENT_RECEIPT_SCHEMA_OBJECTS)
    || !exact(triggers, RETIRED_SALES_TRIGGERS)
    || !exact(tables, RETIRED_SALES_TABLES)
    || !exact(tombstoneViews, RETIREMENT_TOMBSTONE_VIEWS)
    || !exact(sharedRetirementGuards, SHARED_IMPORT_RETIREMENT_GUARDS)
    || !exact(sharedDeletes, SHARED_IMPORT_TABLES)
    || statements.length !== RETIREMENT_RECEIPT_SCHEMA_OBJECTS.length + 1 + RETIRED_SALES_TRIGGERS.length
      + RETIRED_SALES_TABLES.length + RETIREMENT_TOMBSTONE_VIEWS.length
      + SHARED_IMPORT_TABLES.length + SHARED_IMPORT_RETIREMENT_GUARDS.length) {
    throw new Error("D1 0092 销售表、trigger、tombstone view、shared guard 或共享 sales 行清单不完整");
  }
  return statements;
}

async function verifiedMigration(input: ResolvedInput): Promise<VerifiedMigration> {
  const sql = await readFile(input.migrationPath, "utf8");
  return {
    fileSha256: sha256(sql),
    pathSha256: sha256(input.migrationPath),
    statements: validateSalesD1RetirementSql(sql),
  };
}

function assertPreRetirementState(
  database: DatabaseSync,
  input: ResolvedInput,
  attestation: VerifiedAttestation,
): { authority: SalesD1AuthoritySnapshot; coreEvidenceSha256: string } {
  if (sourceRetirementState(database) !== "present") {
    throw new Error("D1 销售域处于部分或已退役状态，拒绝猜测执行");
  }
  const authority = inspectSalesD1WriteAuthority(database);
  if (authority.owner !== "postgresql" || authority.cutoverId !== input.cutoverId
    || authority.epoch !== attestation.authorityEpoch
    || timestamp(authority.updatedAt, "D1 authority updatedAt") !== attestation.authorityUpdatedAt) {
    throw new Error("D1 authority 未以同一 cutoverId/epoch 进入 PostgreSQL terminal");
  }
  if (Object.values(authority.blockers).some((value) => value !== 0)) {
    throw new Error(`D1 销售退役 blockers 非零：${JSON.stringify(authority.blockers)}`);
  }
  const triggerEvidence = authorityTriggerEvidence(database);
  if (triggerEvidence.count !== AUTHORITY_TRIGGER_COUNT
    || triggerEvidence.sha256 !== attestation.authoritySchemaSha256
    || triggerEvidence.sha256 !== AUTHORITY_SCHEMA_SHA256) {
    throw new Error("D1 authority trigger schema 与已验证 attestation 不一致");
  }
  assertSalesSchemaBoundary(database);
  const coreEvidenceSha256 = sourceCoreEvidenceSha256(database);
  if (coreEvidenceSha256 !== attestation.cleanupCoreEvidenceSha256) {
    throw new Error("D1 当前销售核心事实/控制摘要与 attestation cleanup 证明不一致");
  }
  return { authority, coreEvidenceSha256 };
}

function auditCore(audit: SalesD1RetirementAudit): Omit<SalesD1RetirementAudit, "auditId"> {
  const core = { ...audit } as Partial<SalesD1RetirementAudit>;
  delete core.auditId;
  return core as Omit<SalesD1RetirementAudit, "auditId">;
}

function validateTableEvidenceMap(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
): Record<string, TableEvidence> {
  const record = exactObject(value, expectedKeys, label);
  for (const key of expectedKeys) {
    const evidence = exactObject(record[key], ["rowCount", "sha256"], `${label} ${key}`);
    safeInteger(evidence.rowCount, `${label} ${key} rowCount`);
    if (typeof evidence.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(evidence.sha256)) {
      throw new Error(`${label} ${key} sha256 无效`);
    }
  }
  return record as Record<string, TableEvidence>;
}

function validateRetiredEvidence(value: unknown): RetiredEvidence {
  const record = exactObject(value, ["salesTables", "sharedSalesRows"], "retired evidence");
  return {
    salesTables: validateTableEvidenceMap(
      record.salesTables,
      RETIRED_SALES_TABLES,
      "retired sales tables",
    ),
    sharedSalesRows: validateTableEvidenceMap(
      record.sharedSalesRows,
      SHARED_IMPORT_TABLES,
      "retired shared sales rows",
    ),
  };
}

function validatePreservationEvidence(value: unknown): PreservationEvidence {
  const record = exactObject(value, [
    "schemaObjectCount", "schemaSha256", "erpTables", "sharedNonSalesRows",
  ], "preservation evidence");
  safeInteger(record.schemaObjectCount, "preservation schemaObjectCount");
  if (typeof record.schemaSha256 !== "string" || !/^[0-9a-f]{64}$/.test(record.schemaSha256)) {
    throw new Error("preservation schemaSha256 无效");
  }
  return {
    schemaObjectCount: record.schemaObjectCount as number,
    schemaSha256: record.schemaSha256,
    erpTables: validateTableEvidenceMap(record.erpTables, ERP_DATA_TABLES, "preserved ERP tables"),
    sharedNonSalesRows: validateTableEvidenceMap(
      record.sharedNonSalesRows,
      SHARED_IMPORT_TABLES,
      "preserved non-sales rows",
    ),
  };
}

function validateAuthorityEvidence(value: unknown): SalesD1RetirementAudit["authority"] {
  const record = exactObject(value, ["owner", "epoch", "cutoverId", "updatedAt", "blockers"], "audit authority");
  const blockers = exactObject(record.blockers, AUTHORITY_BLOCKERS, "audit authority blockers");
  for (const name of AUTHORITY_BLOCKERS) {
    if (safeInteger(blockers[name], `audit blocker ${name}`) !== 0) {
      throw new Error("audit authority blockers 必须全部为零");
    }
  }
  if (record.owner !== "postgresql" || typeof record.cutoverId !== "string") {
    throw new Error("audit authority 终态无效");
  }
  return {
    owner: "postgresql",
    epoch: safeInteger(record.epoch, "audit authority epoch", 1),
    cutoverId: record.cutoverId,
    updatedAt: timestamp(record.updatedAt, "audit authority updatedAt"),
    blockers: blockers as unknown as SalesD1AuthoritySnapshot["blockers"],
  };
}

function validateAudit(
  value: unknown,
  expected: ResolvedInput,
  attestation: VerifiedAttestation,
  migration: VerifiedMigration,
): SalesD1RetirementAudit {
  const raw = exactObject(value, [
    "version", "auditId", "cutoverId", "sourcePathSha256", "auditOutputPathSha256",
    "approvedPlanId", "sourceCoreEvidenceSha256", "recordedAt", "attestation",
    "smokeReceipt", "postgresqlPreflight", "migration", "authority",
    "retiredEvidence", "preservedEvidence", "result",
  ], "retirement audit");
  const attestationValue = exactObject(raw.attestation, [
    "payloadSha256", "fileSha256", "pathSha256", "observedAt", "authorityEpoch",
    "authorityUpdatedAt", "sourcePathSha256", "sourceFileIdentitySha256",
    "authoritySchemaSha256", "sourceSizeBytes", "migrationApplyRunId",
    "migrationVerifyRunId", "cleanupManifestId", "cleanupManifestSha256",
    "cleanupCoreEvidenceSha256",
  ], "audit attestation");
  const migrationValue = exactObject(raw.migration, [
    "fileSha256", "pathSha256", "statementCount",
  ], "audit migration");
  const smokeReceipt = exactObject(raw.smokeReceipt, ["fileSha256", "pathSha256"], "audit smoke receipt");
  const result = exactObject(raw.result, [
    "retiredTablesAbsent", "retirementTombstoneViewsPresent", "retiredTriggersAbsent",
    "sharedSalesRowsDeleted", "sharedImportRetirementGuardsPresent",
    "preservedEvidenceSha256",
  ], "audit result");
  const authority = validateAuthorityEvidence(raw.authority);
  const retired = validateRetiredEvidence(raw.retiredEvidence);
  const preserved = validatePreservationEvidence(raw.preservedEvidence);
  if (!/^[0-9a-f]{64}$/.test(String(smokeReceipt.fileSha256 ?? ""))
    || !/^[0-9a-f]{64}$/.test(String(smokeReceipt.pathSha256 ?? ""))) {
    throw new Error("audit smoke receipt 摘要无效");
  }
  const approvedPlanId = String(raw.approvedPlanId ?? "");
  const preflight = validatePostgresqlPreflight(raw.postgresqlPreflight, {
    planId: approvedPlanId,
    cutoverId: expected.cutoverId,
    attestationPayloadSha256: attestation.payloadSha256,
    migrationVerifyRunId: attestation.migrationVerifyRunId,
    smokeReceiptSha256: String(smokeReceipt.fileSha256),
  }, { requireFresh: false });
  const audit = {
    ...raw,
    attestation: attestationValue,
    smokeReceipt,
    postgresqlPreflight: preflight,
    migration: migrationValue,
    authority,
    retiredEvidence: retired,
    preservedEvidence: preserved,
    result,
  } as unknown as SalesD1RetirementAudit;
  const expectedPlanId = retirementPlanId({
    input: expected,
    attestation,
    migration,
    authority,
    coreEvidenceSha256: String(raw.sourceCoreEvidenceSha256 ?? ""),
    retired,
    preserved,
  });
  if (audit.version !== RETIREMENT_VERSION
    || !/^[0-9a-f]{64}$/.test(String(audit.auditId ?? ""))
    || audit.auditId !== sha256(canonicalJson(auditCore(audit)))
    || audit.cutoverId !== expected.cutoverId
    || audit.sourcePathSha256 !== expected.sourcePathSha256
    || audit.auditOutputPathSha256 !== sha256(expected.auditOutput)
    || !/^[0-9a-f]{64}$/.test(audit.approvedPlanId)
    || audit.approvedPlanId !== expectedPlanId
    || audit.sourceCoreEvidenceSha256 !== attestation.cleanupCoreEvidenceSha256
    || !evidenceEqual(audit.attestation, attestation)
    || !evidenceEqual(audit.migration, {
      fileSha256: migration.fileSha256,
      pathSha256: migration.pathSha256,
      statementCount: migration.statements.length,
    })
    || canonicalJson(audit.result.retiredTablesAbsent) !== canonicalJson(RETIRED_SALES_TABLES)
    || canonicalJson(audit.result.retirementTombstoneViewsPresent)
      !== canonicalJson(RETIREMENT_TOMBSTONE_VIEWS)
    || canonicalJson(audit.result.retiredTriggersAbsent) !== canonicalJson(RETIRED_SALES_TRIGGERS)
    || canonicalJson(audit.result.sharedSalesRowsDeleted) !== canonicalJson(SHARED_IMPORT_TABLES)
    || canonicalJson(audit.result.sharedImportRetirementGuardsPresent)
      !== canonicalJson(SHARED_IMPORT_RETIREMENT_GUARDS)
    || audit.result.preservedEvidenceSha256 !== sha256(canonicalJson(preserved))
    || audit.authority.cutoverId !== expected.cutoverId
    || audit.authority.epoch !== attestation.authorityEpoch
    || audit.authority.updatedAt !== attestation.authorityUpdatedAt
    || timestamp(audit.recordedAt, "retirement audit recordedAt") !== audit.recordedAt) {
    throw new Error("现有 D1 退役审计不是同一不可变证据");
  }
  return audit;
}

async function readJsonIfExists(filePath: string): Promise<unknown | null> {
  return readFile(filePath, "utf8").then(JSON.parse).catch((error: unknown) => {
    if ((error as NodeJS.ErrnoException)?.code === "ENOENT") return null;
    throw error;
  });
}

async function writePreparedAudit(filePath: string, audit: SalesD1RetirementAudit): Promise<void> {
  const temporary = path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.${randomUUID()}.tmp`,
  );
  let handle: Awaited<ReturnType<typeof open>> | null = null;
  try {
    handle = await open(temporary, "wx");
    await handle.writeFile(`${JSON.stringify(audit, null, 2)}\n`, { encoding: "utf8" });
    await handle.sync();
    await handle.close();
    handle = null;
    await rename(temporary, filePath);
    const published = await open(filePath, "r+");
    try {
      await published.sync();
    } finally {
      await published.close();
    }
  } finally {
    if (handle) await handle.close().catch(() => undefined);
    await rm(temporary, { force: true }).catch(() => undefined);
  }
}

async function archivePreparedAudit(filePath: string, auditId: string): Promise<void> {
  const archived = `${filePath}.abandoned-${auditId.slice(0, 16)}-${randomUUID()}.json`;
  await rename(filePath, archived);
}

async function publishPreparedAudit(preparedPath: string, auditOutput: string): Promise<void> {
  await rename(preparedPath, auditOutput);
  const published = await open(auditOutput, "r+");
  try {
    await published.sync();
  } finally {
    await published.close();
  }
}

function assertPostRetirementState(
  database: DatabaseSync,
  audit: SalesD1RetirementAudit,
  options: { compareHistoricalPreservation: boolean; receiptStatus: "approved" | "completed" },
): void {
  if (sourceRetirementState(database) !== "retired") {
    throw new Error("D1 销售表或 authority 未完整退役");
  }
  const remainingSalesTable = database.prepare(
    "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'sales_*' LIMIT 1",
  ).get() as { name?: string } | undefined;
  if (remainingSalesTable?.name) {
    throw new Error(`D1 仍存在销售表：${remainingSalesTable.name}`);
  }
  assertRetirementTombstoneViews(database);
  for (const trigger of RETIRED_SALES_TRIGGERS) {
    if (triggerExists(database, trigger)) throw new Error(`D1 销售 trigger 未退役：${trigger}`);
  }
  assertSharedImportRetirementGuards(database);
  for (const table of SHARED_IMPORT_TABLES) {
    const count = safeInteger((database.prepare(
      `SELECT COUNT(*) AS count FROM ${quoteIdentifier(table)} WHERE domain='sales'`,
    ).get() as { count?: number } | undefined)?.count, `${table} sales rows`);
    if (count !== 0) throw new Error(`D1 共享导入表 ${table} 仍含 sales 行`);
  }
  assertErpSchemaBoundary(database);
  assertReceiptMatchesAudit(database, audit, options.receiptStatus);
  if (options.compareHistoricalPreservation) {
    const preserved = preservationEvidence(database);
    if (!evidenceEqual(preserved, audit.preservedEvidence)
      || sha256(canonicalJson(preserved)) !== audit.result.preservedEvidenceSha256) {
      throw new Error("D1 ERP、非 sales 共享行或非 sales schema 未完整保留");
    }
  }
}

function planFromEvidence(
  input: ResolvedInput,
  attestation: VerifiedAttestation,
  migration: VerifiedMigration,
  authority: SalesD1AuthoritySnapshot,
  coreEvidenceSha256: string,
  retired: RetiredEvidence,
  preserved: PreservationEvidence,
): SalesD1RetirementPlan {
  const planId = retirementPlanId({
    input,
    attestation,
    migration,
    authority,
    coreEvidenceSha256,
    retired,
    preserved,
  });
  return {
    status: "planned",
    cutoverId: input.cutoverId,
    sourcePathSha256: input.sourcePathSha256,
    migrationSha256: migration.fileSha256,
    attestationSha256: attestation.payloadSha256,
    planId,
    authorityEpoch: authority.epoch,
    blockers: authority.blockers,
    salesTableCounts: Object.fromEntries(
      Object.entries(retired.salesTables).map(([name, evidence]) => [name, evidence.rowCount]),
    ),
    sharedSalesRowCounts: Object.fromEntries(
      Object.entries(retired.sharedSalesRows).map(([name, evidence]) => [name, evidence.rowCount]),
    ),
    preservedEvidenceSha256: sha256(canonicalJson(preserved)),
  };
}

function retirementPlanId(input: {
  input: ResolvedInput;
  attestation: VerifiedAttestation;
  migration: VerifiedMigration;
  authority: SalesD1AuthoritySnapshot;
  coreEvidenceSha256: string;
  retired: RetiredEvidence;
  preserved: PreservationEvidence;
}): string {
  return sha256(canonicalJson({
    version: RETIREMENT_PLAN_VERSION,
    cutoverId: input.input.cutoverId,
    sourcePathSha256: input.input.sourcePathSha256,
    auditOutputPathSha256: sha256(input.input.auditOutput),
    attestationPayloadSha256: input.attestation.payloadSha256,
    migrationSha256: input.migration.fileSha256,
    authority: {
      owner: input.authority.owner,
      epoch: input.authority.epoch,
      cutoverId: input.authority.cutoverId,
      updatedAt: timestamp(input.authority.updatedAt, "D1 authority updatedAt"),
      blockers: input.authority.blockers,
    },
    sourceCoreEvidenceSha256: input.coreEvidenceSha256,
    retiredEvidence: input.retired,
    preservedSchemaEvidence: {
      schemaObjectCount: input.preserved.schemaObjectCount,
      schemaSha256: input.preserved.schemaSha256,
    },
  }));
}

async function loadVerifiedInputs(input: SalesD1RetirementInput): Promise<{
  resolved: ResolvedInput;
  attestation: VerifiedAttestation;
  migration: VerifiedMigration;
}> {
  const resolved = await resolveInput(input);
  const [attestation, migration] = await Promise.all([
    verifiedAttestation(resolved),
    verifiedMigration(resolved),
  ]);
  return { resolved, attestation, migration };
}

const MANAGED_PREFLIGHT_MAX_OUTPUT_BYTES = 64 * 1024;

function assertManagedWriterEnvironment(): void {
  const raw = String(process.env.TERUISI_DJANGO_DATABASE_URL ?? "");
  let databaseUrl: URL;
  try {
    databaseUrl = new URL(raw);
  } catch {
    throw new Error("受控 retirement preflight 缺少有效 writer 数据库环境");
  }
  const queryNames = [...databaseUrl.searchParams.keys()].sort();
  if (databaseUrl.protocol !== "postgresql:"
    || decodeURIComponent(databaseUrl.username) !== "teruisi_sales_writer"
    || !databaseUrl.password
    || databaseUrl.hostname !== "127.0.0.1"
    || databaseUrl.port !== "5432"
    || databaseUrl.pathname !== "/teruisi_sales"
    || canonicalJson(queryNames) !== canonicalJson([
      "application_name", "connect_timeout", "options", "sslmode",
    ])
    || databaseUrl.searchParams.get("sslmode") !== "disable"
    || databaseUrl.searchParams.get("application_name") !== "teruisi_sales_retirement"
    || databaseUrl.searchParams.get("connect_timeout") !== "5"
    || databaseUrl.searchParams.get("options")
      !== "-c statement_timeout=900000 -c idle_in_transaction_session_timeout=905000"
    || process.env.TERUISI_DJANGO_ENVIRONMENT !== "production"
    || process.env.TERUISI_DJANGO_PROCESS_ROLE !== "migration_writer"
    || process.env.TERUISI_DJANGO_EXPECT_READ_ONLY !== "false") {
    throw new Error("受控 retirement preflight writer 环境不符合固定本机契约");
  }
}

function assertManagedRehearsalWriterEnvironment(input: {
  databaseName: string;
  rehearsalId: string;
}): void {
  const expectedDatabase = `teruisi_sales_rehearsal_${input.rehearsalId}`;
  const expectedApplication = `teruisi_sales_rehearsal_retirement_${input.rehearsalId}`;
  if (!/^[0-9a-f]{12}$/.test(input.rehearsalId)
    || input.databaseName !== expectedDatabase) {
    throw new Error("受控 rehearsal retirement 数据库身份无效");
  }
  const raw = String(process.env.TERUISI_DJANGO_DATABASE_URL ?? "");
  let databaseUrl: URL;
  try {
    databaseUrl = new URL(raw);
  } catch {
    throw new Error("受控 rehearsal retirement 缺少有效 writer 数据库环境");
  }
  const queryNames = [...databaseUrl.searchParams.keys()].sort();
  if (databaseUrl.protocol !== "postgresql:"
    || decodeURIComponent(databaseUrl.username) !== "teruisi_sales_writer"
    || !databaseUrl.password
    || databaseUrl.hostname !== "127.0.0.1"
    || databaseUrl.port !== "5432"
    || decodeURIComponent(databaseUrl.pathname.replace(/^\//, "")) !== expectedDatabase
    || canonicalJson(queryNames) !== canonicalJson([
      "application_name", "connect_timeout", "options", "sslmode",
    ])
    || databaseUrl.searchParams.get("sslmode") !== "disable"
    || databaseUrl.searchParams.get("application_name") !== expectedApplication
    || databaseUrl.searchParams.get("connect_timeout") !== "5"
    || databaseUrl.searchParams.get("options")
      !== "-c statement_timeout=900000 -c idle_in_transaction_session_timeout=905000"
    || process.env.TERUISI_DJANGO_ENVIRONMENT !== "production"
    || process.env.TERUISI_DJANGO_PROCESS_ROLE !== "migration_writer"
    || process.env.TERUISI_DJANGO_EXPECT_READ_ONLY !== "false"
    || process.env.TERUISI_DJANGO_RETIREMENT_REHEARSAL_MANAGED !== "1"
    || process.env.TERUISI_DJANGO_RETIREMENT_MANAGED === "1") {
    throw new Error("受控 rehearsal retirement writer 环境不符合隔离本机契约");
  }
}

function isStrictChild(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}

async function spawnExactJson(
  executable: string,
  arguments_: readonly string[],
  cwd: string,
): Promise<unknown> {
  return await new Promise((resolve, reject) => {
    const child = spawn(executable, arguments_, {
      cwd,
      env: process.env,
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 120_000,
      killSignal: "SIGKILL",
    });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let outputBytes = 0;
    let settled = false;
    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      child.kill("SIGKILL");
      reject(error);
    };
    const collect = (target: Buffer[]) => (chunk: Buffer | string) => {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      outputBytes += bytes.length;
      if (outputBytes > MANAGED_PREFLIGHT_MAX_OUTPUT_BYTES) {
        fail(new Error("Django retirement preflight 输出超过上限"));
        return;
      }
      target.push(bytes);
    };
    child.stdout.on("data", collect(stdout));
    child.stderr.on("data", collect(stderr));
    child.once("error", () => fail(new Error("无法启动受控 Django retirement preflight")));
    child.once("close", (code, signal) => {
      if (settled) return;
      settled = true;
      if (code !== 0 || signal !== null || stderr.length !== 0) {
        reject(new Error("受控 Django retirement preflight 未通过"));
        return;
      }
      const output = Buffer.concat(stdout).toString("utf8").trim();
      if (!output || output.split(/\r?\n/).length !== 1) {
        reject(new Error("受控 Django retirement preflight 未返回单一 JSON"));
        return;
      }
      try {
        resolve(JSON.parse(output));
      } catch {
        reject(new Error("受控 Django retirement preflight 返回无效 JSON"));
      }
    });
  });
}

export async function createDjangoRetirementPreflightRunner(options: {
  runtimeRoot: string;
}): Promise<NonNullable<RetirementDependencies["verifyPostgresqlPreflight"]>> {
  if (!path.isAbsolute(options.runtimeRoot)) throw new Error("Django runtime root 必须是绝对路径");
  const runtimeRoot = await realpath(options.runtimeRoot);
  const appRoot = await realpath(path.join(runtimeRoot, "app"));
  const expectedOperator = await realpath(path.join(appRoot, "tools", "sales-d1-retirement.ts"));
  const currentOperator = await realpath(fileURLToPath(import.meta.url));
  if (currentOperator.toLowerCase() !== expectedOperator.toLowerCase()) {
    throw new Error("D1 退役只能从受保护 runtime app operator 执行");
  }
  const deployment = exactObject(
    JSON.parse(await readFile(path.join(appRoot, "deployment.json"), "utf8")),
    [
      "version", "deployedAt", "sourceRoot", "fingerprintAlgorithm",
      "fileCount", "appFingerprint",
    ],
    "runtime deployment manifest",
  );
  if (deployment.version !== 2
    || deployment.fingerprintAlgorithm !== DEPLOYMENT_FINGERPRINT_ALGORITHM
    || typeof deployment.fileCount !== "number"
    || !Number.isSafeInteger(deployment.fileCount)
    || deployment.fileCount < 1
    || typeof deployment.appFingerprint !== "string"
    || !/^[0-9a-f]{64}$/.test(deployment.appFingerprint)) {
    throw new Error("runtime deployment manifest 无效");
  }
  const python = await realpath(path.join(runtimeRoot, "venv", "Scripts", "python.exe"));
  const backendRoot = await realpath(path.join(appRoot, "backend"));
  const manage = await realpath(path.join(backendRoot, "manage.py"));
  if (!(await stat(python)).isFile() || !(await stat(manage)).isFile()) {
    throw new Error("受控 Django retirement preflight runtime 不完整");
  }
  assertManagedWriterEnvironment();
  return async (input) => spawnExactJson(python, [
    manage,
    "sales_cutover_retirement_preflight",
    "--plan-id", input.planId,
    "--cutover-id", input.cutoverId,
    "--attestation-sha256", input.attestationPayloadSha256,
    "--smoke-receipt", input.smokeReceiptPath,
    "--smoke-receipt-sha256", input.smokeReceiptSha256,
  ], backendRoot);
}

export async function executeSalesD1RetirementWithDjangoPreflight(
  input: SalesD1RetirementInput & {
    execute: true;
    approvedPlanId: string;
    smokeReceiptPath: string;
    smokeReceiptSha256: string;
  },
  options: { runtimeRoot: string },
): Promise<{ status: "completed" | "already_completed"; audit: SalesD1RetirementAudit }> {
  if (process.platform !== "win32") {
    throw new Error("受控 D1 retirement execute 只允许固定 Windows 本机 runtime");
  }
  const runtimeRoot = await realpath(options.runtimeRoot);
  if (runtimeRoot.toLowerCase() !== path.win32.normalize(MANAGED_RUNTIME_ROOT).toLowerCase()) {
    throw new Error("受控 D1 retirement execute 的 runtime root 不符合固定部署契约");
  }
  const serviceConfig = exactObject(
    JSON.parse(await readFile(path.join(runtimeRoot, "service.json"), "utf8")),
    [
      "version", "configuredAt", "configuredFrom", "readerAddress",
      "writerAddress", "financeReaderAddress", "financeWriterAddress",
      "customerServiceReaderAddress", "customerServiceWriterAddress",
      "postgresAddress", "erpSourceD1",
    ],
    "runtime service config",
  );
  if (serviceConfig.version !== 5
    || serviceConfig.readerAddress !== "127.0.0.1:8001"
    || serviceConfig.writerAddress !== "127.0.0.1:8002"
    || serviceConfig.financeReaderAddress !== "127.0.0.1:8011"
    || serviceConfig.financeWriterAddress !== "127.0.0.1:8012"
    || serviceConfig.customerServiceReaderAddress !== "127.0.0.1:8071"
    || serviceConfig.customerServiceWriterAddress !== "127.0.0.1:8072"
    || serviceConfig.postgresAddress !== "127.0.0.1:5432"
    || typeof serviceConfig.erpSourceD1 !== "string"
    || !path.isAbsolute(serviceConfig.erpSourceD1)
    || path.extname(serviceConfig.erpSourceD1).toLowerCase() !== ".sqlite"
    || (await realpath(serviceConfig.erpSourceD1)).toLowerCase()
      !== (await realpath(input.source)).toLowerCase()) {
    throw new Error("受控 D1 retirement source 与固定服务 ERP D1 配置不一致");
  }
  const verifyPostgresqlPreflight = await createDjangoRetirementPreflightRunner(options);
  return executeSalesD1RetirementInternal(input, { verifyPostgresqlPreflight });
}

type ManagedRehearsalOptions = {
  runtimeRoot: string;
  rehearsalRoot: string;
  rehearsalId: string;
  databaseName: string;
};

async function resolveManagedRehearsalContext(
  input: SalesD1RetirementInput & {
    execute: true;
    approvedPlanId: string;
    smokeReceiptPath: string;
    smokeReceiptSha256: string;
  },
  options: ManagedRehearsalOptions,
): Promise<{
  runtimeRoot: string;
  appRoot: string;
  backendRoot: string;
  python: string;
  source: string;
  attestationPath: string;
  smokeReceiptPath: string;
  auditOutput: string;
}> {
  if (process.platform !== "win32") {
    throw new Error("受控 rehearsal retirement 只允许固定 Windows 本机 runtime");
  }
  if (!/^[0-9a-f]{12}$/.test(options.rehearsalId)
    || options.databaseName !== `teruisi_sales_rehearsal_${options.rehearsalId}`
    || input.cutoverId !== `rehearsal-${options.rehearsalId}`) {
    throw new Error("受控 rehearsal retirement 身份不匹配");
  }
  const runtimeRoot = await realpath(options.runtimeRoot);
  if (runtimeRoot.toLowerCase() !== path.win32.normalize(MANAGED_RUNTIME_ROOT).toLowerCase()) {
    throw new Error("受控 rehearsal retirement runtime root 不符合固定部署契约");
  }
  const expectedRehearsalRoot = path.join(runtimeRoot, "rehearsals", options.rehearsalId);
  const rehearsalRoot = await realpath(options.rehearsalRoot);
  if (rehearsalRoot.toLowerCase() !== expectedRehearsalRoot.toLowerCase()) {
    throw new Error("受控 rehearsal retirement 根目录不符合固定隔离契约");
  }
  const appRoot = await realpath(path.join(runtimeRoot, "app"));
  const expectedOperator = await realpath(path.join(appRoot, "tools", "sales-d1-retirement.ts"));
  const currentOperator = await realpath(fileURLToPath(import.meta.url));
  if (currentOperator.toLowerCase() !== expectedOperator.toLowerCase()) {
    throw new Error("rehearsal retirement 只能从受保护 runtime app operator 执行");
  }
  const deployment = exactObject(
    JSON.parse(await readFile(path.join(appRoot, "deployment.json"), "utf8")),
    [
      "version", "deployedAt", "sourceRoot", "fingerprintAlgorithm",
      "fileCount", "appFingerprint",
    ],
    "runtime deployment manifest",
  );
  if (deployment.version !== 2
    || deployment.fingerprintAlgorithm !== DEPLOYMENT_FINGERPRINT_ALGORITHM
    || typeof deployment.fileCount !== "number"
    || !Number.isSafeInteger(deployment.fileCount)
    || deployment.fileCount < 1
    || typeof deployment.appFingerprint !== "string"
    || !/^[0-9a-f]{64}$/.test(deployment.appFingerprint)) {
    throw new Error("runtime deployment manifest 无效");
  }
  const serviceConfig = exactObject(
    JSON.parse(await readFile(path.join(runtimeRoot, "service.json"), "utf8")),
    [
      "version", "configuredAt", "configuredFrom", "readerAddress",
      "writerAddress", "financeReaderAddress", "financeWriterAddress",
      "customerServiceReaderAddress", "customerServiceWriterAddress",
      "postgresAddress", "erpSourceD1",
    ],
    "runtime service config",
  );
  if (serviceConfig.version !== 5
    || serviceConfig.readerAddress !== "127.0.0.1:8001"
    || serviceConfig.writerAddress !== "127.0.0.1:8002"
    || serviceConfig.financeReaderAddress !== "127.0.0.1:8011"
    || serviceConfig.financeWriterAddress !== "127.0.0.1:8012"
    || serviceConfig.customerServiceReaderAddress !== "127.0.0.1:8071"
    || serviceConfig.customerServiceWriterAddress !== "127.0.0.1:8072"
    || serviceConfig.postgresAddress !== "127.0.0.1:5432"
    || typeof serviceConfig.erpSourceD1 !== "string"
    || !path.isAbsolute(serviceConfig.erpSourceD1)) {
    throw new Error("runtime service config 不符合 rehearsal 固定契约");
  }
  const productionSource = await realpath(serviceConfig.erpSourceD1);
  const source = await realpath(input.source);
  const d1Root = await realpath(path.join(rehearsalRoot, ".wrangler", "state", "v3", "d1"));
  const r2Root = await realpath(path.join(rehearsalRoot, ".wrangler", "state", "v3", "r2"));
  if (source.toLowerCase() === productionSource.toLowerCase()
    || path.extname(source).toLowerCase() !== ".sqlite"
    || !isStrictChild(d1Root, source)
    || !(await stat(source)).isFile()
    || !(await stat(r2Root)).isDirectory()) {
    throw new Error("rehearsal retirement D1/R2 未绑定到独立复制根");
  }
  const auditRoot = await realpath(path.join(rehearsalRoot, "audit"));
  const attestationPath = await realpath(input.attestationPath);
  const smokeReceiptPath = await realpath(input.smokeReceiptPath);
  const auditParent = await realpath(path.dirname(input.auditOutput));
  const auditOutput = path.join(auditParent, path.basename(input.auditOutput));
  if (!isStrictChild(auditRoot, attestationPath)
    || !isStrictChild(auditRoot, smokeReceiptPath)
    || auditParent.toLowerCase() !== auditRoot.toLowerCase()
    || path.extname(auditOutput).toLowerCase() !== ".json"
    || (input.repositoryRoot
      && (await realpath(input.repositoryRoot)).toLowerCase() !== appRoot.toLowerCase())) {
    throw new Error("rehearsal retirement 证据路径越过隔离 audit/runtime app 边界");
  }
  const existingAudit = await stat(auditOutput).catch(() => null);
  if (existingAudit && (!(existingAudit).isFile()
    || (await realpath(auditOutput)).toLowerCase() !== auditOutput.toLowerCase())) {
    throw new Error("rehearsal retirement audit output 身份无效");
  }
  const python = await realpath(path.join(runtimeRoot, "venv", "Scripts", "python.exe"));
  const backendRoot = await realpath(path.join(appRoot, "backend"));
  const manage = await realpath(path.join(backendRoot, "manage.py"));
  if (!(await stat(python)).isFile() || !(await stat(manage)).isFile()) {
    throw new Error("受控 rehearsal Django preflight runtime 不完整");
  }
  return {
    runtimeRoot,
    appRoot,
    backendRoot,
    python,
    source,
    attestationPath,
    smokeReceiptPath,
    auditOutput,
  };
}

async function createDjangoRehearsalPreflightRunner(input: {
  python: string;
  backendRoot: string;
  databaseName: string;
  rehearsalId: string;
}): Promise<NonNullable<RetirementDependencies["verifyPostgresqlPreflight"]>> {
  const manage = await realpath(path.join(input.backendRoot, "manage.py"));
  const databaseProbe = [
    "import json, os",
    "import psycopg",
    "with psycopg.connect(os.environ['TERUISI_DJANGO_DATABASE_URL']) as connection:",
    "    with connection.cursor() as cursor:",
    "        cursor.execute('SELECT current_user, current_database()')",
    "        user, database = cursor.fetchone()",
    "print(json.dumps({'currentUser': user, 'currentDatabase': database}, separators=(',', ':')))",
  ].join("\n");
  return async (preflightInput) => {
    assertManagedRehearsalWriterEnvironment(input);
    const databaseIdentity = exactObject(
      await spawnExactJson(input.python, ["-c", databaseProbe], input.backendRoot),
      ["currentUser", "currentDatabase"],
      "rehearsal PostgreSQL identity",
    );
    if (databaseIdentity.currentUser !== "teruisi_sales_writer"
      || databaseIdentity.currentDatabase !== input.databaseName) {
      throw new Error("rehearsal retirement current_user/current_database 回查失败");
    }
    return spawnExactJson(input.python, [
      manage,
      "sales_cutover_retirement_preflight",
      "--plan-id", preflightInput.planId,
      "--cutover-id", preflightInput.cutoverId,
      "--attestation-sha256", preflightInput.attestationPayloadSha256,
      "--smoke-receipt", preflightInput.smokeReceiptPath,
      "--smoke-receipt-sha256", preflightInput.smokeReceiptSha256,
    ], input.backendRoot);
  };
}

export async function executeSalesD1RetirementRehearsalWithDjangoPreflight(
  input: SalesD1RetirementInput & {
    execute: true;
    approvedPlanId: string;
    smokeReceiptPath: string;
    smokeReceiptSha256: string;
  },
  options: ManagedRehearsalOptions,
): Promise<{ status: "completed" | "already_completed"; audit: SalesD1RetirementAudit }> {
  const context = await resolveManagedRehearsalContext(input, options);
  assertManagedRehearsalWriterEnvironment(options);
  const verifyPostgresqlPreflight = await createDjangoRehearsalPreflightRunner({
    python: context.python,
    backendRoot: context.backendRoot,
    databaseName: options.databaseName,
    rehearsalId: options.rehearsalId,
  });
  return executeSalesD1RetirementInternal({
    ...input,
    source: context.source,
    attestationPath: context.attestationPath,
    smokeReceiptPath: context.smokeReceiptPath,
    auditOutput: context.auditOutput,
    repositoryRoot: context.appRoot,
  }, { verifyPostgresqlPreflight });
}

export async function planSalesD1Retirement(
  input: SalesD1RetirementInput,
): Promise<SalesD1RetirementPlan> {
  const { resolved, attestation, migration } = await loadVerifiedInputs(input);
  const finalValue = await readJsonIfExists(resolved.auditOutput);
  const preparedValue = await readJsonIfExists(resolved.preparedAudit);
  const database = new DatabaseSync(resolved.source, { readOnly: true });
  try {
    const state = sourceRetirementState(database);
    if (state === "partial") throw new Error("D1 销售域部分退役且无可接受终态");
    if (finalValue) {
      const audit = validateAudit(finalValue, resolved, attestation, migration);
      if (preparedValue) {
        const prepared = validateAudit(preparedValue, resolved, attestation, migration);
        if (prepared.auditId !== audit.auditId) {
          throw new Error("final 与 prepared D1 退役审计证据冲突");
        }
      }
      assertPostRetirementState(database, audit, {
        compareHistoricalPreservation: false,
        receiptStatus: "completed",
      });
      return {
        status: "already_completed",
        cutoverId: resolved.cutoverId,
        sourcePathSha256: resolved.sourcePathSha256,
        migrationSha256: migration.fileSha256,
        attestationSha256: attestation.payloadSha256,
        planId: audit.approvedPlanId,
        auditId: audit.auditId,
      };
    }
    if (state === "retired") {
      if (!preparedValue) throw new Error("D1 已退役但缺少同一不可变审计证据，拒绝猜测完成");
      const audit = validateAudit(preparedValue, resolved, attestation, migration);
      assertPostRetirementState(database, audit, {
        compareHistoricalPreservation: false,
        receiptStatus: "completed",
      });
      return {
        status: "recovery_required",
        cutoverId: resolved.cutoverId,
        sourcePathSha256: resolved.sourcePathSha256,
        migrationSha256: migration.fileSha256,
        attestationSha256: attestation.payloadSha256,
        planId: audit.approvedPlanId,
        auditId: audit.auditId,
      };
    }
    const before = assertPreRetirementState(database, resolved, attestation);
    const retired = retiredEvidence(database);
    const preserved = preservationEvidence(database);
    if (preparedValue) {
      validateAudit(preparedValue, resolved, attestation, migration);
    }
    if (tableExists(database, RETIREMENT_RECEIPT_TABLE)) {
      assertRetirementReceiptSchema(database);
      if (retirementReceipt(database)) throw new Error("未退役 D1 不得已有 sales receipt");
    }
    return planFromEvidence(
      resolved,
      attestation,
      migration,
      before.authority,
      before.coreEvidenceSha256,
      retired,
      preserved,
    );
  } finally {
    database.close();
  }
}

async function executeSalesD1RetirementInternal(
  input: SalesD1RetirementInput & {
    execute: boolean;
    approvedPlanId: string;
    smokeReceiptPath: string;
    smokeReceiptSha256: string;
  },
  dependencies: RetirementDependencies = {},
): Promise<{ status: "completed" | "already_completed"; audit: SalesD1RetirementAudit }> {
  if (input.execute !== true || !/^[0-9a-f]{64}$/.test(input.approvedPlanId)) {
    throw new Error("真实退役必须显式 execute=true 并提供已审核 approvedPlanId");
  }
  const { resolved, attestation, migration } = await loadVerifiedInputs(input);
  const finalValue = await readJsonIfExists(resolved.auditOutput);
  let preparedValue = await readJsonIfExists(resolved.preparedAudit);
  const initialDatabase = new DatabaseSync(resolved.source, { readOnly: true });
  let initialPlan: SalesD1RetirementPlan;
  try {
    const initialState = sourceRetirementState(initialDatabase);
    if (initialState === "partial") throw new Error("D1 销售域部分退役且无可接受终态");
    if (finalValue) {
      const audit = validateAudit(finalValue, resolved, attestation, migration);
      if (preparedValue) {
        const prepared = validateAudit(preparedValue, resolved, attestation, migration);
        if (prepared.auditId !== audit.auditId) throw new Error("final 与 prepared D1 退役审计证据冲突");
      }
      assertPostRetirementState(initialDatabase, audit, {
        compareHistoricalPreservation: false,
        receiptStatus: "completed",
      });
      return { status: "already_completed", audit };
    }
    if (initialState === "retired") {
      if (!preparedValue) throw new Error("D1 已退役但缺少同一不可变审计证据，拒绝猜测完成");
      const audit = validateAudit(preparedValue, resolved, attestation, migration);
      assertPostRetirementState(initialDatabase, audit, {
        compareHistoricalPreservation: false,
        receiptStatus: "completed",
      });
      await publishPreparedAudit(resolved.preparedAudit, resolved.auditOutput);
      return { status: "already_completed", audit };
    }
    const before = assertPreRetirementState(initialDatabase, resolved, attestation);
    if (tableExists(initialDatabase, RETIREMENT_RECEIPT_TABLE)) {
      assertRetirementReceiptSchema(initialDatabase);
      if (retirementReceipt(initialDatabase)) throw new Error("未退役 D1 不得已有 sales receipt");
    }
    const retired = retiredEvidence(initialDatabase);
    const preserved = preservationEvidence(initialDatabase);
    initialPlan = planFromEvidence(
      resolved,
      attestation,
      migration,
      before.authority,
      before.coreEvidenceSha256,
      retired,
      preserved,
    );
  } finally {
    initialDatabase.close();
  }
  if (initialPlan.status !== "planned" || initialPlan.planId !== input.approvedPlanId) {
    throw new Error("approvedPlanId 与当前不可变 D1 退役计划不一致");
  }
  const smoke = await verifiedSmokeReceipt(input.smokeReceiptPath, input.smokeReceiptSha256);
  if (typeof dependencies.verifyPostgresqlPreflight !== "function") {
    throw new Error("缺少受控 PostgreSQL retirement preflight capability，拒绝写入 D1");
  }
  const preflightValue = await dependencies.verifyPostgresqlPreflight({
    planId: input.approvedPlanId,
    cutoverId: resolved.cutoverId,
    attestationPayloadSha256: attestation.payloadSha256,
    migrationVerifyRunId: attestation.migrationVerifyRunId,
    smokeReceiptPath: smoke.path,
    smokeReceiptSha256: smoke.evidence.fileSha256,
  });
  const observedNow = (dependencies.now ?? (() => new Date()))();
  const preflight = validatePostgresqlPreflight(preflightValue, {
    planId: input.approvedPlanId,
    cutoverId: resolved.cutoverId,
    attestationPayloadSha256: attestation.payloadSha256,
    migrationVerifyRunId: attestation.migrationVerifyRunId,
    smokeReceiptSha256: smoke.evidence.fileSha256,
  }, { now: observedNow, requireFresh: true });

  const database = new DatabaseSync(resolved.source);
  let transactionOpen = false;
  let committed = false;
  try {
    database.exec("BEGIN IMMEDIATE");
    transactionOpen = true;
    const before = assertPreRetirementState(database, resolved, attestation);
    const beforeRetired = retiredEvidence(database);
    const beforePreserved = preservationEvidence(database);
    const lockedPlanId = retirementPlanId({
      input: resolved,
      attestation,
      migration,
      authority: before.authority,
      coreEvidenceSha256: before.coreEvidenceSha256,
      retired: beforeRetired,
      preserved: beforePreserved,
    });
    if (lockedPlanId !== input.approvedPlanId) {
      throw new Error("写锁内 D1 证据已变化，approvedPlanId 失效");
    }
    const lockedNow = (dependencies.now ?? (() => new Date()))();
    validatePostgresqlPreflight(preflight, {
      planId: input.approvedPlanId,
      cutoverId: resolved.cutoverId,
      attestationPayloadSha256: attestation.payloadSha256,
      migrationVerifyRunId: attestation.migrationVerifyRunId,
      smokeReceiptSha256: smoke.evidence.fileSha256,
    }, { now: lockedNow, requireFresh: true });
    ensureRetirementReceiptSchema(database);
    assertRetirementReceiptSchema(database);
    if (retirementReceipt(database)) throw new Error("未退役 D1 不得已有 sales receipt");
    if (preparedValue) {
      const abandoned = validateAudit(preparedValue, resolved, attestation, migration);
      await archivePreparedAudit(resolved.preparedAudit, abandoned.auditId);
      preparedValue = null;
    }
    const recordedAt = lockedNow.toISOString();
    const core: Omit<SalesD1RetirementAudit, "auditId"> = {
      version: RETIREMENT_VERSION,
      cutoverId: resolved.cutoverId,
      sourcePathSha256: resolved.sourcePathSha256,
      auditOutputPathSha256: sha256(resolved.auditOutput),
      approvedPlanId: input.approvedPlanId,
      sourceCoreEvidenceSha256: before.coreEvidenceSha256,
      recordedAt,
      attestation,
      smokeReceipt: smoke.evidence,
      postgresqlPreflight: preflight,
      migration: {
        fileSha256: migration.fileSha256,
        pathSha256: migration.pathSha256,
        statementCount: migration.statements.length,
      },
      authority: {
        owner: "postgresql",
        epoch: before.authority.epoch,
        cutoverId: before.authority.cutoverId,
        updatedAt: timestamp(before.authority.updatedAt, "D1 authority updatedAt"),
        blockers: before.authority.blockers,
      },
      retiredEvidence: beforeRetired,
      preservedEvidence: beforePreserved,
      result: {
        retiredTablesAbsent: [...RETIRED_SALES_TABLES],
        retirementTombstoneViewsPresent: [...RETIREMENT_TOMBSTONE_VIEWS],
        retiredTriggersAbsent: [...RETIRED_SALES_TRIGGERS],
        sharedSalesRowsDeleted: [...SHARED_IMPORT_TABLES],
        sharedImportRetirementGuardsPresent: [...SHARED_IMPORT_RETIREMENT_GUARDS],
        preservedEvidenceSha256: sha256(canonicalJson(beforePreserved)),
      },
    };
    const audit: SalesD1RetirementAudit = { ...core, auditId: sha256(canonicalJson(core)) };
    await writePreparedAudit(resolved.preparedAudit, audit);
    preparedValue = audit;
    insertApprovedRetirementReceipt(database, audit);

    for (let index = 0; index < migration.statements.length; index += 1) {
      const statement = migration.statements[index];
      database.exec(statement);
      dependencies.afterStatement?.(index, statement, database);
    }
    assertPostRetirementState(database, audit, {
      compareHistoricalPreservation: true,
      receiptStatus: "approved",
    });
    completeRetirementReceipt(database, audit, lockedNow.toISOString());
    assertPostRetirementState(database, audit, {
      compareHistoricalPreservation: true,
      receiptStatus: "completed",
    });
    database.exec("COMMIT");
    transactionOpen = false;
    committed = true;
    await publishPreparedAudit(resolved.preparedAudit, resolved.auditOutput);
    return { status: "completed", audit };
  } catch (error) {
    if (transactionOpen) {
      database.exec("ROLLBACK");
      transactionOpen = false;
    }
    if (committed) {
      throw new Error(
        `D1 已提交退役但审计发布待恢复：${error instanceof Error ? error.message : "未知错误"}`,
      );
    }
    throw error;
  } finally {
    database.close();
  }
}

/**
 * Test-only seam for exercising transaction and response-loss behavior with a
 * deterministic preflight double.  It is intentionally unable to target a
 * source outside the OS temporary directory; the production export above
 * always constructs its preflight runner from the protected Django runtime.
 */
export async function executeSalesD1RetirementForTest(
  input: SalesD1RetirementInput & {
    execute: boolean;
    approvedPlanId: string;
    smokeReceiptPath: string;
    smokeReceiptSha256: string;
  },
  dependencies: RetirementDependencies = {},
): Promise<{ status: "completed" | "already_completed"; audit: SalesD1RetirementAudit }> {
  if (!process.env.NODE_TEST_CONTEXT) {
    throw new Error("test-only retirement seam 只能由 Node test runner 调用");
  }
  const temporaryRoot = await realpath(tmpdir());
  const resolvedSource = await realpath(input.source);
  const relativeSource = path.relative(temporaryRoot, resolvedSource);
  if (!relativeSource || relativeSource.startsWith("..") || path.isAbsolute(relativeSource)) {
    throw new Error("test-only retirement seam 只能操作 OS 临时目录内的 SQLite fixture");
  }
  return executeSalesD1RetirementInternal(input, dependencies);
}

export function parseSalesD1RetirementArguments(argv: readonly string[]): {
  mode: "plan";
  input: SalesD1RetirementInput;
} {
  const values = new Map<string, string>();
  const valueNames = new Set([
    "--source",
    "--cutover-id",
    "--attestation",
    "--attestation-sha256",
    "--audit-output",
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--execute" || key === "--confirmed-postgresql-smoke") {
      throw new Error("CLI 只允许生成 plan；真实退役必须由受控 PG preflight capability 调用");
    }
    if (!key || !valueNames.has(key)) {
      throw new Error(`未知参数：${key ?? ""}`);
    }
    if (values.has(key)) throw new Error(`参数重复：${key}`);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${key} 缺少参数值`);
    values.set(key, value);
    index += 1;
  }
  return {
    mode: "plan",
    input: {
      source: values.get("--source") ?? "",
      cutoverId: values.get("--cutover-id") ?? "",
      attestationPath: values.get("--attestation") ?? "",
      attestationSha256: values.get("--attestation-sha256") ?? "",
      auditOutput: values.get("--audit-output") ?? "",
    },
  };
}

function parseManagedExecutionArguments(argv: readonly string[]): {
  runtimeRoot: string;
  input: SalesD1RetirementInput & {
    execute: true;
    approvedPlanId: string;
    smokeReceiptPath: string;
    smokeReceiptSha256: string;
  };
} {
  if (argv[0] !== "--managed-execute"
    || process.env.TERUISI_DJANGO_RETIREMENT_MANAGED !== "1") {
    throw new Error("managed retirement execute 只能由受控本机服务 action 调用");
  }
  const names = new Set([
    "--runtime-root", "--source", "--cutover-id", "--attestation",
    "--attestation-sha256", "--audit-output", "--approved-plan-id",
    "--smoke-receipt", "--smoke-receipt-sha256",
  ]);
  const values = new Map<string, string>();
  for (let index = 1; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key || !names.has(key)) throw new Error(`managed retirement 参数无效：${key ?? ""}`);
    if (values.has(key)) throw new Error(`managed retirement 参数重复：${key}`);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${key} 缺少参数值`);
    values.set(key, value);
    index += 1;
  }
  if ([...names].some((name) => !values.has(name))) {
    throw new Error("managed retirement 参数集合不完整");
  }
  const runtimeRoot = values.get("--runtime-root")!;
  return {
    runtimeRoot,
    input: {
      source: values.get("--source")!,
      cutoverId: values.get("--cutover-id")!,
      attestationPath: values.get("--attestation")!,
      attestationSha256: values.get("--attestation-sha256")!,
      auditOutput: values.get("--audit-output")!,
      repositoryRoot: path.join(runtimeRoot, "app"),
      execute: true,
      approvedPlanId: values.get("--approved-plan-id")!,
      smokeReceiptPath: values.get("--smoke-receipt")!,
      smokeReceiptSha256: values.get("--smoke-receipt-sha256")!,
    },
  };
}

function parseManagedRehearsalExecutionArguments(argv: readonly string[]): {
  options: ManagedRehearsalOptions;
  input: SalesD1RetirementInput & {
    execute: true;
    approvedPlanId: string;
    smokeReceiptPath: string;
    smokeReceiptSha256: string;
  };
} {
  if (argv[0] !== "--managed-rehearsal-execute"
    || process.env.TERUISI_DJANGO_RETIREMENT_REHEARSAL_MANAGED !== "1"
    || process.env.TERUISI_DJANGO_RETIREMENT_MANAGED === "1") {
    throw new Error("managed rehearsal retirement 只能由受控隔离演练 operator 调用");
  }
  const names = new Set([
    "--runtime-root", "--rehearsal-root", "--rehearsal-id", "--database-name",
    "--source", "--cutover-id", "--attestation", "--attestation-sha256",
    "--audit-output", "--approved-plan-id", "--smoke-receipt",
    "--smoke-receipt-sha256",
  ]);
  const values = new Map<string, string>();
  for (let index = 1; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key || !names.has(key)) {
      throw new Error(`managed rehearsal retirement 参数无效：${key ?? ""}`);
    }
    if (values.has(key)) throw new Error(`managed rehearsal retirement 参数重复：${key}`);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${key} 缺少参数值`);
    values.set(key, value);
    index += 1;
  }
  if ([...names].some((name) => !values.has(name))) {
    throw new Error("managed rehearsal retirement 参数集合不完整");
  }
  const runtimeRoot = values.get("--runtime-root")!;
  const rehearsalRoot = values.get("--rehearsal-root")!;
  const rehearsalId = values.get("--rehearsal-id")!;
  const databaseName = values.get("--database-name")!;
  return {
    options: { runtimeRoot, rehearsalRoot, rehearsalId, databaseName },
    input: {
      source: values.get("--source")!,
      cutoverId: values.get("--cutover-id")!,
      attestationPath: values.get("--attestation")!,
      attestationSha256: values.get("--attestation-sha256")!,
      auditOutput: values.get("--audit-output")!,
      repositoryRoot: path.join(runtimeRoot, "app"),
      execute: true,
      approvedPlanId: values.get("--approved-plan-id")!,
      smokeReceiptPath: values.get("--smoke-receipt")!,
      smokeReceiptSha256: values.get("--smoke-receipt-sha256")!,
    },
  };
}

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  if (argv[0] === "--managed-execute") {
    const parsed = parseManagedExecutionArguments(argv);
    const result = await executeSalesD1RetirementWithDjangoPreflight(parsed.input, {
      runtimeRoot: parsed.runtimeRoot,
    });
    process.stdout.write(`${JSON.stringify({
      status: result.status,
      cutoverId: result.audit.cutoverId,
      approvedPlanId: result.audit.approvedPlanId,
      auditId: result.audit.auditId,
      auditOutputPathSha256: result.audit.auditOutputPathSha256,
    })}\n`);
    return;
  }
  if (argv[0] === "--managed-rehearsal-execute") {
    const parsed = parseManagedRehearsalExecutionArguments(argv);
    const result = await executeSalesD1RetirementRehearsalWithDjangoPreflight(
      parsed.input,
      parsed.options,
    );
    process.stdout.write(`${JSON.stringify({
      status: result.status,
      cutoverId: result.audit.cutoverId,
      approvedPlanId: result.audit.approvedPlanId,
      auditId: result.audit.auditId,
      auditOutputPathSha256: result.audit.auditOutputPathSha256,
      preservedEvidenceSha256: result.audit.result.preservedEvidenceSha256,
    })}\n`);
    return;
  }
  const parsed = parseSalesD1RetirementArguments(argv);
  process.stdout.write(`${JSON.stringify(await planSalesD1Retirement(parsed.input))}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error: unknown) => {
    process.stderr.write(`${error instanceof Error ? error.message : "D1 销售域退役失败"}\n`);
    process.exitCode = 1;
  });
}
