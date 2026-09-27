import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { DatabaseSync } from "node:sqlite";

import {
  inspectSalesD1WriteAuthority,
  transitionSalesD1WriteAuthority,
  transitionSalesD1WriteAuthorityInOpenTransaction,
  type SalesD1AuthoritySnapshot,
// @ts-expect-error Node 24's native TypeScript loader requires the deployed .ts extension.
} from "./sales-d1-write-authority.ts";
import {
  executeLegacySalesR2Cleanup,
  LEGACY_SALES_R2_BUCKET,
  planLegacySalesR2Cleanup,
  readLegacySalesR2CleanupManifest,
  type LocalR2Client,
// @ts-expect-error Node 24's native TypeScript loader requires the deployed .ts extension.
} from "./sales-legacy-r2-cleanup.ts";

const STATE_VERSION = "sales-local-cutover-v1" as const;
export const SALES_CUTOVER_MAINTENANCE_PORTS = [3000, 5791, 8001, 8002] as const;
export const D1_CUTOVER_PRE_SCHEMA_FILES = [
  "0090_sales_write_authority.sql",
  "0091_erp_reference_projection.sql",
] as const;
const D1_CUTOVER_PRE_SCHEMA_FILE_SHA256: Readonly<Record<
  (typeof D1_CUTOVER_PRE_SCHEMA_FILES)[number],
  string
>> = {
  "0090_sales_write_authority.sql": "d631d70b077bca10f78bc08414da9c2e2e61ace8b39f18aaa021cc45fa93d44b",
  "0091_erp_reference_projection.sql": "a9b4749a0bf2019f564f3a37ab9971dce38d8df3317cff99b45b0e83beedb65c",
};
export const D1_AUTHORITY_TRIGGER_COUNT = 36;
export const D1_AUTHORITY_SCHEMA_SHA256 = "8a0896d9f6b20c2b39eae8cbf1ab39faa21d4cb772e8990d40902e8b86d8af17";
const D1_ERP_BRIDGE_OBJECTS = [
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
export const D1_ERP_BRIDGE_SCHEMA_SHA256 = "dd201a4d13d5279b70acfde4eb80a4b4bfa3a28dfede1e9597cfa4e99ffbea9e";

type JsonRecord = Record<string, unknown>;
type CutoverStep = { name: string; completedAt: string; result: JsonRecord };

export type SalesLocalCutoverState = {
  version: typeof STATE_VERSION;
  cutoverId: string;
  sourcePathDigest: string;
  createdAt: string;
  updatedAt: string;
  status: "running" | "completed";
  steps: CutoverStep[];
};

export type DjangoJsonRunner = (arguments_: readonly string[]) => Promise<JsonRecord>;

type ExecuteOptions = {
  source: string;
  cutoverId: string;
  auditDirectory: string;
  backendDirectory: string;
  python: string;
  r2PersistTo: string;
  approvedR2CleanupManifestId: string;
  expectedSourceCanonicalSnapshotSha256?: string;
  repositoryRoot?: string;
};

type ManagedCliMode = "production" | "rehearsal";
type ManagedExecuteOptions = ExecuteOptions & {
  managedMode: ManagedCliMode;
  runtimeRoot: string;
};

type ExecuteDependencies = {
  django?: DjangoJsonRunner;
  erpDjango?: DjangoJsonRunner;
  assertMaintenance?: () => Promise<void>;
  now?: () => Date;
  r2Client?: LocalR2Client;
  verifyExpectedSourceSnapshot?: (
    source: string,
    expectedSnapshotSha256: string,
  ) => Promise<JsonRecord>;
};

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    const encoded = JSON.stringify(value);
    if (encoded === undefined) throw new Error("canonical snapshot 不允许 undefined");
    return encoded;
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map(
    (key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`,
  ).join(",")}}`;
}

function canonicalSnapshotSha256FromDryRun(dryRun: JsonRecord): string {
  const required = [
    "canonicalFormatVersion", "runId", "sourceCounts", "sourceDigests",
    "sourceRevision", "status",
  ];
  if (JSON.stringify(Object.keys(dryRun).sort()) !== JSON.stringify(required.sort())
    || dryRun.status !== "dry_run_completed"
    || dryRun.canonicalFormatVersion !== "sales-projection-v4"
    || !/^\d+:\d+$/.test(String(dryRun.sourceRevision ?? ""))
    || !dryRun.sourceCounts || typeof dryRun.sourceCounts !== "object" || Array.isArray(dryRun.sourceCounts)
    || !dryRun.sourceDigests || typeof dryRun.sourceDigests !== "object" || Array.isArray(dryRun.sourceDigests)) {
    throw new Error("Django sales dry-run canonical snapshot 契约无效");
  }
  const counts = dryRun.sourceCounts as Record<string, unknown>;
  const digests = dryRun.sourceDigests as Record<string, unknown>;
  const keys = Object.keys(counts).sort();
  if (keys.length === 0 || JSON.stringify(keys) !== JSON.stringify(Object.keys(digests).sort())) {
    throw new Error("Django sales dry-run canonical counts/digests 字段不一致");
  }
  for (const key of keys) {
    if (!Number.isSafeInteger(counts[key]) || Number(counts[key]) < 0
      || typeof digests[key] !== "string" || !/^[0-9a-f]{64}$/.test(digests[key])) {
      throw new Error("Django sales dry-run canonical counts/digests 值无效");
    }
  }
  return sha256(canonicalJson({
    canonicalFormatVersion: dryRun.canonicalFormatVersion,
    sourceRevision: dryRun.sourceRevision,
    sourceCounts: counts,
    sourceDigests: digests,
  }));
}

function cutoverId(value: string): string {
  const normalized = String(value ?? "").trim();
  if (!/^[A-Za-z0-9._:-]{8,128}$/.test(normalized)) {
    throw new Error("cutoverId 必须是 8 到 128 位安全标识");
  }
  return normalized;
}

function jsonRecord(value: unknown, label: string): JsonRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} 未返回 JSON 对象`);
  return value as JsonRecord;
}

function safeOutput(value: string): string {
  return value
    .replace(/postgres(?:ql)?:\/\/[^@\s]+@/gi, "postgresql://[redacted]@")
    .slice(-16_000);
}

async function writeAtomicJson(filePath: string, value: unknown): Promise<void> {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporary = path.join(path.dirname(filePath), `.${path.basename(filePath)}.${randomUUID()}.tmp`);
  try {
    await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
    await rename(temporary, filePath);
  } finally {
    await rm(temporary, { force: true }).catch(() => undefined);
  }
}

function validateState(input: unknown, expected: { cutoverId: string; sourcePathDigest: string }): SalesLocalCutoverState {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("cutover state 不是对象");
  const state = input as SalesLocalCutoverState;
  if (state.version !== STATE_VERSION
    || state.cutoverId !== expected.cutoverId
    || state.sourcePathDigest !== expected.sourcePathDigest
    || !["running", "completed"].includes(state.status)
    || !Array.isArray(state.steps)) {
    throw new Error("cutover state 身份或契约不匹配");
  }
  const names = new Set<string>();
  for (const step of state.steps) {
    if (!step || typeof step.name !== "string" || !step.name || names.has(step.name)
      || !Number.isFinite(Date.parse(step.completedAt))
      || !step.result || typeof step.result !== "object" || Array.isArray(step.result)) {
      throw new Error("cutover state 步骤无效或重复");
    }
    names.add(step.name);
  }
  return state;
}

async function recordStep(
  statePath: string,
  state: SalesLocalCutoverState,
  name: string,
  result: JsonRecord,
  now: () => Date,
): Promise<SalesLocalCutoverState> {
  if (state.steps.some((step) => step.name === name)) return state;
  const updatedAt = now().toISOString();
  const updated = {
    ...state,
    updatedAt,
    steps: [...state.steps, { name, completedAt: updatedAt, result }],
  };
  await writeAtomicJson(statePath, updated);
  return updated;
}

function tableExists(database: DatabaseSync, name: string): boolean {
  return Boolean(database.prepare(
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
  ).get(name));
}

export function validateD1CutoverPreSchema(database: DatabaseSync): {
  authoritySchemaSha256: string;
  erpBridgeSchemaSha256: string;
} {
  const authorityRows = (database.prepare(
    `SELECT name, sql FROM sqlite_master
     WHERE type = 'trigger' AND name LIKE 'sales_authority_%'
     ORDER BY name COLLATE BINARY`,
  ).all() as Array<{ name: string; sql: string }>).map((row) => [row.name, row.sql ?? ""]);
  const authorityDigest = sha256(JSON.stringify(authorityRows));
  if (authorityRows.length !== D1_AUTHORITY_TRIGGER_COUNT || authorityDigest !== D1_AUTHORITY_SCHEMA_SHA256) {
    throw new Error("D1 0090 authority 写栅栏不完整或语义摘要不匹配");
  }
  const placeholders = D1_ERP_BRIDGE_OBJECTS.map(() => "?").join(",");
  const erpRows = (database.prepare(
    `SELECT type, name, sql FROM sqlite_master
     WHERE name IN (${placeholders})
     ORDER BY type COLLATE BINARY, name COLLATE BINARY`,
  ).all(...D1_ERP_BRIDGE_OBJECTS) as Array<{ type: string; name: string; sql: string }>).map(
    (row) => [row.type, row.name, row.sql ?? ""],
  );
  const erpDigest = sha256(JSON.stringify(erpRows));
  if (erpRows.length !== D1_ERP_BRIDGE_OBJECTS.length || erpDigest !== D1_ERP_BRIDGE_SCHEMA_SHA256) {
    throw new Error("D1 0091 ERP bridge schema 不完整或语义摘要不匹配");
  }
  return { authoritySchemaSha256: authorityDigest, erpBridgeSchemaSha256: erpDigest };
}

async function readD1CutoverPreSchema(repositoryRoot: string): Promise<Array<{ name: string; sql: string }>> {
  if (D1_CUTOVER_PRE_SCHEMA_FILES.some((name) => name.startsWith("0092_") || name.includes("retirement"))) {
    throw new Error("pre-schema 列表不得包含 D1 销售退役迁移 0092");
  }
  const sqlFiles: Array<{ name: string; sql: string }> = [];
  for (const name of D1_CUTOVER_PRE_SCHEMA_FILES) {
    const sqlPath = path.join(repositoryRoot, "drizzle", name);
    const sql = await readFile(sqlPath, "utf8");
    if (sha256(sql) !== D1_CUTOVER_PRE_SCHEMA_FILE_SHA256[name]) {
      throw new Error(`pre-schema 文件 SHA-256 与受审版本不一致：${name}`);
    }
    if (/0092_sales_domain_retirement|DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`"]?sales_/i.test(sql)) {
      throw new Error(`pre-schema 文件包含退役动作：${name}`);
    }
    sqlFiles.push({ name, sql: sql.replaceAll("--> statement-breakpoint", "") });
  }
  return sqlFiles;
}

function applyD1CutoverPreSchemaInOpenTransaction(
  database: DatabaseSync,
  sqlFiles: ReadonlyArray<{ name: string; sql: string }>,
): void {
  for (const item of sqlFiles) database.exec(item.sql);
  validateD1CutoverPreSchema(database);
}

export async function applyD1CutoverPreSchema(input: {
  source: string;
  repositoryRoot: string;
}): Promise<{ files: string[] }> {
  const sqlFiles = await readD1CutoverPreSchema(input.repositoryRoot);
  const database = new DatabaseSync(input.source);
  database.exec("BEGIN IMMEDIATE");
  try {
    applyD1CutoverPreSchemaInOpenTransaction(database, sqlFiles);
    database.exec("COMMIT");
  } catch (error) {
    database.exec("ROLLBACK");
    throw error;
  } finally {
    database.close();
  }
  return { files: sqlFiles.map((item) => item.name) };
}

function inspectD1(source: string): SalesD1AuthoritySnapshot {
  const database = new DatabaseSync(source, { readOnly: true });
  try {
    return inspectSalesD1WriteAuthority(database);
  } finally {
    database.close();
  }
}

function transitionD1(source: string, input: {
  expectedOwner: "d1" | "pending";
  expectedEpoch: number;
  targetOwner: "pending" | "postgresql";
  cutoverId: string;
}): SalesD1AuthoritySnapshot {
  const database = new DatabaseSync(source);
  try {
    return transitionSalesD1WriteAuthority(database, input);
  } finally {
    database.close();
  }
}

async function addressCanListen(port: number, host: string, ipv6Only = false): Promise<boolean> {
  return new Promise((resolve) => {
    const server = net.createServer();
    let settled = false;
    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      server.removeAllListeners();
      resolve(value);
    };
    server.once("error", () => finish(false));
    server.listen({ host, port, exclusive: true, ipv6Only }, () => {
      server.close((error) => finish(!error));
    });
  });
}

export async function portHasAnyListener(
  port: number,
  canListen: (port: number, host: string, ipv6Only?: boolean) => Promise<boolean> = addressCanListen,
): Promise<boolean> {
  if (!(await canListen(port, "0.0.0.0"))) return true;
  return !(await canListen(port, "::", true));
}

export async function assertSalesCutoverMaintenance(): Promise<void> {
  const open = (await Promise.all(SALES_CUTOVER_MAINTENANCE_PORTS.map(
    async (port) => ({ port, open: await portHasAnyListener(port) }),
  )))
    .filter((item) => item.open)
    .map((item) => item.port);
  if (open.length > 0) throw new Error(`销售切换要求 Worker/helper/reader/writer 全部停止；仍监听端口：${open.join(",")}`);
}

async function runProcess(
  command: string,
  args: readonly string[],
  cwd: string,
  environment: NodeJS.ProcessEnv = process.env,
): Promise<JsonRecord> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, [...args], {
      cwd,
      env: environment,
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8").on("data", (chunk) => { stdout += chunk; });
    child.stderr.setEncoding("utf8").on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (Number(code ?? -1) !== 0) {
        reject(new Error(`Django cutover 命令失败：${safeOutput(stderr || stdout)}`));
        return;
      }
      const lines = stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).reverse();
      for (const line of lines) {
        try {
          resolve(jsonRecord(JSON.parse(line), "Django cutover 命令"));
          return;
        } catch {
          // Django may print migration progress before the command JSON.
        }
      }
      // `manage.py migrate` intentionally has no JSON payload.
      resolve({ status: "completed", outputSha256: sha256(stdout) });
    });
  });
}

async function verifyExpectedSourceSnapshot(input: {
  source: string;
  expectedSnapshotSha256: string;
  python: string;
  backendDirectory: string;
  repositoryRoot: string;
}): Promise<JsonRecord> {
  const gatePath = path.join(input.repositoryRoot, "tools", "sales-cutover-snapshot-gate.py");
  const result = await runProcess(input.python, [
    gatePath,
    "verify-expected-live",
    "--backend-dir", input.backendDirectory,
    "--source", input.source,
    "--expected-snapshot-sha256", input.expectedSnapshotSha256,
  ], input.backendDirectory);
  if (result.status !== "verified"
    || result.canonicalFormatVersion !== "sales-projection-v4"
    || result.snapshotSha256 !== input.expectedSnapshotSha256
    || !/^\d+:\d+$/.test(String(result.sourceRevision ?? ""))
    || !Number.isSafeInteger(result.digestKeyCount) || Number(result.digestKeyCount) < 1) {
    throw new Error("写锁内实时 D1 canonical snapshot 门禁结果无效");
  }
  return result;
}

export function createDjangoJsonRunner(input: {
  python: string;
  backendDirectory: string;
  environment?: NodeJS.ProcessEnv;
}): DjangoJsonRunner {
  return (arguments_) => runProcess(
    input.python,
    [path.join(input.backendDirectory, "manage.py"), ...arguments_],
    input.backendDirectory,
    input.environment,
  );
}

export function assertLocalCutoverDatabaseUrl(value: string, expectedUser: string): {
  databaseName: string;
  applicationName: string;
} {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("cutover 数据库连接配置无效");
  }
  const databaseName = decodeURIComponent(parsed.pathname.replace(/^\//, ""));
  const applicationName = parsed.searchParams.get("application_name") ?? "";
  const queryNames = [...parsed.searchParams.keys()].sort();
  if (parsed.protocol !== "postgresql:"
    || parsed.hostname !== "127.0.0.1"
    || parsed.port !== "5432"
    || decodeURIComponent(parsed.username) !== expectedUser
    || !parsed.password
    || !/^teruisi_sales(?:_rehearsal_[0-9a-f]{12})?$/.test(databaseName)
    || JSON.stringify(queryNames) !== JSON.stringify([
      "application_name", "connect_timeout", "options", "sslmode",
    ])
    || parsed.searchParams.get("sslmode") !== "disable"
    || parsed.searchParams.get("connect_timeout") !== "5"
    || parsed.searchParams.get("options")
      !== "-c statement_timeout=900000 -c idle_in_transaction_session_timeout=905000"
    || !/^teruisi_(?:cutover|rehearsal_cutover|cutover_erp|rehearsal_erp)_[0-9a-f]{12}$/.test(applicationName)) {
    throw new Error("cutover 数据库连接必须绑定本机受控角色与生产/隔离演练数据库");
  }
  return { databaseName, applicationName };
}

function createDefaultOwnerDjangoJsonRunner(input: {
  python: string;
  backendDirectory: string;
}): DjangoJsonRunner {
  const databaseUrl = String(process.env.TERUISI_DJANGO_DATABASE_URL ?? "").trim();
  assertLocalCutoverDatabaseUrl(databaseUrl, "teruisi_sales_owner");
  if (process.env.TERUISI_DJANGO_PROCESS_ROLE !== "migration_writer"
    || process.env.TERUISI_DJANGO_EXPECT_READ_ONLY !== "false"
    || process.env.TERUISI_DJANGO_ENVIRONMENT !== "production") {
    throw new Error("cutover 主 Django runner 必须使用 migration_writer 非只读角色");
  }
  return createDjangoJsonRunner({
    ...input,
    environment: { ...process.env, TERUISI_DJANGO_DATABASE_URL: databaseUrl },
  });
}

function createDefaultErpDjangoJsonRunner(input: {
  python: string;
  backendDirectory: string;
}): DjangoJsonRunner {
  const databaseUrl = String(process.env.TERUISI_DJANGO_ERP_DATABASE_URL ?? "").trim();
  assertLocalCutoverDatabaseUrl(databaseUrl, "teruisi_erp_reference_sync");
  const environment: NodeJS.ProcessEnv = {
    ...process.env,
    TERUISI_DJANGO_DATABASE_URL: databaseUrl,
    TERUISI_DJANGO_PROCESS_ROLE: "erp_reference_sync",
    TERUISI_DJANGO_EXPECT_READ_ONLY: "false",
    TERUISI_DJANGO_SALES_AUTHORITY_EPOCH: "",
    TERUISI_DJANGO_SALES_CUTOVER_ID: "",
  };
  delete environment.TERUISI_DJANGO_ERP_DATABASE_URL;
  return createDjangoJsonRunner({ ...input, environment });
}

function pgStatus(payload: JsonRecord): {
  status: "pending" | "active" | "disabled";
  authorityEpoch: string;
  cutoverId: string;
} {
  const status = String(payload.status ?? "");
  const authorityEpoch = String(payload.authorityEpoch ?? "");
  const id = payload.cutoverId == null ? "" : String(payload.cutoverId);
  if (!["pending", "active", "disabled"].includes(status)
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(authorityEpoch)) {
    throw new Error("PostgreSQL authority status 响应无效");
  }
  return { status: status as "pending" | "active" | "disabled", authorityEpoch, cutoverId: id };
}

function erpSyncStatus(payload: JsonRecord): JsonRecord {
  const status = String(payload.status ?? "");
  const sourceEpoch = String(payload.sourceEpoch ?? "");
  const headSequence = Number(payload.headSequence);
  const erpRevision = Number(payload.erpRevision);
  const rowCount = Number(payload.rowCount);
  const contentHash = String(payload.contentHash ?? "");
  if (status !== "caught_up"
    || !/^[0-9a-f]{32}$/.test(sourceEpoch)
    || !Number.isSafeInteger(headSequence) || headSequence < 0
    || !Number.isSafeInteger(erpRevision) || erpRevision < 1
    || !Number.isSafeInteger(rowCount) || rowCount < 0
    || !/^[0-9a-f]{64}$/.test(contentHash)) {
    throw new Error("ERP reference checkpoint 未返回有效的 caught_up 水位");
  }
  return {
    status,
    sourceEpoch,
    headSequence,
    erpRevision,
    rowCount,
    contentHash,
  };
}

export async function ensureErpReferenceCaughtUp(
  django: DjangoJsonRunner,
  source: string,
): Promise<JsonRecord> {
  const statusArguments = [
    "sync_erp_reference", "--source", source, "--status", "--max-age-seconds", "300",
  ] as const;
  try {
    return {
      recoveryMode: "already_caught_up",
      ...erpSyncStatus(await django(statusArguments)),
    };
  } catch {
    // A stale heartbeat or pending event is repaired by the normal, fenced
    // consumer. A missing checkpoint cannot consume and falls through to the
    // one-time identical-baseline initializer. Epoch/path mismatches fail both
    // paths closed and are never rebound automatically.
  }
  try {
    const synchronized = await django(["sync_erp_reference", "--source", source]);
    if (!["up_to_date", "synchronized"].includes(String(synchronized.status ?? ""))) {
      throw new Error("ERP reference sync 未返回完成状态");
    }
    return {
      recoveryMode: "synchronized_existing",
      syncStatus: synchronized.status,
      ...erpSyncStatus(await django(statusArguments)),
    };
  } catch {
    // The only valid remaining case is the first 0091 installation, where the
    // target baseline already matches but no durable checkpoint exists yet.
  }
  const initialized = await django([
    "sync_erp_reference", "--source", source, "--initialize-checkpoint",
  ]);
  if (initialized.status !== "initialized") {
    throw new Error("ERP reference checkpoint 初始化未完成");
  }
  const synchronized = await django(["sync_erp_reference", "--source", source]);
  if (!["up_to_date", "synchronized"].includes(String(synchronized.status ?? ""))) {
    throw new Error("ERP reference checkpoint 初始化后未追平");
  }
  return {
    recoveryMode: "initialized_and_synchronized",
    initializeStatus: initialized.status,
    syncStatus: synchronized.status,
    ...erpSyncStatus(await django(statusArguments)),
  };
}

async function refreshErpReferenceCheckpoint(
  django: DjangoJsonRunner,
  source: string,
  failureContext: string,
): Promise<JsonRecord> {
  const refreshed = await django(["sync_erp_reference", "--source", source]);
  if (!["up_to_date", "synchronized"].includes(String(refreshed.status ?? ""))) {
    throw new Error(`${failureContext} ERP checkpoint 心跳刷新失败`);
  }
  return erpSyncStatus(await django([
    "sync_erp_reference", "--source", source,
    "--status", "--max-age-seconds", "60",
  ]));
}

async function migrateAndVerifyFreshSnapshot(
  django: DjangoJsonRunner,
  source: string,
  statePath: string,
  initialState: SalesLocalCutoverState,
  now: () => Date,
  expectedSnapshotSha256?: string,
): Promise<SalesLocalCutoverState> {
  let state = initialState;
  const existingDryRun = state.steps.find((step) => step.name === "sales_snapshot_dry_run")?.result;
  if (expectedSnapshotSha256 && existingDryRun
    && canonicalSnapshotSha256FromDryRun(existingDryRun) !== expectedSnapshotSha256) {
    throw new Error("已记录 Django sales dry-run snapshot 与正式批准材料不一致");
  }
  if (expectedSnapshotSha256
    && state.steps.some((step) => step.name === "sales_snapshot_applied")
    && !existingDryRun) {
    throw new Error("已记录 Django sales apply 缺少正式批准的 dry-run snapshot");
  }
  if (!state.steps.some((step) => step.name === "sales_snapshot_applied")) {
    const dryRun = existingDryRun
      ?? await django(["migrate_sales_from_d1", "--source", source, "--dry-run"]);
    const dryRunId = String(dryRun.runId ?? "");
    if (!/^[0-9a-f]{32,64}$/i.test(dryRunId) || dryRun.status !== "dry_run_completed") {
      throw new Error("Django sales dry-run 未返回可审批 runId");
    }
    if (expectedSnapshotSha256
      && canonicalSnapshotSha256FromDryRun(dryRun) !== expectedSnapshotSha256) {
      throw new Error("Django sales dry-run snapshot 已偏离正式批准的演练材料");
    }
    state = await recordStep(statePath, state, "sales_snapshot_dry_run", dryRun, now);
    let applied: JsonRecord;
    try {
      applied = await django([
        "migrate_sales_from_d1", "--source", source, "--apply", "--approved-run-id", dryRunId,
        "--allow-legacy-digest-upgrade",
      ]);
    } catch (applyError) {
      try {
        const recovered = await django([
          "migrate_sales_from_d1", "--source", source,
          "--recover-approved-apply", "--approved-run-id", dryRunId,
        ]);
        if (recovered.status !== "recovered_completed_apply"
          || recovered.approvedRunId !== dryRunId
          || !/^[0-9a-f]{32,64}$/i.test(String(recovered.runId ?? ""))) {
          throw new Error("已消费审批的 apply 恢复证据无效");
        }
        applied = {
          ...recovered,
          status: "completed",
          recoveryStatus: recovered.status,
          recoveredFromApproval: true,
        };
      } catch {
        throw applyError;
      }
    }
    if (applied.status !== "completed") throw new Error("Django sales apply 未完成");
    state = await recordStep(statePath, state, "sales_snapshot_applied", applied, now);
  }
  const verified = await django(["migrate_sales_from_d1", "--source", source, "--verify-only"]);
  if (verified.status !== "verified") throw new Error("Django sales verify 未完成");
  return recordStep(statePath, state, "sales_snapshot_verified_before_prepare", verified, now);
}

export async function executeSalesLocalCutover(
  options: ExecuteOptions,
  dependencies: ExecuteDependencies = {},
): Promise<SalesLocalCutoverState> {
  const id = cutoverId(options.cutoverId);
  if (!path.isAbsolute(options.source) || path.extname(options.source).toLowerCase() !== ".sqlite") {
    throw new Error("--source 必须是精确的绝对 .sqlite 路径");
  }
  const source = await realpath(options.source);
  if (!(await stat(source)).isFile()) throw new Error("--source 不是文件");
  if (!path.isAbsolute(options.auditDirectory)) throw new Error("--audit-dir 必须是绝对路径");
  await mkdir(options.auditDirectory, { recursive: true });
  const auditDirectory = await realpath(options.auditDirectory);
  if (!(await stat(auditDirectory)).isDirectory()) throw new Error("--audit-dir 不是目录");
  const backendDirectory = await realpath(options.backendDirectory);
  const python = await realpath(options.python);
  if (!(await stat(path.join(backendDirectory, "manage.py"))).isFile() || !(await stat(python)).isFile()) {
    throw new Error("Django backend 或 Python 路径无效");
  }
  const repositoryRoot = await realpath(
    options.repositoryRoot ?? path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."),
  );
  const sourcePathDigest = sha256(source);
  const statePath = path.join(auditDirectory, `sales-cutover-${sha256(id).slice(0, 24)}.state.json`);
  const now = dependencies.now ?? (() => new Date());
  let loaded: unknown = null;
  let stateLoadError: unknown = null;
  try {
    loaded = JSON.parse(await readFile(statePath, "utf8"));
  } catch (error) {
    if ((error as NodeJS.ErrnoException)?.code !== "ENOENT") stateLoadError = error;
  }
  let state: SalesLocalCutoverState;
  try {
    state = loaded
      ? validateState(loaded, { cutoverId: id, sourcePathDigest })
      : {
      version: STATE_VERSION,
      cutoverId: id,
      sourcePathDigest,
      createdAt: now().toISOString(),
      updatedAt: now().toISOString(),
      status: "running" as const,
      steps: [],
    };
  } catch (error) {
    stateLoadError = error;
    state = {
      version: STATE_VERSION,
      cutoverId: id,
      sourcePathDigest,
      createdAt: now().toISOString(),
      updatedAt: now().toISOString(),
      status: "running",
      steps: [],
    };
  }

  const django = dependencies.django ?? createDefaultOwnerDjangoJsonRunner({ python, backendDirectory });
  const erpDjango = dependencies.erpDjango
    ?? (dependencies.django ? django : createDefaultErpDjangoJsonRunner({ python, backendDirectory }));
  // External authorities are the recovery source of truth.  Activation may
  // commit immediately before the local state rename, and the state file may
  // subsequently be lost or damaged.  Never rerun migration/cleanup or rebuild
  // an attestation once both authorities are terminal: only an already-saved,
  // lightweight-valid attestation may repair the local audit state.
  let earlyD1: SalesD1AuthoritySnapshot | null = null;
  const earlyDatabase = new DatabaseSync(source, { readOnly: true });
  try {
    if (tableExists(earlyDatabase, "sales_write_authority")) {
      earlyD1 = inspectSalesD1WriteAuthority(earlyDatabase);
    }
  } finally {
    earlyDatabase.close();
  }
  if (earlyD1?.owner === "postgresql") {
    const pg = pgStatus(await django(["sales_write_authority", "status"]));
    if (earlyD1.cutoverId !== id || pg.cutoverId !== id
      || !["pending", "active"].includes(pg.status)) {
      throw new Error("D1/PostgreSQL terminal authority 身份不一致");
    }
    if (pg.status === "active") {
      const existing = await django([
        "sales_cutover_attestation_status",
        "--cutover-id", id,
        "--audit-dir", auditDirectory,
      ]);
      const recordedSha = String(
        state.steps.find((step) => step.name === "d1_terminal_attested")?.result.payloadSha256 ?? "",
      );
      if (existing.status !== "valid"
        || existing.cutoverId !== id
        || !/^[0-9a-f]{64}$/.test(String(existing.payloadSha256 ?? ""))
        || (recordedSha !== "" && existing.payloadSha256 !== recordedSha)) {
        throw new Error("已完成 cutover 的不可变 attestation 轻量复验失败");
      }
      state = await recordStep(statePath, state, "d1_terminal_attested", {
        ...existing,
        recoveredFromExternalAuthorities: state.status !== "completed" || Boolean(stateLoadError),
      }, now);
      state = await recordStep(statePath, state, "postgres_authority_activated", {
        ...pg,
        recoveredFromExternalAuthorities: state.status !== "completed" || Boolean(stateLoadError),
      }, now);
      if (state.status !== "completed" || stateLoadError) {
        state = { ...state, status: "completed", updatedAt: now().toISOString() };
        await writeAtomicJson(statePath, state);
      }
      return state;
    }
  }
  if (stateLoadError) throw stateLoadError;
  if (!loaded) await writeAtomicJson(statePath, state);
  if (state.status === "completed") {
    throw new Error("已完成 cutover 的 D1/PostgreSQL authority 终态不一致");
  }

  await (dependencies.assertMaintenance ?? assertSalesCutoverMaintenance)();
  state = await recordStep(statePath, state, "maintenance_ports_closed", {
    ports: [...SALES_CUTOVER_MAINTENANCE_PORTS],
  }, now);
  const cleanupManifestPath = path.join(
    auditDirectory,
    `sales-cutover-${sha256(id).slice(0, 24)}.legacy-r2-cleanup.json`,
  );
  const cleanupPlan = await planLegacySalesR2Cleanup({
    source,
    cutoverId: id,
    bucket: LEGACY_SALES_R2_BUCKET,
    persistTo: options.r2PersistTo,
    manifestPath: cleanupManifestPath,
  });
  if (cleanupPlan.manifestId !== options.approvedR2CleanupManifestId
    || !/^[0-9a-f]{64}$/.test(options.approvedR2CleanupManifestId)) {
    throw new Error(
      `legacy R2 清理 dry-run 尚未获批准；请审核 ${cleanupManifestPath} 后传入其 manifestId`,
    );
  }
  state = await recordStep(statePath, state, "legacy_r2_cleanup_plan_approved", {
    manifestId: cleanupPlan.manifestId,
    sessions: cleanupPlan.sessions.length,
    objects: cleanupPlan.objects.length,
  }, now);

  // All read-only/operator-approval preflight above precedes either D1 or PG
  // schema mutation, so a rejected cleanup plan leaves the old stack intact.
  let database: DatabaseSync;
  let authorityTable = false;
  let erpBridgeTable = false;
  if (options.expectedSourceCanonicalSnapshotSha256) {
    const expectedSnapshotSha256 = options.expectedSourceCanonicalSnapshotSha256;
    if (!/^[0-9a-f]{64}$/.test(expectedSnapshotSha256)) {
      throw new Error("正式 cutover 缺少有效的 expected canonical snapshot SHA-256");
    }
    const sqlFiles = await readD1CutoverPreSchema(repositoryRoot);
    database = new DatabaseSync(source);
    database.exec("BEGIN IMMEDIATE");
    let schemaResult: JsonRecord;
    try {
      const verified = await (
        dependencies.verifyExpectedSourceSnapshot
        ?? ((lockedSource, expected) => verifyExpectedSourceSnapshot({
          source: lockedSource,
          expectedSnapshotSha256: expected,
          python,
          backendDirectory,
          repositoryRoot,
        }))
      )(source, expectedSnapshotSha256);
      if (verified.status !== "verified" || verified.snapshotSha256 !== expectedSnapshotSha256) {
        throw new Error("写锁内实时 D1 canonical snapshot 未匹配正式批准材料");
      }
      authorityTable = tableExists(database, "sales_write_authority");
      erpBridgeTable = tableExists(database, "erp_reference_projection_source_state");
      if (!authorityTable || !erpBridgeTable) {
        if (authorityTable && inspectSalesD1WriteAuthority(database).owner !== "d1") {
          throw new Error("D1 已离开 d1 owner 但 pre-schema 不完整，拒绝补写");
        }
        applyD1CutoverPreSchemaInOpenTransaction(database, sqlFiles);
        schemaResult = {
          files: sqlFiles.map((item) => item.name),
          sourceCanonicalSnapshotSha256: expectedSnapshotSha256,
        };
      } else {
        schemaResult = {
          files: [...D1_CUTOVER_PRE_SCHEMA_FILES],
          alreadyPresent: true,
          ...validateD1CutoverPreSchema(database),
          sourceCanonicalSnapshotSha256: expectedSnapshotSha256,
        };
      }
      database.exec("COMMIT");
    } catch (error) {
      database.exec("ROLLBACK");
      throw error;
    } finally {
      database.close();
    }
    state = await recordStep(statePath, state, "d1_0090_0091_pre_schema", schemaResult, now);
  } else {
    database = new DatabaseSync(source, { readOnly: true });
    try {
      authorityTable = tableExists(database, "sales_write_authority");
      erpBridgeTable = tableExists(database, "erp_reference_projection_source_state");
    } finally {
      database.close();
    }
    if (!authorityTable || !erpBridgeTable) {
      if (authorityTable) {
        const current = inspectD1(source);
        if (current.owner !== "d1") throw new Error("D1 已离开 d1 owner 但 pre-schema 不完整，拒绝补写");
      }
      const applied = await applyD1CutoverPreSchema({ source, repositoryRoot });
      state = await recordStep(statePath, state, "d1_0090_0091_pre_schema", applied, now);
    } else {
      database = new DatabaseSync(source, { readOnly: true });
      let schemaEvidence: JsonRecord;
      try {
        schemaEvidence = validateD1CutoverPreSchema(database);
      } finally {
        database.close();
      }
      state = await recordStep(statePath, state, "d1_0090_0091_pre_schema", {
        files: [...D1_CUTOVER_PRE_SCHEMA_FILES],
        alreadyPresent: true,
        ...schemaEvidence,
      }, now);
    }
  }
  if (options.expectedSourceCanonicalSnapshotSha256) {
    const recordedSnapshotSha256 = String(
      state.steps.find((step) => step.name === "d1_0090_0091_pre_schema")
        ?.result.sourceCanonicalSnapshotSha256 ?? "",
    );
    if (recordedSnapshotSha256 !== options.expectedSourceCanonicalSnapshotSha256) {
      throw new Error("D1 pre-schema state 未绑定正式批准的 canonical snapshot");
    }
  }

  const migrated = await django(["migrate", "--noinput"]);
  state = await recordStep(statePath, state, "postgres_schema_migrated", migrated, now);
  const erpCheckpoint = await ensureErpReferenceCaughtUp(erpDjango, source);
  state = await recordStep(
    statePath,
    state,
    "erp_reference_checkpoint_caught_up",
    erpCheckpoint,
    now,
  );

  let d1 = inspectD1(source);
  let pg = pgStatus(await django(["sales_write_authority", "status"]));
  if (pg.status === "disabled") throw new Error("PostgreSQL sales authority 已 disabled，拒绝自动恢复");
  if (pg.status === "active" && (pg.cutoverId !== id || d1.owner !== "postgresql" || d1.cutoverId !== id)) {
    throw new Error("检测到 PostgreSQL active 与 D1 terminal 不一致，拒绝继续");
  }

  if (d1.owner === "d1") {
    if (pg.status !== "pending" || (pg.cutoverId && pg.cutoverId !== id)) {
      throw new Error("D1 仍为 owner，但 PostgreSQL authority 不是本次可准备状态");
    }
    state = await migrateAndVerifyFreshSnapshot(
      django,
      source,
      statePath,
      state,
      now,
      options.expectedSourceCanonicalSnapshotSha256,
    );
    const applyRunId = String(
      state.steps.find((step) => step.name === "sales_snapshot_applied")?.result.runId ?? "",
    );
    if (!/^[0-9a-f]{32,64}$/i.test(applyRunId)) {
      throw new Error("缺少本次 cutover 明确绑定的 v4 applyRunId，拒绝猜测历史 apply");
    }
    pg = pgStatus(await django(["sales_write_authority", "status"]));
    if (!pg.cutoverId) {
      const prepared = await django([
        "sales_write_authority", "prepare",
        "--expected-epoch", pg.authorityEpoch,
        "--cutover-id", id,
      ]);
      pg = pgStatus(prepared);
      state = await recordStep(statePath, state, "postgres_authority_prepared", prepared, now);
    } else if (pg.cutoverId === id) {
      state = await recordStep(statePath, state, "postgres_authority_prepared", pg as unknown as JsonRecord, now);
    } else {
      throw new Error("PostgreSQL authority 已由另一 cutoverId 准备");
    }
    let pendingSnapshot: SalesD1AuthoritySnapshot | null = null;
    const cleanup = await executeLegacySalesR2Cleanup({
      source,
      cutoverId: id,
      bucket: LEGACY_SALES_R2_BUCKET,
      persistTo: options.r2PersistTo,
      manifestPath: cleanupManifestPath,
      approvedManifestId: cleanupPlan.manifestId,
      client: dependencies.r2Client,
      beforeLockedCleanup: async () => {
        const verified = await django([
          "migrate_sales_from_d1", "--source", source, "--verify-only",
        ]);
        if (verified.status !== "verified" || !/^[0-9a-f]{32,64}$/i.test(String(verified.runId ?? ""))) {
          throw new Error("D1 写锁内的 v4 全量 verify 未完成");
        }
        const verifyRunId = String(verified.runId);
        await refreshErpReferenceCheckpoint(erpDjango, source, "D1 写锁内");
        const migrationEvidence = await django([
          "sales_cutover_migration_evidence",
          "--source", source,
          "--migration-apply-run-id", applyRunId,
          "--migration-verify-run-id", verifyRunId,
        ]);
        if (migrationEvidence.status !== "verified"
          || migrationEvidence.migrationApplyRunId !== applyRunId
          || migrationEvidence.migrationVerifyRunId !== verifyRunId) {
          throw new Error("R2 删除前 v4 apply/verify 强绑定核验失败");
        }
        return { applyRunId, runId: verifyRunId };
      },
      finalizeD1: (lockedDatabase) => {
        const locked = inspectSalesD1WriteAuthority(lockedDatabase);
        pendingSnapshot = transitionSalesD1WriteAuthorityInOpenTransaction(lockedDatabase, {
          expectedOwner: "d1",
          expectedEpoch: locked.epoch,
          targetOwner: "pending",
          cutoverId: id,
        });
      },
    });
    d1 = inspectD1(source);
    if (!pendingSnapshot || d1.owner !== "pending" || d1.cutoverId !== id) {
      throw new Error("legacy R2 清理与 D1 pending 原子提交回查失败");
    }
    state = await recordStep(statePath, state, "d1_locked_verify_cleanup_pending", {
      manifestId: cleanup.manifestId,
      lockedVerifyRunId: cleanup.lockedVerifyRunId ?? "",
      sessions: cleanup.sessions.length,
      objects: cleanup.objects.length,
      d1Epoch: d1.epoch,
    }, now);
  }

  if (d1.owner === "pending") {
    if (d1.cutoverId !== id || pg.status !== "pending" || pg.cutoverId !== id) {
      throw new Error("D1 pending 与 PostgreSQL prepared cutover 身份不一致");
    }
    const cleanup = await executeLegacySalesR2Cleanup({
      source,
      cutoverId: id,
      bucket: LEGACY_SALES_R2_BUCKET,
      persistTo: options.r2PersistTo,
      manifestPath: cleanupManifestPath,
      approvedManifestId: cleanupPlan.manifestId,
      client: dependencies.r2Client,
      beforeLockedCleanup: async () => {
        throw new Error("pending 恢复不应重新执行 locked verify 或 R2 删除");
      },
      finalizeD1: () => {
        throw new Error("pending 恢复不应重新执行 D1 finalize");
      },
    });
    if (cleanup.status !== "completed" || !cleanup.lockedApplyRunId || !cleanup.lockedVerifyRunId) {
      throw new Error("D1 pending 缺少原子 full-verify/legacy-cleanup 完成证明");
    }
    state = await recordStep(statePath, state, "d1_locked_verify_cleanup_pending", {
      manifestId: cleanup.manifestId,
      lockedVerifyRunId: cleanup.lockedVerifyRunId,
      sessions: cleanup.sessions.length,
      objects: cleanup.objects.length,
      d1Epoch: d1.epoch,
      recovered: true,
    }, now);
    // R2 deletion may take longer than the strict ERP heartbeat window.  The
    // locked verify above proves the exact D1/PG snapshot before any deletion;
    // refresh the independently fenced ERP checkpoint again after cleanup so
    // the final evidence observes the same source head with a fresh heartbeat.
    const finalErpCheckpoint = await refreshErpReferenceCheckpoint(
      erpDjango,
      source,
      "legacy R2 清理后",
    );
    const evidence = await django([
      "sales_cutover_evidence",
      "--source", source,
      "--cutover-id", id,
      "--migration-apply-run-id", cleanup.lockedApplyRunId,
      "--migration-verify-run-id", cleanup.lockedVerifyRunId,
      "--cleanup-manifest", cleanupManifestPath,
    ]);
    if (evidence.status !== "verified"
      || evidence.migrationVerifyRunId !== cleanup.lockedVerifyRunId
      || evidence.cleanupManifestId !== cleanup.manifestId) {
      throw new Error("PostgreSQL v4/legacy cleanup 最终证据回查失败");
    }
    state = await recordStep(
      statePath,
      state,
      "postgres_cutover_evidence_verified",
      { ...evidence, erpCheckpoint: finalErpCheckpoint },
      now,
    );
    d1 = transitionD1(source, {
      expectedOwner: "pending",
      expectedEpoch: d1.epoch,
      targetOwner: "postgresql",
      cutoverId: id,
    });
    state = await recordStep(statePath, state, "d1_authority_postgresql_terminal", d1 as unknown as JsonRecord, now);
  }

  if (d1.owner !== "postgresql" || d1.cutoverId !== id) {
    throw new Error("D1 未进入本次 PostgreSQL terminal owner");
  }
  pg = pgStatus(await django(["sales_write_authority", "status"]));
  if (pg.cutoverId !== id || !["pending", "active"].includes(pg.status)) {
    throw new Error("PostgreSQL authority 与 D1 terminal cutoverId 不一致");
  }
  const completedCleanup = await readLegacySalesR2CleanupManifest(cleanupManifestPath);
  if (completedCleanup.status !== "completed"
    || !completedCleanup.lockedApplyRunId
    || !completedCleanup.lockedVerifyRunId) {
    throw new Error("D1 terminal 前缺少已完成的 legacy cleanup/locked verify 证明");
  }
  // The live-baseline evidence check may itself be expensive.  Refresh once
  // more immediately before attestation, including D1-terminal/PG-pending
  // forward recovery, without granting the owner runner ERP write privileges.
  const attestationErpCheckpoint = await refreshErpReferenceCheckpoint(
    erpDjango,
    source,
    "D1 terminal attestation 前",
  );
  const attestation = await django([
    "sales_cutover_attestation",
    "--source", source,
    "--cutover-id", id,
    "--audit-dir", auditDirectory,
    "--migration-apply-run-id", completedCleanup.lockedApplyRunId,
    "--migration-verify-run-id", completedCleanup.lockedVerifyRunId,
    "--cleanup-manifest", cleanupManifestPath,
  ]);
  const attestationSha256 = String(attestation.payloadSha256 ?? "");
  if (attestation.status !== "attested" || !/^[0-9a-f]{64}$/.test(attestationSha256)) {
    throw new Error("D1 terminal attestation 未生成有效 SHA-256");
  }
  state = await recordStep(statePath, state, "d1_terminal_attested", {
    ...attestation,
    erpCheckpoint: attestationErpCheckpoint,
  }, now);
  if (pg.status === "pending") {
    const active = await django([
      "sales_write_authority", "activate",
      "--expected-epoch", pg.authorityEpoch,
      "--cutover-id", id,
      "--attestation-sha256", attestationSha256,
    ]);
    pg = pgStatus(active);
    state = await recordStep(statePath, state, "postgres_authority_activated", active, now);
  }
  if (pg.status !== "active" || pg.cutoverId !== id) throw new Error("PostgreSQL authority 激活回查失败");
  const completedAt = now().toISOString();
  state = { ...state, status: "completed", updatedAt: completedAt };
  await writeAtomicJson(statePath, state);
  return state;
}

export function parseSalesLocalCutoverArguments(argv: readonly string[]): ManagedExecuteOptions {
  const values = new Map<string, string>();
  const flags = new Set([
    "--managed-execute",
    "--managed-rehearsal-execute",
    "--confirmed-maintenance",
  ]);
  const options = new Set([
    "--source", "--cutover-id", "--audit-dir", "--backend-dir", "--python",
    "--r2-persist-to", "--approved-r2-cleanup-manifest-id", "--repository-root",
    "--runtime-root", "--expected-source-canonical-snapshot-sha256",
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key?.startsWith("--")) throw new Error(`未知参数：${key ?? ""}`);
    if (!flags.has(key) && !options.has(key)) throw new Error(`未知参数：${key}`);
    if (values.has(key)) throw new Error(`参数重复：${key}`);
    if (flags.has(key)) {
      values.set(key, "true");
      continue;
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${key} 缺少参数值`);
    values.set(key, value);
    index += 1;
  }
  const managedModes = ["--managed-execute", "--managed-rehearsal-execute"]
    .filter((flag) => values.has(flag));
  if (managedModes.length !== 1 || !values.has("--confirmed-maintenance")) {
    throw new Error(
      "必须显式提供唯一 managed execute 模式与 --confirmed-maintenance；省略时不执行切换",
    );
  }
  return {
    source: values.get("--source") ?? "",
    cutoverId: values.get("--cutover-id") ?? "",
    auditDirectory: values.get("--audit-dir") ?? "",
    backendDirectory: values.get("--backend-dir") ?? "",
    python: values.get("--python") ?? "",
    r2PersistTo: values.get("--r2-persist-to") ?? "",
    approvedR2CleanupManifestId: values.get("--approved-r2-cleanup-manifest-id") ?? "",
    expectedSourceCanonicalSnapshotSha256:
      values.get("--expected-source-canonical-snapshot-sha256") ?? "",
    repositoryRoot: values.get("--repository-root"),
    runtimeRoot: values.get("--runtime-root") ?? "",
    managedMode: values.has("--managed-execute") ? "production" : "rehearsal",
  };
}

function sameLocalPath(left: string, right: string): boolean {
  return path.resolve(left).toLowerCase() === path.resolve(right).toLowerCase();
}

async function assertManagedCliContext(options: ManagedExecuteOptions): Promise<void> {
  const runtimeRoot = await realpath(options.runtimeRoot);
  const fixedRuntimeRoot = await realpath("D:\\teruisi-runtime\\django-sales");
  if (!sameLocalPath(runtimeRoot, fixedRuntimeRoot)) {
    throw new Error("managed cutover 只允许固定受保护 runtime");
  }
  const installedApp = await realpath(path.join(runtimeRoot, "app"));
  const expectedScript = await realpath(path.join(installedApp, "tools", "sales-local-cutover.ts"));
  if (!process.argv[1] || !sameLocalPath(await realpath(process.argv[1]), expectedScript)) {
    throw new Error("managed cutover 只能从受保护 runtime app 执行");
  }
  const expectedBackend = await realpath(path.join(installedApp, "backend"));
  const expectedPython = await realpath(path.join(runtimeRoot, "venv", "Scripts", "python.exe"));
  const repositoryRoot = await realpath(options.repositoryRoot ?? "");
  const backendDirectory = await realpath(options.backendDirectory);
  const python = await realpath(options.python);
  if (!sameLocalPath(repositoryRoot, installedApp)
    || !sameLocalPath(backendDirectory, expectedBackend)
    || !sameLocalPath(python, expectedPython)) {
    throw new Error("managed cutover 的源码、backend 或 Python 未绑定受保护 runtime");
  }

  const source = await realpath(options.source);
  const auditDirectory = await realpath(options.auditDirectory);
  const r2PersistTo = await realpath(options.r2PersistTo);
  const ownerUrl = String(process.env.TERUISI_DJANGO_DATABASE_URL ?? "").trim();
  const erpUrl = String(process.env.TERUISI_DJANGO_ERP_DATABASE_URL ?? "").trim();
  const ownerDatabase = assertLocalCutoverDatabaseUrl(ownerUrl, "teruisi_sales_owner");
  const erpDatabase = assertLocalCutoverDatabaseUrl(erpUrl, "teruisi_erp_reference_sync");
  if (process.env.TERUISI_DJANGO_PROCESS_ROLE !== "migration_writer"
    || process.env.TERUISI_DJANGO_EXPECT_READ_ONLY !== "false"
    || process.env.TERUISI_DJANGO_ENVIRONMENT !== "production"
    || ownerDatabase.databaseName !== erpDatabase.databaseName) {
    throw new Error("managed cutover 数据库角色、只读标记或 ERP 数据库不一致");
  }
  const expectedWrangler = await realpath(
    path.join(
      installedApp, "runtime-tools", "node_modules", "wrangler", "wrangler-dist", "cli.js",
    ),
  );
  const configuredWrangler = await realpath(
    String(process.env.TERUISI_WRANGLER_CLI_JS ?? ""),
  );
  if (!sameLocalPath(configuredWrangler, expectedWrangler)) {
    throw new Error("managed cutover 未绑定部署内固定 Wrangler CLI");
  }

  if (options.managedMode === "production") {
    if (process.env.TERUISI_DJANGO_CUTOVER_MANAGED !== "1"
      || process.env.TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED === "1") {
      throw new Error("production cutover 缺少唯一 managed marker");
    }
    if (!/^[0-9a-f]{64}$/.test(options.expectedSourceCanonicalSnapshotSha256 ?? "")) {
      throw new Error("production cutover 缺少 operator 批准的 canonical snapshot SHA-256");
    }
    const serviceConfig = jsonRecord(
      JSON.parse(await readFile(path.join(runtimeRoot, "service.json"), "utf8")),
      "Django service config",
    );
    if (serviceConfig.version !== 3
      || typeof serviceConfig.erpSourceD1 !== "string"
      || !sameLocalPath(await realpath(serviceConfig.erpSourceD1), source)
      || !sameLocalPath(auditDirectory, path.join(runtimeRoot, "audits", "sales-cutover"))
      || !sameLocalPath(r2PersistTo, path.dirname(path.dirname(path.dirname(path.dirname(source)))))
      || ownerDatabase.databaseName !== "teruisi_sales"
      || ownerDatabase.applicationName !== `teruisi_cutover_${sha256(options.cutoverId).slice(0, 12)}`
      || erpDatabase.applicationName !== `teruisi_cutover_erp_${sha256(options.cutoverId).slice(0, 12)}`) {
      throw new Error("production cutover 的 D1/R2/audit/数据库身份与 service config 不一致");
    }
    return;
  }

  if (process.env.TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED !== "1"
    || process.env.TERUISI_DJANGO_CUTOVER_MANAGED === "1") {
    throw new Error("rehearsal cutover 缺少唯一 managed marker");
  }
  if (options.expectedSourceCanonicalSnapshotSha256) {
    throw new Error("rehearsal cutover 不接受 production canonical snapshot 批准参数");
  }
  const matched = /^rehearsal-([0-9a-f]{12})$/.exec(options.cutoverId);
  if (!matched) throw new Error("rehearsal cutoverId 未绑定 12 位演练身份");
  const rehearsalId = matched[1];
  const rehearsalRoot = await realpath(path.join(runtimeRoot, "rehearsals", rehearsalId));
  const expectedSource = path.join(
    rehearsalRoot,
    ".wrangler", "state", "v3", "d1", "rehearsal-D1DatabaseObject", "source-d1.sqlite",
  );
  if (!sameLocalPath(source, expectedSource)
    || !sameLocalPath(auditDirectory, path.join(rehearsalRoot, "audit", "cutover"))
    || !sameLocalPath(r2PersistTo, path.join(rehearsalRoot, ".wrangler", "state"))
    || ownerDatabase.databaseName !== `teruisi_sales_rehearsal_${rehearsalId}`
    || erpDatabase.databaseName !== ownerDatabase.databaseName
    || ownerDatabase.applicationName !== `teruisi_rehearsal_cutover_${rehearsalId}`
    || erpDatabase.applicationName !== `teruisi_rehearsal_erp_${rehearsalId}`) {
    throw new Error("rehearsal cutover 的 DB/D1/R2/audit 身份不在隔离根内");
  }
}

async function main() {
  const options = parseSalesLocalCutoverArguments(process.argv.slice(2));
  await assertManagedCliContext(options);
  const state = await executeSalesLocalCutover(options);
  process.stdout.write(`${JSON.stringify({
    status: state.status,
    cutoverId: state.cutoverId,
    steps: state.steps.map((step) => step.name),
  })}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error: unknown) => {
    process.stderr.write(`${error instanceof Error ? error.message : "本机销售 cutover 失败"}\n`);
    process.exitCode = 1;
  });
}
