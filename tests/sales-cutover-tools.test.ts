import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import {
  executeLegacySalesR2Cleanup,
  createWranglerLocalR2Client,
  LEGACY_SALES_R2_BUCKET,
  planLegacySalesR2Cleanup,
  type LocalR2Client,
} from "../tools/sales-legacy-r2-cleanup";
import {
  applyD1CutoverPreSchema,
  assertLocalCutoverDatabaseUrl,
  D1_CUTOVER_PRE_SCHEMA_FILES,
  ensureErpReferenceCaughtUp,
  executeSalesLocalCutover,
  parseSalesLocalCutoverArguments,
  portHasAnyListener,
  SALES_CUTOVER_MAINTENANCE_PORTS,
  type DjangoJsonRunner,
} from "../tools/sales-local-cutover";

const CUTOVER_ID = "cutover-tools-test-001";
const UUID_ONE = "11111111-1111-4111-8111-111111111111";
const UUID_TWO = "22222222-2222-4222-8222-222222222222";

const ERP_CAUGHT_UP = {
  status: "caught_up",
  sourceEpoch: "1".repeat(32),
  headSequence: 0,
  erpRevision: 5,
  rowCount: 0,
  contentHash: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
};
const TEST_CANONICAL_DIGEST = "9".repeat(64);
const TEST_CANONICAL_SNAPSHOT_SHA256 = createHash("sha256").update(
  `{"canonicalFormatVersion":"sales-projection-v4","sourceCounts":{"sales_order_lines":0},`
    + `"sourceDigests":{"sales_order_lines":"${TEST_CANONICAL_DIGEST}"},"sourceRevision":"8:5"}`,
  "utf8",
).digest("hex");

test("cutover maintenance binds Worker and workflow helper to the same stopped fence", () => {
  assert.deepEqual([...SALES_CUTOVER_MAINTENANCE_PORTS], [3000, 5791, 8001, 8002]);
});

test("cutover maintenance rejects IPv4 or IPv6 listeners instead of probing loopback only", async () => {
  const availableCalls: Array<[number, string, boolean | undefined]> = [];
  assert.equal(await portHasAnyListener(5791, async (port, host, ipv6Only) => {
    availableCalls.push([port, host, ipv6Only]);
    return true;
  }), false);
  assert.deepEqual(availableCalls, [
    [5791, "0.0.0.0", undefined],
    [5791, "::", true],
  ]);

  const occupiedCalls: string[] = [];
  assert.equal(await portHasAnyListener(5791, async (_port, host) => {
    occupiedCalls.push(host);
    return false;
  }), true);
  assert.deepEqual(occupiedCalls, ["0.0.0.0"]);
});

test("destructive cutover CLI rejects unknown, duplicate, and removed apply-id arguments", () => {
  assert.throws(
    () => parseSalesLocalCutoverArguments([
      "--managed-execute", "--confirmed-maintenance", "--unknown", "x",
    ]),
    /未知参数/,
  );
  assert.throws(
    () => parseSalesLocalCutoverArguments([
      "--managed-execute", "--managed-execute", "--confirmed-maintenance",
    ]),
    /参数重复/,
  );
  assert.throws(
    () => parseSalesLocalCutoverArguments([
      "--managed-execute", "--confirmed-maintenance",
      "--existing-migration-apply-run-id", "a".repeat(32),
    ]),
    /未知参数/,
  );
  assert.throws(
    () => parseSalesLocalCutoverArguments(["--execute", "--confirmed-maintenance"]),
    /未知参数/,
  );
  assert.throws(
    () => parseSalesLocalCutoverArguments([
      "--managed-execute", "--managed-rehearsal-execute", "--confirmed-maintenance",
    ]),
    /唯一 managed execute 模式/,
  );
});

test("destructive cutover CLI is bound to managed production or rehearsal runtime", async () => {
  const source = await readFile(
    new URL("../tools/sales-local-cutover.ts", import.meta.url),
    "utf8",
  );
  assert.match(source, /--managed-execute/);
  assert.match(source, /--managed-rehearsal-execute/);
  assert.match(source, /TERUISI_DJANGO_CUTOVER_MANAGED/);
  assert.match(source, /TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED/);
  assert.match(source, /D:\\\\teruisi-runtime\\\\django-sales/);
  assert.match(source, /teruisi_sales_owner/);
  assert.match(source, /teruisi_erp_reference_sync/);
  assert.match(source, /D1_CUTOVER_PRE_SCHEMA_FILE_SHA256/);
  assert.match(source, /exclusive: true, ipv6Only/);
});

test("deployed cutover entrypoint imports with Node's native TypeScript loader", () => {
  const moduleUrl = new URL("../tools/sales-local-cutover.ts", import.meta.url).href;
  const result = spawnSync(
    process.execPath,
    ["--input-type=module", "--eval", `await import(${JSON.stringify(moduleUrl)})`],
    { encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr || result.stdout);
});

test("managed cutover database URLs reject search_path and query overrides", () => {
  const query = new URLSearchParams({
    sslmode: "disable",
    application_name: "teruisi_cutover_0123456789ab",
    connect_timeout: "5",
    options: "-c statement_timeout=900000 -c idle_in_transaction_session_timeout=905000",
  });
  const valid = `postgresql://teruisi_sales_owner:test-password@127.0.0.1:5432/teruisi_sales?${query}`;
  assert.deepEqual(assertLocalCutoverDatabaseUrl(valid, "teruisi_sales_owner"), {
    databaseName: "teruisi_sales",
    applicationName: "teruisi_cutover_0123456789ab",
  });
  assert.throws(
    () => assertLocalCutoverDatabaseUrl(
      `${valid}&options=${encodeURIComponent("-c search_path=other_schema")}`,
      "teruisi_sales_owner",
    ),
    /必须绑定本机受控角色/,
  );
  assert.throws(
    () => assertLocalCutoverDatabaseUrl(`${valid}&search_path=other_schema`, "teruisi_sales_owner"),
    /必须绑定本机受控角色/,
  );
  const malicious = new URL(valid);
  malicious.searchParams.set(
    "options",
    "-c statement_timeout=900000 -c idle_in_transaction_session_timeout=905000 -c search_path=other_schema",
  );
  assert.throws(
    () => assertLocalCutoverDatabaseUrl(malicious.toString(), "teruisi_sales_owner"),
    /必须绑定本机受控角色/,
  );
});

async function temporaryRoot() {
  return mkdtemp(path.join(os.tmpdir(), "teruisi-sales-cutover-"));
}

test("ERP cutover checkpoint initializes once and is caught up before sales handoff", async () => {
  const calls: string[][] = [];
  let statusAttempts = 0;
  let syncAttempts = 0;
  const runner: DjangoJsonRunner = async (arguments_) => {
    const args = [...arguments_];
    calls.push(args);
    if (args.includes("--status")) {
      statusAttempts += 1;
      if (statusAttempts === 1) throw new Error("checkpoint missing");
      return ERP_CAUGHT_UP;
    }
    if (args.includes("--initialize-checkpoint")) return { status: "initialized" };
    syncAttempts += 1;
    if (syncAttempts === 1) throw new Error("checkpoint missing");
    return { status: "up_to_date" };
  };
  const result = await ensureErpReferenceCaughtUp(runner, "C:\\source.sqlite");
  assert.equal(result.recoveryMode, "initialized_and_synchronized");
  assert.deepEqual(calls.map((args) => args.includes("--status")
    ? "status"
    : args.includes("--initialize-checkpoint") ? "initialize" : "sync"), [
    "status", "sync", "initialize", "sync", "status",
  ]);
});

test("ERP cutover checkpoint never rebinds a mismatched existing source", async () => {
  const calls: string[][] = [];
  const runner: DjangoJsonRunner = async (arguments_) => {
    const args = [...arguments_];
    calls.push(args);
    if (args.includes("--initialize-checkpoint")) throw new Error("existing epoch mismatch");
    throw new Error("source epoch mismatch");
  };
  await assert.rejects(
    ensureErpReferenceCaughtUp(runner, "C:\\source.sqlite"),
    /existing epoch mismatch/,
  );
  assert.deepEqual(calls.map((args) => args.includes("--status")
    ? "status"
    : args.includes("--initialize-checkpoint") ? "initialize" : "sync"), [
    "status", "sync", "initialize",
  ]);
});

function createCleanupDatabase(filePath: string) {
  const sqlite = new DatabaseSync(filePath);
  sqlite.exec(`
    CREATE TABLE sales_write_authority (
      id INTEGER PRIMARY KEY, owner TEXT NOT NULL, epoch INTEGER NOT NULL,
      cutover_id TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO sales_write_authority VALUES (1, 'd1', 1, '', CURRENT_TIMESTAMP);
    CREATE TABLE sales_import_uploads (
      id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL DEFAULT 'fingerprint',
      file_name TEXT NOT NULL DEFAULT 'sales.xlsx', file_size_bytes INTEGER NOT NULL DEFAULT 10,
      chunk_size_bytes INTEGER NOT NULL DEFAULT 10, chunk_count INTEGER NOT NULL DEFAULT 1,
      received_chunk_count INTEGER NOT NULL DEFAULT 1, received_bytes INTEGER NOT NULL DEFAULT 10,
      status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT '2019-01-01T00:00:00Z',
      updated_at TEXT NOT NULL DEFAULT '2019-01-02T00:00:00Z', expires_at TEXT NOT NULL
    );
    CREATE TABLE sales_import_upload_chunks (
      upload_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, object_key TEXT NOT NULL,
      size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT '2019-01-02T00:00:00Z'
    );
    CREATE TABLE sales_order_lines (id INTEGER PRIMARY KEY);
    CREATE TABLE sales_import_batches (id TEXT PRIMARY KEY, status TEXT NOT NULL);
    CREATE TABLE sales_overview_cache_state (
      id INTEGER PRIMARY KEY, sales_revision INTEGER, erp_product_revision INTEGER
    );
    INSERT INTO sales_overview_cache_state VALUES (1, 8, 5);
    CREATE TABLE sales_projection_source_state (id INTEGER PRIMARY KEY, source_epoch TEXT);
    INSERT INTO sales_projection_source_state VALUES (1, 'source');
    CREATE TABLE sales_overview_response_cache (cache_key TEXT PRIMARY KEY);
    CREATE TABLE sales_projection_outbox (event_sequence INTEGER PRIMARY KEY, domain TEXT);
    CREATE TABLE import_content_fingerprints (sequence INTEGER PRIMARY KEY, domain TEXT, status TEXT);
    CREATE TABLE import_content_attempts (sequence INTEGER PRIMARY KEY, domain TEXT, outcome TEXT);
    CREATE TABLE import_scope_heads (domain TEXT, scope_key TEXT, status TEXT);
  `);
  sqlite.close();
}

function insertLegacyUpload(
  sqlite: DatabaseSync,
  id: string,
  status: string,
  expiresAt: string,
) {
  sqlite.prepare(
    "INSERT INTO sales_import_uploads (id,status,expires_at) VALUES (?,?,?)",
  ).run(id, status, expiresAt);
}

function insertLegacyChunk(
  sqlite: DatabaseSync,
  uploadId: string,
  chunkIndex: number,
  objectKey: string,
  sha256: string,
) {
  sqlite.prepare(
    `INSERT INTO sales_import_upload_chunks
       (upload_id,chunk_index,object_key,size_bytes,sha256) VALUES (?,?,?,10,?)`,
  ).run(uploadId, chunkIndex, objectKey, sha256);
}

async function localR2State(root: string) {
  const state = path.join(root, ".wrangler", "state");
  await mkdir(path.join(state, "v3", "r2"), { recursive: true });
  return state;
}

function mapClient(objects: Set<string>, failFirstDelete = false): LocalR2Client {
  let failed = false;
  return {
    async deleteObject(key) {
      if (failFirstDelete && !failed) {
        failed = true;
        throw new Error("stub delete failed");
      }
      objects.delete(key);
    },
    async inspectObject(key) {
      if (!objects.has(key)) return null;
      const digest = /([0-9a-f]{64})/i.exec(key)?.[1]?.toLowerCase() ?? "0".repeat(64);
      return { sizeBytes: 10, sha256: digest };
    },
  };
}

function atomicCleanupCallbacks(cutoverId = CUTOVER_ID) {
  return {
    beforeLockedCleanup: async () => ({ applyRunId: "a".repeat(32), runId: "e".repeat(32) }),
    finalizeD1: (database: DatabaseSync) => {
      const result = database.prepare(
        "UPDATE sales_write_authority SET owner='pending',epoch=epoch+1,cutover_id=? WHERE id=1 AND owner='d1'",
      ).run(cutoverId);
      assert.equal(Number(result.changes), 1);
    },
  };
}

test("cleanup plan revalidates before the authority pre-schema exists", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  const state = await localR2State(root);
  const manifestPath = path.join(root, "audit", "cleanup.json");
  createCleanupDatabase(source);
  const sqlite = new DatabaseSync(source);
  sqlite.exec("DROP TABLE sales_write_authority");
  sqlite.close();

  const first = await planLegacySalesR2Cleanup({
    source,
    cutoverId: CUTOVER_ID,
    bucket: LEGACY_SALES_R2_BUCKET,
    persistTo: state,
    manifestPath,
    now: new Date("2026-08-28T00:00:00Z"),
  });
  const revalidated = await planLegacySalesR2Cleanup({
    source,
    cutoverId: CUTOVER_ID,
    bucket: LEGACY_SALES_R2_BUCKET,
    persistTo: state,
    manifestPath,
  });
  assert.equal(revalidated.status, "planned");
  assert.equal(revalidated.manifestId, first.manifestId);
});

test("legacy R2 cleanup deletes only exact expired manifest keys then atomically removes D1 metadata", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  const state = await localR2State(root);
  const manifest = path.join(root, "audit", "cleanup.json");
  createCleanupDatabase(source);
  const key = `sales-upload/${UUID_ONE}/000000-${"a".repeat(64)}-part`;
  const sqlite = new DatabaseSync(source);
  insertLegacyUpload(sqlite, UUID_ONE, "ready", "2020-01-01T00:00:00Z");
  insertLegacyUpload(sqlite, UUID_TWO, "completed", "2020-01-01T00:00:00Z");
  insertLegacyChunk(sqlite, UUID_ONE, 0, key, "a".repeat(64));
  sqlite.close();
  const objects = new Set([key, "unrelated/object"]);

  const planned = await planLegacySalesR2Cleanup({
    source,
    cutoverId: CUTOVER_ID,
    bucket: LEGACY_SALES_R2_BUCKET,
    persistTo: state,
    manifestPath: manifest,
    now: new Date("2026-08-28T00:00:00Z"),
  });
  const result = await executeLegacySalesR2Cleanup({
    source,
    cutoverId: CUTOVER_ID,
    bucket: LEGACY_SALES_R2_BUCKET,
    persistTo: state,
    manifestPath: manifest,
    approvedManifestId: planned.manifestId,
    client: mapClient(objects),
    ...atomicCleanupCallbacks(),
  });
  assert.equal(result.status, "completed");
  assert.deepEqual(result.objects.map((item) => item.objectKey), [key]);
  assert.equal(objects.has(key), false);
  assert.equal(objects.has("unrelated/object"), true);
  const verify = new DatabaseSync(source, { readOnly: true });
  assert.equal(verify.prepare("SELECT COUNT(*) count FROM sales_import_upload_chunks").get()?.count, 0);
  assert.equal(verify.prepare("SELECT COUNT(*) count FROM sales_import_uploads").get()?.count, 0);
  assert.equal(verify.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "pending");
  verify.close();
});

test("partial R2 failure preserves every D1 row and resumes the same manifest", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  const state = await localR2State(root);
  const manifestPath = path.join(root, "audit", "cleanup.json");
  createCleanupDatabase(source);
  const keys = [0, 1].map((index) => `sales-upload/${UUID_ONE}/${index}-${"b".repeat(64)}-part`);
  const sqlite = new DatabaseSync(source);
  insertLegacyUpload(sqlite, UUID_ONE, "ready", "2020-01-01T00:00:00Z");
  keys.forEach((key, index) => insertLegacyChunk(sqlite, UUID_ONE, index, key, "b".repeat(64)));
  sqlite.close();
  const objects = new Set(keys);

  const planned = await planLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
    now: new Date("2026-08-28T00:00:00Z"),
  });
  await assert.rejects(executeLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
    approvedManifestId: planned.manifestId, client: mapClient(objects, true),
    ...atomicCleanupCallbacks(),
  }), /R2 精确对象删除失败/);
  const afterFailure = new DatabaseSync(source, { readOnly: true });
  assert.equal(afterFailure.prepare("SELECT COUNT(*) count FROM sales_import_upload_chunks").get()?.count, 2);
  assert.equal(afterFailure.prepare("SELECT COUNT(*) count FROM sales_import_uploads").get()?.count, 1);
  assert.equal(afterFailure.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "d1");
  afterFailure.close();

  const resumed = await executeLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
    approvedManifestId: planned.manifestId, client: mapClient(objects),
    ...atomicCleanupCallbacks(),
  });
  assert.equal(resumed.status, "completed");
  assert.equal(objects.size, 0);
  assert.equal(JSON.parse(await readFile(manifestPath, "utf8")).manifestId, resumed.manifestId);
});

test("cleanup without atomic callbacks cannot inspect, delete, or mutate anything", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  const state = await localR2State(root);
  const manifestPath = path.join(root, "audit", "cleanup.json");
  createCleanupDatabase(source);
  const key = `sales-upload/${UUID_ONE}/0-${"c".repeat(64)}-part`;
  const sqlite = new DatabaseSync(source);
  insertLegacyUpload(sqlite, UUID_ONE, "ready", "2020-01-01T00:00:00Z");
  insertLegacyChunk(sqlite, UUID_ONE, 0, key, "c".repeat(64));
  sqlite.close();
  const planned = await planLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
  });
  let inspections = 0;
  let deletions = 0;
  const unsafe = {
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
    approvedManifestId: planned.manifestId,
    client: {
      async inspectObject() { inspections += 1; return { sizeBytes: 10, sha256: "c".repeat(64) }; },
      async deleteObject() { deletions += 1; },
    },
  } as unknown as Parameters<typeof executeLegacySalesR2Cleanup>[0];
  await assert.rejects(executeLegacySalesR2Cleanup(unsafe), /只能由 cutover 原子/);
  assert.equal(inspections, 0);
  assert.equal(deletions, 0);
  const verify = new DatabaseSync(source, { readOnly: true });
  assert.equal(verify.prepare("SELECT COUNT(*) count FROM sales_import_upload_chunks").get()?.count, 1);
  assert.equal(verify.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "d1");
  verify.close();
});

test("cleanup rejects extra fields and numeric strings before any R2 action", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  const state = await localR2State(root);
  const manifestPath = path.join(root, "audit", "cleanup.json");
  createCleanupDatabase(source);
  const key = `sales-upload/${UUID_ONE}/0-${"c".repeat(64)}-part`;
  const sqlite = new DatabaseSync(source);
  insertLegacyUpload(sqlite, UUID_ONE, "ready", "2020-01-01T00:00:00Z");
  insertLegacyChunk(sqlite, UUID_ONE, 0, key, "c".repeat(64));
  sqlite.close();
  const planned = await planLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
  });
  let actions = 0;
  const client: LocalR2Client = {
    async inspectObject() { actions += 1; return null; },
    async deleteObject() { actions += 1; },
  };
  for (const mutate of [
    (value: Record<string, unknown>) => { value.extra = true; },
    (value: Record<string, unknown>) => {
      (value.sessions as Array<Record<string, unknown>>)[0].fileSizeBytes = "10";
    },
  ]) {
    const value = JSON.parse(JSON.stringify(planned)) as Record<string, unknown>;
    mutate(value);
    await writeFile(manifestPath, `${JSON.stringify(value)}\n`, "utf8");
    await assert.rejects(executeLegacySalesR2Cleanup({
      source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
      approvedManifestId: planned.manifestId, client, ...atomicCleanupCallbacks(),
    }), /字段集合|fileSizeBytes/);
  }
  assert.equal(actions, 0);
});

test("default Wrangler client validates an exact key without requiring manifest-only fields", async () => {
  const root = await temporaryRoot();
  const state = await localR2State(root);
  const calls: string[][] = [];
  const client = createWranglerLocalR2Client({
    bucket: LEGACY_SALES_R2_BUCKET,
    persistTo: state,
    temporaryDirectory: root,
    processRunner: async (_command, args) => {
      calls.push(args);
      return { code: 0, output: "" };
    },
  });
  const key = `sales-upload/${UUID_ONE}/0-${"a".repeat(64)}-part`;
  await client.deleteObject(key);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].slice(0, 4), [
    "wrangler", "r2", "object", "delete",
  ]);
  assert.equal(calls[0].includes(`${LEGACY_SALES_R2_BUCKET}/${key}`), true);
});

test("production Wrangler client refuses npx fallback and uses an explicitly bound cli.js", async () => {
  const previousEnvironment = process.env.TERUISI_DJANGO_ENVIRONMENT;
  const previousCli = process.env.TERUISI_WRANGLER_CLI_JS;
  try {
    process.env.TERUISI_DJANGO_ENVIRONMENT = "production";
    delete process.env.TERUISI_WRANGLER_CLI_JS;
    assert.throws(() => createWranglerLocalR2Client({
      bucket: LEGACY_SALES_R2_BUCKET,
      persistTo: path.resolve(".wrangler", "state"),
      temporaryDirectory: path.resolve(".tmp"),
    }), /显式绑定受保护的 Wrangler CLI/);
    const calls: Array<{ command: string; args: string[] }> = [];
    const cli = path.resolve(
      "runtime-tools", "node_modules", "wrangler", "wrangler-dist", "cli.js",
    );
    const client = createWranglerLocalR2Client({
      bucket: LEGACY_SALES_R2_BUCKET,
      persistTo: path.resolve(".wrangler", "state"),
      temporaryDirectory: path.resolve(".tmp"),
      wranglerCliPath: cli,
      processRunner: async (command, args) => {
        calls.push({ command, args });
        return { code: 0, output: "" };
      },
    });
    const key = `sales-upload/${UUID_ONE}/0-${"a".repeat(64)}-part`;
    await client.deleteObject(key);
    assert.equal(calls[0].command, process.execPath);
    assert.deepEqual(calls[0].args.slice(0, 4), [cli, "r2", "object", "delete"]);
  } finally {
    if (previousEnvironment == null) delete process.env.TERUISI_DJANGO_ENVIRONMENT;
    else process.env.TERUISI_DJANGO_ENVIRONMENT = previousEnvironment;
    if (previousCli == null) delete process.env.TERUISI_WRANGLER_CLI_JS;
    else process.env.TERUISI_WRANGLER_CLI_JS = previousCli;
  }
});

test("same R2 key with changed bytes is retained and diagnostics redact the raw key", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  const state = await localR2State(root);
  const manifestPath = path.join(root, "audit", "cleanup.json");
  createCleanupDatabase(source);
  const key = `sales-upload/${UUID_ONE}/secret-customer-key-${"d".repeat(64)}`;
  const sqlite = new DatabaseSync(source);
  insertLegacyUpload(sqlite, UUID_ONE, "ready", "2020-01-01T00:00:00Z");
  insertLegacyChunk(sqlite, UUID_ONE, 0, key, "d".repeat(64));
  sqlite.close();
  const planned = await planLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
  });
  let deletions = 0;
  const error = await executeLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
    approvedManifestId: planned.manifestId,
    client: {
      async inspectObject() { return { sizeBytes: 11, sha256: "e".repeat(64) }; },
      async deleteObject() { deletions += 1; },
    },
    ...atomicCleanupCallbacks(),
  }).then(() => null, (caught: unknown) => caught as Error);
  assert.ok(error);
  assert.match(error.message, /keySha256=[0-9a-f]{64}/);
  assert.doesNotMatch(error.message, /secret-customer-key|sales-upload/);
  assert.equal(deletions, 0);
  const verify = new DatabaseSync(source, { readOnly: true });
  assert.equal(verify.prepare("SELECT COUNT(*) count FROM sales_import_upload_chunks").get()?.count, 1);
  assert.equal(verify.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "d1");
  verify.close();
});

test("atomic cleanup handles the real 99-session 92-chunk shape without changing core evidence", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  const state = await localR2State(root);
  const manifestPath = path.join(root, "audit", "cleanup.json");
  createCleanupDatabase(source);
  const objects = new Set<string>();
  const sqlite = new DatabaseSync(source);
  for (let index = 0; index < 99; index += 1) {
    const uploadId = `expired-session-${String(index).padStart(3, "0")}`;
    insertLegacyUpload(sqlite, uploadId, "ready", "2020-01-01T00:00:00Z");
    if (index < 92) {
      const digest = createHash("sha256").update(`chunk-${index}`).digest("hex");
      const key = `sales-upload/${uploadId}/0-${digest}-part`;
      insertLegacyChunk(sqlite, uploadId, 0, key, digest);
      objects.add(key);
    }
  }
  sqlite.close();
  const planned = await planLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
  });
  assert.equal(planned.sessions.length, 99);
  assert.equal(planned.objects.length, 92);
  const result = await executeLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state, manifestPath,
    approvedManifestId: planned.manifestId,
    client: mapClient(objects),
    ...atomicCleanupCallbacks(),
  });
  assert.equal(result.status, "completed");
  assert.equal(objects.size, 0);
  const verify = new DatabaseSync(source, { readOnly: true });
  assert.equal(verify.prepare("SELECT COUNT(*) count FROM sales_import_uploads").get()?.count, 0);
  assert.equal(verify.prepare("SELECT COUNT(*) count FROM sales_import_upload_chunks").get()?.count, 0);
  assert.equal(verify.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "pending");
  verify.close();
});

test("legacy cleanup fails closed for invalid time, active/orphan chunks, and non-local targets", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  const state = await localR2State(root);
  createCleanupDatabase(source);
  let sqlite = new DatabaseSync(source);
  insertLegacyUpload(sqlite, UUID_ONE, "ready", "not-a-time");
  sqlite.close();
  await assert.rejects(planLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state,
    manifestPath: path.join(root, "invalid.json"),
  }), /无效 expires_at/);

  sqlite = new DatabaseSync(source);
  sqlite.exec("DELETE FROM sales_import_uploads");
  insertLegacyUpload(sqlite, UUID_TWO, "completed", "2099-01-01T00:00:00Z");
  sqlite.close();
  const futureCompleted = await planLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state,
    manifestPath: path.join(root, "future-completed.json"),
  });
  assert.deepEqual(futureCompleted.sessions.map((item) => item.id), [UUID_TWO]);

  sqlite = new DatabaseSync(source);
  insertLegacyUpload(sqlite, UUID_ONE, "ready", "2099-01-01T00:00:00Z");
  sqlite.close();
  await assert.rejects(planLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state,
    manifestPath: path.join(root, "future-active.json"),
  }), /全部 D1 上传会话.*completed 或已过期/);

  sqlite = new DatabaseSync(source);
  sqlite.exec("DELETE FROM sales_import_uploads");
  insertLegacyChunk(sqlite, UUID_ONE, 0, `sales-upload/${UUID_ONE}/part`, "c".repeat(64));
  sqlite.close();
  await assert.rejects(planLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: LEGACY_SALES_R2_BUCKET, persistTo: state,
    manifestPath: path.join(root, "orphan.json"),
  }), /孤立分片/);
  await assert.rejects(planLegacySalesR2Cleanup({
    source, cutoverId: CUTOVER_ID, bucket: "some-other-bucket", persistTo: state,
    manifestPath: path.join(root, "bucket.json"),
  }), /只允许本机桶/);
});

function createPreSchemaDatabase(filePath: string) {
  const sqlite = new DatabaseSync(filePath);
  sqlite.exec(`
    CREATE TABLE sales_order_lines (id INTEGER PRIMARY KEY);
    CREATE TABLE sales_import_batches (id TEXT PRIMARY KEY, status TEXT NOT NULL);
    CREATE TABLE sales_import_uploads (
      id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL DEFAULT 'fingerprint',
      file_name TEXT NOT NULL DEFAULT 'sales.xlsx', file_size_bytes INTEGER NOT NULL DEFAULT 10,
      chunk_size_bytes INTEGER NOT NULL DEFAULT 10, chunk_count INTEGER NOT NULL DEFAULT 1,
      received_chunk_count INTEGER NOT NULL DEFAULT 1, received_bytes INTEGER NOT NULL DEFAULT 10,
      status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT '2019-01-01T00:00:00Z',
      updated_at TEXT NOT NULL DEFAULT '2019-01-02T00:00:00Z', expires_at TEXT NOT NULL
    );
    CREATE TABLE sales_import_upload_chunks (
      upload_id TEXT, chunk_index INTEGER, object_key TEXT,
      size_bytes INTEGER, sha256 TEXT,
      created_at TEXT NOT NULL DEFAULT '2019-01-02T00:00:00Z'
    );
    CREATE TABLE sales_overview_cache_state (
      id INTEGER PRIMARY KEY, sales_revision INTEGER NOT NULL, erp_product_revision INTEGER NOT NULL
    );
    INSERT INTO sales_overview_cache_state VALUES (1, 8, 5);
    CREATE TABLE sales_projection_source_state (id INTEGER PRIMARY KEY, source_epoch TEXT);
    INSERT INTO sales_projection_source_state VALUES (1, 'legacy');
    CREATE TABLE sales_overview_response_cache (cache_key TEXT PRIMARY KEY);
    CREATE TABLE sales_projection_outbox (event_sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL);
    CREATE TABLE import_content_fingerprints (sequence INTEGER PRIMARY KEY, domain TEXT, status TEXT);
    CREATE TABLE import_content_attempts (sequence INTEGER PRIMARY KEY, domain TEXT, outcome TEXT);
    CREATE TABLE import_scope_heads (domain TEXT, scope_key TEXT, status TEXT, PRIMARY KEY(domain,scope_key));
    CREATE TABLE erp_product_master (id INTEGER PRIMARY KEY);
    CREATE TABLE erp_reference_import_batches (
      id TEXT PRIMARY KEY, source_key TEXT, status TEXT, completed_at TEXT, created_at TEXT,
      row_count INTEGER, totals_json TEXT
    );
    INSERT INTO erp_reference_import_batches VALUES
      ('erp-1','products','completed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,0,
       '{"contentHash":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}');
  `);
  sqlite.close();
}

test("pre-schema applies exactly 0090 and 0091 and never executes 0092 retirement", async () => {
  assert.deepEqual([...D1_CUTOVER_PRE_SCHEMA_FILES], [
    "0090_sales_write_authority.sql",
    "0091_erp_reference_projection.sql",
  ]);
  const toolSource = await readFile(new URL("../tools/sales-local-cutover.ts", import.meta.url), "utf8");
  assert.doesNotMatch(toolSource, /D1_CUTOVER_PRE_SCHEMA_FILES\s*=\s*\[[^\]]*0092/s);
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  createPreSchemaDatabase(source);
  const result = await applyD1CutoverPreSchema({ source, repositoryRoot: path.resolve(".") });
  assert.deepEqual(result.files, [...D1_CUTOVER_PRE_SCHEMA_FILES]);
  const sqlite = new DatabaseSync(source, { readOnly: true });
  assert.equal(sqlite.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "d1");
  assert.equal(sqlite.prepare("SELECT erp_revision FROM erp_product_projection_state WHERE id=1").get()?.erp_revision, 5);
  assert.equal(sqlite.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sales_order_lines'").get()?.["1"], 1);
  sqlite.close();
});

test("pre-schema rejects any byte change before the first D1 mutation", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  const repositoryRoot = path.join(root, "repository");
  const drizzleRoot = path.join(repositoryRoot, "drizzle");
  await mkdir(drizzleRoot, { recursive: true });
  createPreSchemaDatabase(source);
  for (const name of D1_CUTOVER_PRE_SCHEMA_FILES) {
    const contents = await readFile(path.join(path.resolve("."), "drizzle", name), "utf8");
    await writeFile(
      path.join(drizzleRoot, name),
      name.startsWith("0090_") ? `${contents}\n-- unreviewed change\n` : contents,
      "utf8",
    );
  }
  await assert.rejects(
    applyD1CutoverPreSchema({ source, repositoryRoot }),
    /SHA-256 与受审版本不一致/,
  );
  const sqlite = new DatabaseSync(source, { readOnly: true });
  assert.equal(
    sqlite.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sales_write_authority'").get(),
    undefined,
  );
  sqlite.close();
});

test("formal expected snapshot is rechecked under the first D1 write lock before pre-schema mutation", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  createPreSchemaDatabase(source);
  const options = {
    ...await orchestratorPaths(root, source),
    expectedSourceCanonicalSnapshotSha256: "a".repeat(64),
  };
  const machine = djangoStateMachine(source);
  let lockObserved = false;
  await assert.rejects(
    executeSalesLocalCutover(options, {
      django: machine.runner,
      assertMaintenance: async () => undefined,
      r2Client: mapClient(new Set()),
      verifyExpectedSourceSnapshot: async () => {
        const intruder = new DatabaseSync(source);
        try {
          assert.throws(() => intruder.exec("BEGIN IMMEDIATE"), /locked/i);
          lockObserved = true;
        } finally {
          intruder.close();
        }
        return { status: "verified", snapshotSha256: "b".repeat(64) };
      },
    }),
    /写锁内实时 D1 canonical snapshot 未匹配正式批准材料/,
  );
  assert.equal(lockObserved, true);
  assert.equal(machine.calls.length, 0);
  const sqlite = new DatabaseSync(source, { readOnly: true });
  assert.equal(
    sqlite.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sales_write_authority'").get(),
    undefined,
  );
  sqlite.close();
});

async function createOrchestratorDatabase(filePath: string, input: {
  owner?: "d1" | "pending" | "postgresql";
  epoch?: number;
  cutoverId?: string;
} = {}) {
  createPreSchemaDatabase(filePath);
  await applyD1CutoverPreSchema({ source: filePath, repositoryRoot: path.resolve(".") });
  const sqlite = new DatabaseSync(filePath);
  if (input.owner === "pending" || input.owner === "postgresql") {
    sqlite.prepare(
      "UPDATE sales_write_authority SET owner='pending',epoch=2,cutover_id=? WHERE id=1",
    ).run(input.cutoverId ?? CUTOVER_ID);
  }
  if (input.owner === "postgresql") {
    sqlite.prepare(
      "UPDATE sales_write_authority SET owner='postgresql',epoch=3,cutover_id=? WHERE id=1",
    ).run(input.cutoverId ?? CUTOVER_ID);
  }
  sqlite.close();
}

function djangoStateMachine(source: string, initial: {
  pgStatus?: "pending" | "active";
  pgCutoverId?: string;
  targetMatches?: boolean;
} = {}) {
  const calls: string[][] = [];
  const pg = {
    status: initial.pgStatus ?? "pending",
    cutoverId: initial.pgCutoverId ?? "",
    authorityEpoch: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    targetMatches: initial.targetMatches ?? false,
  };
  const runner: DjangoJsonRunner = async (arguments_) => {
    const args = [...arguments_];
    calls.push(args);
    if (args[0] === "migrate") return { status: "completed" };
    if (args[0] === "sync_erp_reference" && args.includes("--status")) {
      return {
        status: "caught_up",
        sourceEpoch: "1".repeat(32),
        headSequence: 0,
        erpRevision: 5,
        rowCount: 0,
        contentHash: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      };
    }
    if (args[0] === "sync_erp_reference") return { status: "up_to_date" };
    if (args[0] === "migrate_sales_from_d1" && args.includes("--verify-only")) {
      if (!pg.targetMatches) throw new Error("snapshot mismatch");
      return { status: "verified", runId: "e".repeat(32) };
    }
    if (args[0] === "migrate_sales_from_d1" && args.includes("--dry-run")) {
      return {
        status: "dry_run_completed",
        runId: "d".repeat(32),
        canonicalFormatVersion: "sales-projection-v4",
        sourceCounts: { sales_order_lines: 0 },
        sourceDigests: { sales_order_lines: TEST_CANONICAL_DIGEST },
        sourceRevision: "8:5",
      };
    }
    if (args[0] === "migrate_sales_from_d1" && args.includes("--apply")) {
      assert.equal(args.includes("--allow-legacy-digest-upgrade"), true);
      pg.targetMatches = true;
      return { status: "completed", runId: "a".repeat(32) };
    }
    if (args[0] === "migrate_sales_from_d1" && args.includes("--recover-approved-apply")) {
      return {
        status: "recovered_completed_apply",
        approvedRunId: "d".repeat(32),
        runId: "a".repeat(32),
      };
    }
    if (args[0] === "sales_write_authority" && args[1] === "status") return { ...pg };
    if (args[0] === "sales_write_authority" && args[1] === "prepare") {
      pg.cutoverId = String(args[args.indexOf("--cutover-id") + 1]);
      pg.authorityEpoch = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
      return { ...pg };
    }
    if (args[0] === "sales_cutover_attestation") {
      const sqlite = new DatabaseSync(source, { readOnly: true });
      const row = sqlite.prepare("SELECT owner,cutover_id FROM sales_write_authority WHERE id=1").get() as { owner: string; cutover_id: string };
      sqlite.close();
      assert.equal(row.owner, "postgresql");
      assert.equal(row.cutover_id, CUTOVER_ID);
      assert.equal(args.includes("--migration-verify-run-id"), true);
      assert.equal(args.includes("--cleanup-manifest"), true);
      return { status: "attested", payloadSha256: "f".repeat(64) };
    }
    if (args[0] === "sales_cutover_attestation_status") {
      return {
        status: "valid",
        cutoverId: CUTOVER_ID,
        payloadSha256: "f".repeat(64),
      };
    }
    if (args[0] === "sales_cutover_evidence") {
      const cleanupManifest = JSON.parse(await readFile(
        String(args[args.indexOf("--cleanup-manifest") + 1]),
        "utf8",
      ));
      return {
        status: "verified",
        migrationVerifyRunId: "e".repeat(32),
        cleanupManifestId: cleanupManifest.manifestId,
      };
    }
    if (args[0] === "sales_cutover_migration_evidence") {
      const applyRunId = String(args[args.indexOf("--migration-apply-run-id") + 1]);
      const verifyRunId = String(args[args.indexOf("--migration-verify-run-id") + 1]);
      if (applyRunId !== "a".repeat(32)) throw new Error("unknown apply run");
      return {
        status: "verified",
        migrationApplyRunId: applyRunId,
        migrationVerifyRunId: verifyRunId,
      };
    }
    if (args[0] === "sales_write_authority" && args[1] === "activate") {
      assert.equal(args[args.indexOf("--attestation-sha256") + 1], "f".repeat(64));
      pg.status = "active";
      pg.authorityEpoch = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
      return { ...pg };
    }
    throw new Error(`unexpected command: ${args.join(" ")}`);
  };
  return { runner, calls, pg };
}

async function orchestratorPaths(root: string, source: string) {
  const backend = path.join(root, "backend");
  const audit = path.join(root, "audit");
  const python = path.join(root, process.platform === "win32" ? "python.exe" : "python");
  await mkdir(backend, { recursive: true });
  await writeFile(path.join(backend, "manage.py"), "# stub\n");
  await writeFile(python, "stub\n");
  const r2PersistTo = await localR2State(root);
  const cutoverHash = createHash("sha256").update(CUTOVER_ID).digest("hex").slice(0, 24);
  const cleanupManifestPath = path.join(
    audit,
    `sales-cutover-${cutoverHash}.legacy-r2-cleanup.json`,
  );
  const cleanup = await planLegacySalesR2Cleanup({
    source,
    cutoverId: CUTOVER_ID,
    bucket: LEGACY_SALES_R2_BUCKET,
    persistTo: r2PersistTo,
    manifestPath: cleanupManifestPath,
    now: new Date("2026-08-28T00:00:00Z"),
  });
  return {
    source,
    cutoverId: CUTOVER_ID,
    auditDirectory: audit,
    backendDirectory: backend,
    python,
    repositoryRoot: path.resolve("."),
    r2PersistTo,
    approvedR2CleanupManifestId: cleanup.manifestId,
  };
}

test("cutover orchestrator enforces ordered dual-authority handoff and is idempotent", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const machine = djangoStateMachine(source);
  const options = await orchestratorPaths(root, source);
  const state = await executeSalesLocalCutover(options, {
    django: machine.runner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  });
  assert.equal(state.status, "completed");
  assert.deepEqual(state.steps.map((step) => step.name), [
    "maintenance_ports_closed",
    "legacy_r2_cleanup_plan_approved",
    "d1_0090_0091_pre_schema",
    "postgres_schema_migrated",
    "erp_reference_checkpoint_caught_up",
    "sales_snapshot_dry_run",
    "sales_snapshot_applied",
    "sales_snapshot_verified_before_prepare",
    "postgres_authority_prepared",
    "d1_locked_verify_cleanup_pending",
    "postgres_cutover_evidence_verified",
    "d1_authority_postgresql_terminal",
    "d1_terminal_attested",
    "postgres_authority_activated",
  ]);
  assert.deepEqual(state.steps[0]?.result, { ports: [3000, 5791, 8001, 8002] });
  const sqlite = new DatabaseSync(source, { readOnly: true });
  assert.deepEqual({ ...sqlite.prepare("SELECT owner,cutover_id FROM sales_write_authority WHERE id=1").get() }, {
    owner: "postgresql", cutover_id: CUTOVER_ID,
  });
  sqlite.close();
  const again = await executeSalesLocalCutover(options, {
    django: machine.runner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  });
  assert.equal(again.status, "completed");
  assert.equal(again.steps.length, state.steps.length);
});

test("formal expected snapshot stays bound through the fresh Django dry-run", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const machine = djangoStateMachine(source);
  const options = {
    ...await orchestratorPaths(root, source),
    expectedSourceCanonicalSnapshotSha256: TEST_CANONICAL_SNAPSHOT_SHA256,
  };
  const state = await executeSalesLocalCutover(options, {
    django: machine.runner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
    verifyExpectedSourceSnapshot: async () => ({
      status: "verified",
      snapshotSha256: TEST_CANONICAL_SNAPSHOT_SHA256,
    }),
  });
  assert.equal(state.status, "completed");
  const preSchema = state.steps.find((step) => step.name === "d1_0090_0091_pre_schema");
  assert.equal(preSchema?.result.sourceCanonicalSnapshotSha256, TEST_CANONICAL_SNAPSHOT_SHA256);
  const dryRun = state.steps.find((step) => step.name === "sales_snapshot_dry_run");
  assert.equal(dryRun?.result.canonicalFormatVersion, "sales-projection-v4");
});

test("cutover routes ERP checkpoint commands through the dedicated least-privilege runner", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const machine = djangoStateMachine(source);
  const options = await orchestratorPaths(root, source);
  const erpCalls: string[][] = [];
  const mainRunner: DjangoJsonRunner = async (args) => {
    assert.notEqual(args[0], "sync_erp_reference");
    return machine.runner(args);
  };
  const erpRunner: DjangoJsonRunner = async (args) => {
    erpCalls.push([...args]);
    assert.equal(args[0], "sync_erp_reference");
    if (args.includes("--status")) return ERP_CAUGHT_UP;
    return { status: "up_to_date" };
  };
  const result = await executeSalesLocalCutover(options, {
    django: mainRunner,
    erpDjango: erpRunner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  });
  assert.equal(result.status, "completed");
  assert.equal(erpCalls.length, 7);
  assert.equal(erpCalls[0].includes("--status"), true);
  assert.equal(erpCalls[1].includes("--status"), false);
  assert.equal(erpCalls[2].includes("--status"), true);
  assert.equal(erpCalls[3].includes("--status"), false);
  assert.equal(erpCalls[4].includes("--status"), true);
  assert.equal(erpCalls[5].includes("--status"), false);
  assert.equal(erpCalls[6].includes("--status"), true);
  assert.equal(
    erpCalls[2][erpCalls[2].indexOf("--max-age-seconds") + 1],
    "60",
  );
  assert.equal(
    erpCalls[4][erpCalls[4].indexOf("--max-age-seconds") + 1],
    "60",
  );
  assert.equal(
    erpCalls[6][erpCalls[6].indexOf("--max-age-seconds") + 1],
    "60",
  );
  assert.equal(machine.calls.some((args) => args[0] === "sync_erp_reference"), false);
});

test("cutover refreshes the ERP checkpoint after an R2 cleanup longer than 60 seconds", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const key = `sales-upload/${UUID_ONE}/000000-${"a".repeat(64)}-part`;
  const sqlite = new DatabaseSync(source);
  insertLegacyUpload(sqlite, UUID_ONE, "ready", "2020-01-01T00:00:00Z");
  insertLegacyChunk(sqlite, UUID_ONE, 0, key, "a".repeat(64));
  sqlite.close();
  const options = await orchestratorPaths(root, source);
  const machine = djangoStateMachine(source);
  let heartbeatAgeSeconds = 0;
  const erpCalls: string[][] = [];
  const events: string[] = [];
  const erpRunner: DjangoJsonRunner = async (args) => {
    erpCalls.push([...args]);
    if (args.includes("--status")) {
      if (heartbeatAgeSeconds > 60) throw new Error("ERP checkpoint heartbeat stale");
      events.push("erp:status");
      return ERP_CAUGHT_UP;
    }
    heartbeatAgeSeconds = 0;
    events.push("erp:sync");
    return { status: "up_to_date" };
  };
  const mainRunner: DjangoJsonRunner = async (args) => {
    if (args[0] === "sales_cutover_evidence" && heartbeatAgeSeconds > 60) {
      throw new Error("PostgreSQL ERP checkpoint 与 v4 verify/新鲜度不一致");
    }
    if (args[0] === "sales_cutover_evidence") {
      events.push("main:evidence");
      heartbeatAgeSeconds = 61;
    }
    if (args[0] === "sales_cutover_attestation") {
      assert.equal(heartbeatAgeSeconds, 0);
      events.push("main:attestation");
    }
    return machine.runner(args);
  };
  const objects = new Set([key]);
  const baseClient = mapClient(objects);
  const slowCleanupClient: LocalR2Client = {
    async deleteObject(objectKey) {
      await baseClient.deleteObject(objectKey);
      heartbeatAgeSeconds = 61;
      events.push("r2:delete:done");
    },
    inspectObject: (objectKey) => baseClient.inspectObject(objectKey),
  };

  const result = await executeSalesLocalCutover(options, {
    django: mainRunner,
    erpDjango: erpRunner,
    assertMaintenance: async () => undefined,
    r2Client: slowCleanupClient,
  });

  assert.equal(result.status, "completed");
  assert.equal(objects.size, 0);
  assert.deepEqual(erpCalls.map((args) => args.includes("--status") ? "status" : "sync"), [
    "status", "sync", "status", "sync", "status", "sync", "status",
  ]);
  assert.deepEqual(events.slice(events.indexOf("r2:delete:done")), [
    "r2:delete:done",
    "erp:sync",
    "erp:status",
    "main:evidence",
    "erp:sync",
    "erp:status",
    "main:attestation",
  ]);
  const finalEvidence = result.steps.find(
    (step) => step.name === "postgres_cutover_evidence_verified",
  )?.result;
  assert.equal((finalEvidence?.erpCheckpoint as { status?: string })?.status, "caught_up");
});

test("post-cleanup ERP refresh failure leaves D1 pending and never verifies evidence", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const options = await orchestratorPaths(root, source);
  const machine = djangoStateMachine(source);
  let syncCalls = 0;
  const erpRunner: DjangoJsonRunner = async (args) => {
    if (args.includes("--status")) return ERP_CAUGHT_UP;
    syncCalls += 1;
    if (syncCalls === 2) throw new Error("simulated post-cleanup heartbeat failure");
    return { status: "up_to_date" };
  };

  await assert.rejects(executeSalesLocalCutover(options, {
    django: machine.runner,
    erpDjango: erpRunner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  }), /simulated post-cleanup heartbeat failure/);

  const sqlite = new DatabaseSync(source, { readOnly: true });
  assert.equal(sqlite.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "pending");
  sqlite.close();
  assert.equal(machine.calls.some((args) => args[0] === "sales_cutover_evidence"), false);
});

test("pre-attestation ERP refresh is fail-closed and resumes the terminal handoff", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const options = await orchestratorPaths(root, source);
  const machine = djangoStateMachine(source);
  let failPreAttestation = true;
  let syncCalls = 0;
  const erpRunner: DjangoJsonRunner = async (args) => {
    if (args.includes("--status")) return ERP_CAUGHT_UP;
    syncCalls += 1;
    if (failPreAttestation && syncCalls === 3) {
      throw new Error("simulated pre-attestation heartbeat failure");
    }
    return { status: "up_to_date" };
  };

  await assert.rejects(executeSalesLocalCutover(options, {
    django: machine.runner,
    erpDjango: erpRunner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  }), /simulated pre-attestation heartbeat failure/);
  const sqlite = new DatabaseSync(source, { readOnly: true });
  assert.equal(sqlite.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "postgresql");
  sqlite.close();
  assert.equal(machine.pg.status, "pending");
  assert.equal(machine.calls.some((args) => args[0] === "sales_cutover_attestation"), false);

  failPreAttestation = false;
  const recovered = await executeSalesLocalCutover(options, {
    django: machine.runner,
    erpDjango: erpRunner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  });
  assert.equal(recovered.status, "completed");
  assert.equal(machine.pg.status, "active");
  assert.equal(machine.calls.some((args) => args[0] === "sales_cutover_attestation"), true);
  assert.equal(machine.calls.some((args) => args[0] === "sales_write_authority" && args[1] === "activate"), true);
});

test("cutover resumes after a crash immediately following D1 terminal transition", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const options = await orchestratorPaths(root, source);
  await executeLegacySalesR2Cleanup({
    source,
    cutoverId: CUTOVER_ID,
    bucket: LEGACY_SALES_R2_BUCKET,
    persistTo: options.r2PersistTo,
    manifestPath: path.join(
      options.auditDirectory,
      `sales-cutover-${createHash("sha256").update(CUTOVER_ID).digest("hex").slice(0, 24)}.legacy-r2-cleanup.json`,
    ),
    approvedManifestId: options.approvedR2CleanupManifestId,
    client: mapClient(new Set()),
    ...atomicCleanupCallbacks(),
  });
  const terminal = new DatabaseSync(source);
  terminal.prepare(
    "UPDATE sales_write_authority SET owner='postgresql',epoch=epoch+1 WHERE id=1 AND owner='pending' AND cutover_id=?",
  ).run(CUTOVER_ID);
  terminal.close();
  const machine = djangoStateMachine(source, { pgCutoverId: CUTOVER_ID, targetMatches: true });
  const result = await executeSalesLocalCutover(options, {
    django: machine.runner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  });
  assert.equal(result.status, "completed");
  assert.equal(machine.pg.status, "active");
  assert.equal(machine.calls.some((args) => args.includes("--apply")), false);
  assert.equal(machine.calls.some((args) => args[0] === "sales_cutover_attestation"), true);
});

test("cutover always creates a fresh v4 dry-run/apply even when the target already verifies", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const machine = djangoStateMachine(source, { targetMatches: true });
  const options = await orchestratorPaths(root, source);
  const result = await executeSalesLocalCutover(options, {
    django: machine.runner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  });
  assert.equal(result.status, "completed");
  assert.equal(machine.calls.some((args) => args.includes("--apply")), true);
  assert.equal(machine.calls.some((args) => args.includes("--dry-run")), true);
  assert.equal(
    machine.calls.some((args) => args[0] === "sales_cutover_evidence"
      && args.includes("--migration-apply-run-id")
      && args.includes("a".repeat(32))),
    true,
  );
});

test("cutover recovers the same committed apply after output loss without a second dry-run", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const machine = djangoStateMachine(source);
  const options = await orchestratorPaths(root, source);
  let loseApplyOutput = true;
  const lossyRunner: DjangoJsonRunner = async (args) => {
    if (args[0] === "migrate_sales_from_d1" && args.includes("--apply") && loseApplyOutput) {
      loseApplyOutput = false;
      await machine.runner(args);
      throw new Error("simulated committed apply output loss");
    }
    return machine.runner(args);
  };
  const result = await executeSalesLocalCutover(options, {
    django: lossyRunner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  });
  assert.equal(result.status, "completed");
  assert.equal(machine.calls.filter((args) => args.includes("--dry-run")).length, 1);
  assert.equal(machine.calls.filter((args) => args.includes("--apply")).length, 1);
  assert.equal(machine.calls.filter((args) => args.includes("--recover-approved-apply")).length, 1);
  const applied = result.steps.find((step) => step.name === "sales_snapshot_applied")?.result;
  assert.equal(applied?.recoveredFromApproval, true);
});

test("activation response loss converges from external terminal authorities without rerunning cutover", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const machine = djangoStateMachine(source);
  const options = await orchestratorPaths(root, source);
  let loseActivationResponse = true;
  const lossyRunner: DjangoJsonRunner = async (args) => {
    if (args[0] === "sales_write_authority" && args[1] === "activate" && loseActivationResponse) {
      loseActivationResponse = false;
      await machine.runner(args);
      throw new Error("simulated activation response loss");
    }
    return machine.runner(args);
  };
  await assert.rejects(executeSalesLocalCutover(options, {
    django: lossyRunner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  }), /simulated activation response loss/);
  assert.equal(machine.pg.status, "active");
  const beforeRecoveryCalls = machine.calls.length;
  const recovered = await executeSalesLocalCutover(options, {
    django: lossyRunner,
    assertMaintenance: async () => { throw new Error("maintenance must not rerun"); },
    r2Client: mapClient(new Set()),
  });
  assert.equal(recovered.status, "completed");
  assert.deepEqual(machine.calls.slice(beforeRecoveryCalls).map((args) => args[0]), [
    "sales_write_authority",
    "sales_cutover_attestation_status",
  ]);
});

test("missing local state is rebuilt only from matching active authorities and an existing attestation", async () => {
  const root = await temporaryRoot();
  const source = path.join(root, "source.sqlite");
  await createOrchestratorDatabase(source);
  const machine = djangoStateMachine(source);
  const options = await orchestratorPaths(root, source);
  await executeSalesLocalCutover(options, {
    django: machine.runner,
    assertMaintenance: async () => undefined,
    r2Client: mapClient(new Set()),
  });
  const statePath = path.join(
    options.auditDirectory,
    `sales-cutover-${createHash("sha256").update(CUTOVER_ID).digest("hex").slice(0, 24)}.state.json`,
  );
  await rm(statePath);
  const recovered = await executeSalesLocalCutover(options, {
    django: machine.runner,
    assertMaintenance: async () => { throw new Error("maintenance must not rerun"); },
    r2Client: mapClient(new Set()),
  });
  assert.equal(recovered.status, "completed");
  assert.deepEqual(recovered.steps.map((step) => step.name), [
    "d1_terminal_attested",
    "postgres_authority_activated",
  ]);

  await writeFile(statePath, "{damaged-state", "utf8");
  const repairedCorruption = await executeSalesLocalCutover(options, {
    django: machine.runner,
    assertMaintenance: async () => { throw new Error("maintenance must not rerun"); },
    r2Client: mapClient(new Set()),
  });
  assert.equal(repairedCorruption.status, "completed");

  await rm(statePath);
  const noAttestationRunner: DjangoJsonRunner = async (args) => {
    if (args[0] === "sales_cutover_attestation_status") {
      throw new Error("existing attestation missing");
    }
    return machine.runner(args);
  };
  await assert.rejects(executeSalesLocalCutover(options, {
    django: noAttestationRunner,
    assertMaintenance: async () => { throw new Error("maintenance must not rerun"); },
    r2Client: mapClient(new Set()),
  }), /existing attestation missing/);
});
