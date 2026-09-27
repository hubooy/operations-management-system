import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import test from "node:test";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";

import type { CustomerServiceParseResult } from "../lib/customer-service/import-service";
import type { ParsedFinanceWorkbook } from "../lib/finance/types";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return { url: "data:text/javascript,export const env={};", shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { saveCustomerServiceImport } = await import("../lib/customer-service/database");
const { ensureFinanceSchema, saveFinanceImport } = await import("../lib/finance/database");
const { ensureErpReferenceSchema, saveProductMasterImport } = await import("../lib/erp-reference/database");
const { ensureInventorySchema, saveInventoryImport, syncInventoryStockDimensions } = await import("../lib/inventory/database");
const { ensureNetshopSchema, readNetshopScopeRows, saveNetshopImport } = await import("../lib/netshop/database");
const { PublicApiError } = await import("../lib/http/api-error");
const {
  buildImportAttemptHash,
  buildImportContentFingerprint,
  ensureImportFingerprintSchema,
  failImportFingerprint,
  nextImportScopeStateToken,
  recordImportFingerprint,
  recordRejectedImportAttempt,
  renewImportFingerprintReservation,
  reserveImportFingerprint,
} = await import("../lib/imports/content-fingerprint");
type FinanceDatabase = import("../lib/finance/database").FinanceDatabase;
type ErpReferenceDatabase = import("../lib/erp-reference/database").ErpReferenceDatabase;
type SalesDatabase = unknown;
type SalesLineInput = Record<string, string | number>;
const ensureSalesSchema = async (_db: SalesDatabase) => undefined;
const saveSalesImport = async (..._args: unknown[]): Promise<never> => {
  throw new Error("retired D1 sales contract");
};
type InventoryDatabase = import("../lib/inventory/database").InventoryDatabase;
type InventoryStockRow = import("../lib/imports/inventory-stock").InventoryStockRow;
type NetshopDatabase = import("../lib/netshop/database").NetshopDatabase;
type NetshopRowInput = import("../lib/netshop/database").NetshopRowInput;

type RunHook = (sql: string, values: readonly SQLInputValue[]) => void;

function sqliteAdapter(sqlite: DatabaseSync, hook: { beforeRun?: RunHook } = {}) {
  return {
    prepare(sql: string) {
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as SQLInputValue[]; return this; },
        async first<T>() { return (sqlite.prepare(sql).get(...values) ?? null) as T | null; },
        async all<T>() { return { results: sqlite.prepare(sql).all(...values) as T[] }; },
        async run() {
          hook.beforeRun?.(sql, values);
          const result = sqlite.prepare(sql).run(...values);
          return { meta: { changes: Number(result.changes) } };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      sqlite.exec("BEGIN");
      try {
        const results = [];
        for (const statement of statements) results.push(await statement.run());
        sqlite.exec("COMMIT");
        return results;
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
  };
}

test("0056 内容指纹迁移可重复执行并安装粗锁 ownership 索引", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE finance_months (status TEXT NOT NULL, batch_id TEXT NOT NULL);
    CREATE TABLE customer_service_conversations (shop_name TEXT NOT NULL, last_import_batch_id TEXT NOT NULL);
    CREATE TABLE erp_product_master (last_import_batch_id TEXT NOT NULL);
    CREATE TABLE erp_inventory_age_lines (last_import_batch_id TEXT NOT NULL);
    CREATE TABLE erp_combo_items (last_import_batch_id TEXT NOT NULL);
    CREATE TABLE market_ranking_entries (last_import_batch_id TEXT NOT NULL);
    CREATE TABLE netshop_rows (
      source TEXT NOT NULL, dataset TEXT NOT NULL, platform TEXT NOT NULL,
      shop_name TEXT NOT NULL, last_import_batch_id TEXT NOT NULL
    );
  `);
  const migration = await readFile(new URL("../drizzle/0056_import_content_fingerprints.sql", import.meta.url), "utf8");
  sqlite.exec(migration);
  sqlite.exec(migration);
  const indexes = sqlite.prepare(
    "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name",
  ).all().map((row) => String(row.name));
  assert.ok(indexes.includes("finance_months_status_batch_idx"));
  assert.ok(indexes.includes("customer_service_conversations_shop_last_batch_idx"));
  assert.ok(indexes.includes("market_entries_last_batch_idx"));
  assert.ok(indexes.includes("netshop_rows_lock_ownership_idx"));
  sqlite.close();
});

test("共享导入锁按唯一尝试隔离同批次并发，失败清理后允许安全重试", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  await ensureImportFingerprintSchema(db);
  const fingerprint = await buildImportContentFingerprint({
    domain: "test-import",
    scope: { source: "daily", startDate: "2026-08-01", endDate: "2026-08-01" },
    lockScope: { source: "daily" },
    rows: [{ businessDate: "2026-08-01", sku: "SKU-1", amount: 100 }],
  });
  const importHash = await buildImportAttemptHash({ fingerprint, currentStateToken: "initial" });
  const reservationInput = {
    ...fingerprint,
    batchId: importHash,
    importHash,
    rawFileHash: "a".repeat(64),
    currentStateToken: "initial",
  };
  const first = await reserveImportFingerprint(db, reservationInput);
  const concurrent = await reserveImportFingerprint(db, reservationInput);
  assert.equal(first.claimed, true);
  assert.equal(concurrent.claimed, false);
  assert.notEqual(concurrent.attemptId, first.attemptId);
  assert.equal(sqlite.prepare("SELECT owner_token ownerToken FROM import_scope_heads").get()?.ownerToken, first.attemptId);

  await failImportFingerprint(db, {
    ...reservationInput,
    attemptId: first.attemptId,
    errorCode: "INJECTED_FAILURE",
  });
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT state_token stateToken, status, owner_token ownerToken FROM import_scope_heads",
  ).get()! }, { stateToken: "initial", status: "ready", ownerToken: null });

  const retry = await reserveImportFingerprint(db, reservationInput);
  assert.equal(retry.claimed, true);
  await recordImportFingerprint(db, {
    ...reservationInput,
    attemptId: retry.attemptId,
    publishedStateToken: "published-a",
    outcome: "imported",
  });
  assert.deepEqual({ ...sqlite.prepare("SELECT state_token stateToken, status, owner_token ownerToken FROM import_scope_heads").get()! }, {
    stateToken: "published-a",
    status: "ready",
    ownerToken: null,
  });
  assert.deepEqual(sqlite.prepare("SELECT outcome, error_code errorCode FROM import_content_attempts ORDER BY sequence").all().map((row) => ({ ...row })), [
    { outcome: "failed", errorCode: "INJECTED_FAILURE" },
    { outcome: "superseded", errorCode: "IMPORT_SCOPE_BUSY" },
    { outcome: "imported", errorCode: "" },
  ]);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_content_fingerprints WHERE status='completed'").get()?.count, 1);
  sqlite.close();
});

test("过期 processing 租约仅在事实状态未变化时接管，旧 owner 不能清理新尝试", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  await ensureImportFingerprintSchema(db);
  const firstFingerprint = await buildImportContentFingerprint({
    domain: "stale-reservation",
    scope: { date: "2026-08-01" },
    lockScope: { source: "daily" },
    rows: [{ date: "2026-08-01", value: 1 }],
  });
  const firstHash = await buildImportAttemptHash({ fingerprint: firstFingerprint, currentStateToken: "initial" });
  const first = await reserveImportFingerprint(db, {
    ...firstFingerprint,
    batchId: firstHash,
    importHash: firstHash,
    rawFileHash: "1".repeat(64),
    currentStateToken: "initial",
  });
  assert.equal(first.claimed, true);
  sqlite.prepare(
    "UPDATE import_scope_heads SET updated_at = datetime('now', '-31 minutes') WHERE owner_token = ?",
  ).run(first.attemptId);

  const replacementFingerprint = await buildImportContentFingerprint({
    domain: "stale-reservation",
    scope: { date: "2026-08-02" },
    lockScope: { source: "daily" },
    rows: [{ date: "2026-08-02", value: 2 }],
  });
  const replacementHash = await buildImportAttemptHash({
    fingerprint: replacementFingerprint,
    currentStateToken: "initial",
  });
  const replacement = await reserveImportFingerprint(db, {
    ...replacementFingerprint,
    batchId: replacementHash,
    importHash: replacementHash,
    rawFileHash: "2".repeat(64),
    currentStateToken: "initial",
  });
  assert.equal(replacement.claimed, true);
  assert.equal(replacement.recoveredStaleReservation, true);
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT owner_token ownerToken, current_batch_id batchId FROM import_scope_heads",
  ).get()! }, { ownerToken: replacement.attemptId, batchId: replacementHash });
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT outcome, error_code errorCode FROM import_content_attempts WHERE attempt_id = ?",
  ).get(first.attemptId)! }, { outcome: "failed", errorCode: "IMPORT_RESERVATION_EXPIRED" });

  await failImportFingerprint(db, {
    ...firstFingerprint,
    batchId: firstHash,
    importHash: firstHash,
    rawFileHash: "1".repeat(64),
    attemptId: first.attemptId,
    errorCode: "LATE_OLD_OWNER_FAILURE",
  });
  assert.equal(sqlite.prepare("SELECT owner_token ownerToken FROM import_scope_heads").get()?.ownerToken, replacement.attemptId);
  assert.equal(sqlite.prepare(
    "SELECT COUNT(*) count FROM import_content_fingerprints WHERE batch_id = ? AND status = 'processing'",
  ).get(replacementHash)?.count, 1);
  await assert.rejects(renewImportFingerprintReservation(db, {
    ...firstFingerprint,
    batchId: firstHash,
    attemptId: first.attemptId,
  }), (error: unknown) => error instanceof PublicApiError && error.status === 409 && error.code === "conflict");
  await assert.rejects(recordImportFingerprint(db, {
    ...firstFingerprint,
    batchId: firstHash,
    importHash: firstHash,
    rawFileHash: "1".repeat(64),
    attemptId: first.attemptId,
    publishedStateToken: "late-old-state",
  }), (error: unknown) => error instanceof PublicApiError && error.status === 409 && error.code === "conflict");
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT outcome, error_code errorCode FROM import_content_attempts WHERE attempt_id = ?",
  ).get(first.attemptId)! }, { outcome: "failed", errorCode: "IMPORT_RESERVATION_EXPIRED" });
  sqlite.close();
});

test("迟到请求携带旧 ready token 时严格 CAS 拒绝且不能回拨新版本", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  await ensureImportFingerprintSchema(db);
  const firstFingerprint = await buildImportContentFingerprint({
    domain: "external-fact-change",
    scope: { shopName: "测试店铺", version: "a" },
    lockScope: { shopName: "测试店铺" },
    rows: [{ id: "A", value: 1 }],
  });
  const firstHash = await buildImportAttemptHash({ fingerprint: firstFingerprint, currentStateToken: "initial" });
  const first = await reserveImportFingerprint(db, {
    ...firstFingerprint,
    batchId: firstHash,
    importHash: firstHash,
    rawFileHash: "3".repeat(64),
    currentStateToken: "initial",
  });
  await recordImportFingerprint(db, {
    ...firstFingerprint,
    batchId: firstHash,
    importHash: firstHash,
    rawFileHash: "3".repeat(64),
    attemptId: first.attemptId,
    publishedStateToken: "state-newer",
  });

  const replacementFingerprint = await buildImportContentFingerprint({
    domain: "external-fact-change",
    scope: { shopName: "测试店铺", version: "b" },
    lockScope: { shopName: "测试店铺" },
    rows: [{ id: "A", value: 2 }],
  });
  const staleHash = await buildImportAttemptHash({
    fingerprint: replacementFingerprint,
    currentStateToken: "initial",
  });
  const stale = await reserveImportFingerprint(db, {
    ...replacementFingerprint,
    batchId: staleHash,
    importHash: staleHash,
    rawFileHash: "4".repeat(64),
    currentStateToken: "initial",
  });
  assert.equal(stale.resynchronizedState, false);
  assert.equal(stale.claimed, false);
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT state_token stateToken, status, owner_token ownerToken FROM import_scope_heads",
  ).get()! }, {
    stateToken: "state-newer",
    status: "ready",
    ownerToken: null,
  });
  const currentHash = await buildImportAttemptHash({ fingerprint: replacementFingerprint, currentStateToken: "state-newer" });
  const current = await reserveImportFingerprint(db, {
    ...replacementFingerprint,
    batchId: currentHash,
    importHash: currentHash,
    rawFileHash: "4".repeat(64),
    currentStateToken: "state-newer",
  });
  assert.equal(current.claimed, true);
  sqlite.close();
});

test("失败释放保留上一个已发布 state token，后续回滚不会碰撞历史批次", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  await ensureImportFingerprintSchema(db);
  const makeFingerprint = (value: number) => buildImportContentFingerprint({
    domain: "failure-state-chain",
    scope: { source: "snapshot" },
    rows: [{ id: "A", value }],
  });
  const firstFingerprint = await makeFingerprint(1);
  const firstHash = await buildImportAttemptHash({ fingerprint: firstFingerprint, currentStateToken: "initial" });
  const first = await reserveImportFingerprint(db, {
    ...firstFingerprint, batchId: firstHash, importHash: firstHash,
    rawFileHash: "5".repeat(64), currentStateToken: "initial",
  });
  await recordImportFingerprint(db, {
    ...firstFingerprint, batchId: firstHash, importHash: firstHash,
    rawFileHash: "5".repeat(64), attemptId: first.attemptId, publishedStateToken: "state-a",
  });
  const failedFingerprint = await makeFingerprint(2);
  const failedHash = await buildImportAttemptHash({ fingerprint: failedFingerprint, currentStateToken: "state-a" });
  const failed = await reserveImportFingerprint(db, {
    ...failedFingerprint, batchId: failedHash, importHash: failedHash,
    rawFileHash: "6".repeat(64), currentStateToken: "state-a",
  });
  await failImportFingerprint(db, {
    ...failedFingerprint, batchId: failedHash, importHash: failedHash,
    rawFileHash: "6".repeat(64), attemptId: failed.attemptId,
  });
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT state_token stateToken, status, owner_token ownerToken FROM import_scope_heads",
  ).get()! }, { stateToken: "state-a", status: "ready", ownerToken: null });
  const rollbackHash = await buildImportAttemptHash({ fingerprint: firstFingerprint, currentStateToken: "state-a" });
  assert.notEqual(rollbackHash, firstHash);
  assert.equal((await reserveImportFingerprint(db, {
    ...firstFingerprint, batchId: rollbackHash, importHash: rollbackHash,
    rawFileHash: "7".repeat(64), currentStateToken: "state-a",
  })).claimed, true);
  sqlite.close();
});

test("预留审计失败会回滚整次锁申请，不留下孤立 processing head", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let failAttemptInsert = false;
  const db = sqliteAdapter(sqlite, {
    beforeRun(sql) {
      if (failAttemptInsert && sql.includes("INSERT INTO import_content_attempts")) {
        throw new Error("injected reservation audit failure");
      }
    },
  }) as never;
  await ensureImportFingerprintSchema(db);
  const fingerprint = await buildImportContentFingerprint({
    domain: "atomic-reservation",
    scope: { source: "daily", date: "2026-08-01" },
    lockScope: { source: "daily" },
    rows: [{ date: "2026-08-01", value: 1 }],
  });
  const importHash = await buildImportAttemptHash({ fingerprint, currentStateToken: "initial" });
  failAttemptInsert = true;
  await assert.rejects(reserveImportFingerprint(db, {
    ...fingerprint,
    batchId: importHash,
    importHash,
    rawFileHash: "e".repeat(64),
    currentStateToken: "initial",
  }), /injected reservation audit failure/);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_scope_heads").get()?.count, 0);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_content_fingerprints").get()?.count, 0);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_content_attempts").get()?.count, 0);
  sqlite.close();
});

test("事实已发布但响应丢失时，精确重试会完成原尝试并释放写锁", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  await ensureImportFingerprintSchema(db);
  const firstFingerprint = await buildImportContentFingerprint({
    domain: "lost-response",
    scope: { source: "daily", date: "2026-08-01" },
    lockScope: { source: "daily" },
    rows: [{ date: "2026-08-01", value: 1 }],
  });
  const firstHash = await buildImportAttemptHash({ fingerprint: firstFingerprint, currentStateToken: "initial" });
  const firstReservation = await reserveImportFingerprint(db, {
    ...firstFingerprint,
    batchId: firstHash,
    importHash: firstHash,
    rawFileHash: "f".repeat(64),
    currentStateToken: "initial",
  });
  assert.equal(firstReservation.claimed, true);

  const recovered = await recordImportFingerprint(db, {
    ...firstFingerprint,
    batchId: firstHash,
    importHash: firstHash,
    rawFileHash: "f".repeat(64),
    publishedStateToken: "state-after-first-publish",
    outcome: "duplicate",
  });
  assert.equal(recovered.recovered, true);
  assert.notEqual(recovered.attemptId, firstReservation.attemptId);
  assert.equal(recovered.recoveredFromAttemptId, firstReservation.attemptId);
  const recoveredState = await nextImportScopeStateToken({
    previousStateToken: "initial",
    batchId: firstHash,
    contentHash: firstFingerprint.contentHash,
    rowCount: firstFingerprint.rowCount,
  });
  assert.deepEqual({ ...sqlite.prepare("SELECT status, owner_token ownerToken, state_token stateToken FROM import_scope_heads").get()! }, {
    status: "ready",
    ownerToken: null,
    stateToken: recoveredState,
  });
  assert.deepEqual({ ...sqlite.prepare("SELECT outcome, error_code errorCode FROM import_content_attempts WHERE attempt_id=?").get(firstReservation.attemptId)! }, {
    outcome: "imported",
    errorCode: "",
  });
  assert.deepEqual({ ...sqlite.prepare("SELECT outcome, recovered_from_attempt_id recoveredFrom FROM import_content_attempts WHERE attempt_id=?").get(recovered.attemptId)! }, {
    outcome: "duplicate",
    recoveredFrom: firstReservation.attemptId,
  });

  const changedFingerprint = await buildImportContentFingerprint({
    domain: "lost-response",
    scope: { source: "daily", date: "2026-08-01" },
    lockScope: { source: "daily" },
    rows: [{ date: "2026-08-01", value: 2 }],
  });
  const changedHash = await buildImportAttemptHash({
    fingerprint: changedFingerprint,
    currentStateToken: recoveredState,
  });
  const changedReservation = await reserveImportFingerprint(db, {
    ...changedFingerprint,
    batchId: changedHash,
    importHash: changedHash,
    rawFileHash: "0".repeat(64),
    currentStateToken: recoveredState,
  });
  assert.equal(changedReservation.claimed, true);
  await recordImportFingerprint(db, {
    ...changedFingerprint,
    batchId: changedHash,
    importHash: changedHash,
    rawFileHash: "0".repeat(64),
    publishedStateToken: recoveredState,
    outcome: "duplicate",
  });
  const secondRecoveredState = await nextImportScopeStateToken({
    previousStateToken: recoveredState,
    batchId: changedHash,
    contentHash: changedFingerprint.contentHash,
    rowCount: changedFingerprint.rowCount,
  });
  assert.equal(sqlite.prepare("SELECT state_token stateToken FROM import_scope_heads").get()?.stateToken, secondRecoveredState);
  const rollbackHash = await buildImportAttemptHash({ fingerprint: firstFingerprint, currentStateToken: secondRecoveredState });
  assert.notEqual(rollbackHash, firstHash);
  const rollback = await reserveImportFingerprint(db, {
    ...firstFingerprint,
    batchId: rollbackHash,
    importHash: rollbackHash,
    rawFileHash: "9".repeat(64),
    currentStateToken: secondRecoveredState,
  });
  assert.equal(rollback.claimed, true);
  sqlite.close();
});

test("重叠窗口共用粗粒度锁并支持 A 到 B 再回滚 A", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  await ensureImportFingerprintSchema(db);
  const makeFingerprint = (months: string[], amount: number) => buildImportContentFingerprint({
    domain: "finance-overlap",
    scope: { source: "monthly-finance-report", months },
    lockScope: { source: "monthly-finance-report" },
    rows: months.map((month) => ({ month, amount })),
  });
  const wideA = await makeFingerprint(["2026-07", "2026-08"], 100);
  const overlapB = await makeFingerprint(["2026-08", "2026-09"], 200);
  assert.equal(wideA.scopeKey, overlapB.scopeKey);

  const wideAHash = await buildImportAttemptHash({ fingerprint: wideA, currentStateToken: "initial" });
  const wideReservation = await reserveImportFingerprint(db, {
    ...wideA, batchId: wideAHash, importHash: wideAHash, rawFileHash: "b".repeat(64), currentStateToken: "initial",
  });
  assert.equal(wideReservation.claimed, true);
  const blockedOverlapHash = await buildImportAttemptHash({ fingerprint: overlapB, currentStateToken: "initial" });
  const blockedOverlap = await reserveImportFingerprint(db, {
    ...overlapB, batchId: blockedOverlapHash, importHash: blockedOverlapHash, rawFileHash: "c".repeat(64), currentStateToken: "initial",
  });
  assert.equal(blockedOverlap.claimed, false);
  await recordImportFingerprint(db, {
    ...wideA, batchId: wideAHash, importHash: wideAHash, rawFileHash: "b".repeat(64),
    attemptId: wideReservation.attemptId, publishedStateToken: "state-after-a", outcome: "imported",
  });

  const overlapHash = await buildImportAttemptHash({ fingerprint: overlapB, currentStateToken: "state-after-a" });
  const overlapReservation = await reserveImportFingerprint(db, {
    ...overlapB, batchId: overlapHash, importHash: overlapHash, rawFileHash: "c".repeat(64), currentStateToken: "state-after-a",
  });
  assert.equal(overlapReservation.claimed, true);
  await recordImportFingerprint(db, {
    ...overlapB, batchId: overlapHash, importHash: overlapHash, rawFileHash: "c".repeat(64),
    attemptId: overlapReservation.attemptId, publishedStateToken: "state-after-b", outcome: "imported",
  });

  const rollbackHash = await buildImportAttemptHash({ fingerprint: wideA, currentStateToken: "state-after-b" });
  assert.notEqual(rollbackHash, wideAHash);
  const rollback = await reserveImportFingerprint(db, {
    ...wideA, batchId: rollbackHash, importHash: rollbackHash, rawFileHash: "d".repeat(64), currentStateToken: "state-after-b",
  });
  assert.equal(rollback.claimed, true);
  await recordImportFingerprint(db, {
    ...wideA, batchId: rollbackHash, importHash: rollbackHash, rawFileHash: "d".repeat(64),
    attemptId: rollback.attemptId, publishedStateToken: "state-after-rollback", outcome: "imported",
  });
  assert.equal(sqlite.prepare("SELECT state_token stateToken FROM import_scope_heads").get()?.stateToken, "state-after-rollback");
  sqlite.close();
});

test("预校验拒绝只写尝试审计，不创建业务指纹或抢占 scope head", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  await recordRejectedImportAttempt(db, {
    domain: "sales",
    rawFileHash: "a".repeat(64),
    scopeHint: { startDate: "invalid", endDate: "2026-08-01" },
    errorCode: "INVALID_EXPECTED_DATE_RANGE",
    issues: [{ code: "INVALID_EXPECTED_DATE_RANGE", message: "日期无效" }],
    metadata: { fileName: "bad.xlsx", fileSizeBytes: 12 },
  });
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT domain, outcome, error_code errorCode, raw_file_hash rawFileHash, file_name fileName FROM import_content_attempts",
  ).get()! }, {
    domain: "sales",
    outcome: "rejected",
    errorCode: "INVALID_EXPECTED_DATE_RANGE",
    rawFileHash: "a".repeat(64),
    fileName: "bad.xlsx",
  });
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_content_fingerprints").get()?.count, 0);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_scope_heads").get()?.count, 0);
  sqlite.close();
});

test("事实已提交后异常清理释放 owner，精确 duplicate 重试仍会推进一次状态链", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  await ensureImportFingerprintSchema(db);
  const makeFingerprint = (value: string) => buildImportContentFingerprint({
    domain: "released-response-loss",
    scope: { source: "snapshot" },
    lockScope: { source: "snapshot" },
    rows: [{ id: "A", value }],
  });
  const firstFingerprint = await makeFingerprint("A");
  const firstHash = await buildImportAttemptHash({ fingerprint: firstFingerprint, currentStateToken: "initial" });
  const first = await reserveImportFingerprint(db, {
    ...firstFingerprint, batchId: firstHash, importHash: firstHash,
    rawFileHash: "1".repeat(64), currentStateToken: "initial",
  });
  await failImportFingerprint(db, {
    ...firstFingerprint, batchId: firstHash, importHash: firstHash,
    rawFileHash: "1".repeat(64), attemptId: first.attemptId,
  });
  await recordImportFingerprint(db, {
    ...firstFingerprint, batchId: firstHash, importHash: firstHash,
    rawFileHash: "2".repeat(64), publishedStateToken: "initial", outcome: "duplicate",
  });
  const stateAfterA = String(sqlite.prepare(
    "SELECT state_token stateToken FROM import_scope_heads WHERE domain='released-response-loss'",
  ).get()?.stateToken);
  assert.notEqual(stateAfterA, "initial");
  await recordImportFingerprint(db, {
    ...firstFingerprint, batchId: firstHash, importHash: firstHash,
    rawFileHash: "3".repeat(64), publishedStateToken: stateAfterA, outcome: "duplicate",
  });
  assert.equal(sqlite.prepare(
    "SELECT state_token stateToken FROM import_scope_heads WHERE domain='released-response-loss'",
  ).get()?.stateToken, stateAfterA);

  const secondFingerprint = await makeFingerprint("B");
  const secondHash = await buildImportAttemptHash({ fingerprint: secondFingerprint, currentStateToken: stateAfterA });
  const second = await reserveImportFingerprint(db, {
    ...secondFingerprint, batchId: secondHash, importHash: secondHash,
    rawFileHash: "4".repeat(64), currentStateToken: stateAfterA,
  });
  await failImportFingerprint(db, {
    ...secondFingerprint, batchId: secondHash, importHash: secondHash,
    rawFileHash: "4".repeat(64), attemptId: second.attemptId,
  });
  await recordImportFingerprint(db, {
    ...secondFingerprint, batchId: secondHash, importHash: secondHash,
    rawFileHash: "5".repeat(64), publishedStateToken: stateAfterA, outcome: "duplicate",
  });
  const stateAfterB = String(sqlite.prepare(
    "SELECT state_token stateToken FROM import_scope_heads WHERE domain='released-response-loss'",
  ).get()?.stateToken);
  assert.notEqual(stateAfterB, stateAfterA);
  const rollbackHash = await buildImportAttemptHash({ fingerprint: firstFingerprint, currentStateToken: stateAfterB });
  assert.notEqual(rollbackHash, firstHash);
  assert.equal((await reserveImportFingerprint(db, {
    ...firstFingerprint, batchId: rollbackHash, importHash: rollbackHash,
    rawFileHash: "6".repeat(64), currentStateToken: stateAfterB,
  })).claimed, true);
  sqlite.close();
});

function salesLine(orderNo: string, shipDate: string, amountCents: number, channel = "天猫"): SalesLineInput {
  return {
    sourceRowNumber: 1,
    sourceLineKey: orderNo,
    sourceRowHash: orderNo.padEnd(64, "0").slice(0, 64),
    orderNo,
    onlineOrderNo: orderNo,
    channel,
    platform: channel.startsWith("阿里巴巴-") ? "1688" : "天猫",
    shopName: channel.startsWith("阿里巴巴-") ? channel.slice("阿里巴巴-".length) : "测试店铺",
    logisticsCompany: "",
    warehouse: "正常仓",
    productCode: "SKU-1",
    onlineSpecCode: "SKU-1",
    productName: "测试商品",
    specification: "",
    barcode: "",
    supplier: "",
    category: "测试类目",
    quantity: 1,
    listUnitPriceCents: amountCents,
    costAmountCents: 0,
    allocatedUnitPriceCents: amountCents,
    allocatedAmountCents: amountCents,
    feeAllocationCents: 0,
    grossProfitCents: amountCents,
    grossMarginBps: 10_000,
    untaxedGrossProfitCents: amountCents,
    untaxedGrossMarginBps: 10_000,
    orderTime: `${shipDate} 08:00:00`,
    salesTime: `${shipDate} 08:00:00`,
    shipTime: `${shipDate} 09:00:00`,
    lineShipTime: `${shipDate} 09:00:00`,
    businessType: "sale",
  };
}

test.skip("已退役的 D1 销售提交栅栏由 Django 写服务契约测试替代", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as unknown as SalesDatabase;
  await ensureSalesSchema(db);
  await ensureImportFingerprintSchema(db as never);
  const scope = { source: "sales_ledger", startDate: "2026-08-01", endDate: "2026-08-01" };
  const oldRows = [salesLine("ORDER-FENCE", "2026-08-01", 100)];
  const oldFingerprint = await buildImportContentFingerprint({
    domain: "sales", scope, lockScope: { source: "sales_ledger" }, rows: oldRows,
    ignoredTopLevelKeys: ["sourceRowNumber", "sourceLineKey", "sourceRowHash"],
  });
  const oldHash = await buildImportAttemptHash({ fingerprint: oldFingerprint, currentStateToken: "initial" });
  const oldOwner = await reserveImportFingerprint(db as never, {
    ...oldFingerprint, batchId: oldHash, importHash: oldHash,
    rawFileHash: "8".repeat(64), currentStateToken: "initial",
  });
  await renewImportFingerprintReservation(db as never, {
    ...oldFingerprint, batchId: oldHash, attemptId: oldOwner.attemptId,
  });
  sqlite.prepare("UPDATE import_scope_heads SET updated_at=datetime('now', '-31 minutes')").run();

  const newRows = [salesLine("ORDER-FENCE", "2026-08-01", 200)];
  const newFingerprint = await buildImportContentFingerprint({
    domain: "sales", scope, lockScope: { source: "sales_ledger" }, rows: newRows,
    ignoredTopLevelKeys: ["sourceRowNumber", "sourceLineKey", "sourceRowHash"],
  });
  const newHash = await buildImportAttemptHash({ fingerprint: newFingerprint, currentStateToken: "initial" });
  const newOwner = await reserveImportFingerprint(db as never, {
    ...newFingerprint, batchId: newHash, importHash: newHash,
    rawFileHash: "9".repeat(64), currentStateToken: "initial",
  });
  assert.equal(newOwner.recoveredStaleReservation, true);
  await saveSalesImport(db, {
    fileHash: newHash, fileName: "new.xlsx", fileSizeBytes: 1, sheetName: "销售",
    rows: newRows, warnings: [], totals: {}, contentHash: newFingerprint.contentHash,
    replaceStartDate: "2026-08-01", replaceEndDate: "2026-08-01",
    reservationFence: { domain: newFingerprint.domain, scopeKey: newFingerprint.scopeKey, batchId: newHash, attemptId: newOwner.attemptId },
  });
  await recordImportFingerprint(db as never, {
    ...newFingerprint, batchId: newHash, importHash: newHash,
    rawFileHash: "9".repeat(64), attemptId: newOwner.attemptId, publishedStateToken: "state-new",
  });

  await assert.rejects(saveSalesImport(db, {
    fileHash: oldHash, fileName: "old.xlsx", fileSizeBytes: 1, sheetName: "销售",
    rows: oldRows, warnings: [], totals: {}, contentHash: oldFingerprint.contentHash,
    replaceStartDate: "2026-08-01", replaceEndDate: "2026-08-01",
    reservationFence: { domain: oldFingerprint.domain, scopeKey: oldFingerprint.scopeKey, batchId: oldHash, attemptId: oldOwner.attemptId },
  }), (error: unknown) => error instanceof PublicApiError && error.status === 409 && error.code === "conflict");
  assert.equal(sqlite.prepare(
    "SELECT allocated_amount_cents amount FROM sales_order_lines WHERE order_no='ORDER-FENCE'",
  ).get()?.amount, 200);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM sales_import_batches WHERE id=?").get(oldHash)?.count, 0);
  sqlite.close();
});

test.skip("已退役的 D1 销售日期替换由 Django 写服务契约测试替代", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as unknown as SalesDatabase;
  await ensureSalesSchema(db);
  await saveSalesImport(db, {
    fileHash: "1".repeat(64),
    fileName: "sales-a.xlsx",
    fileSizeBytes: 1,
    sheetName: "销售",
    rows: [salesLine("ORDER-START", "2026-07-01", 100), salesLine("ORDER-END", "2026-07-31", 200)],
    warnings: [],
    totals: {},
    contentHash: "1".repeat(64),
    replaceStartDate: "2026-07-01",
    replaceEndDate: "2026-07-31",
  });
  await saveSalesImport(db, {
    fileHash: "2".repeat(64),
    fileName: "sales-b.xlsx",
    fileSizeBytes: 1,
    sheetName: "销售",
    rows: [salesLine("ORDER-START", "2026-07-01", 300)],
    warnings: [],
    totals: {},
    contentHash: "2".repeat(64),
    replaceStartDate: "2026-07-01",
    replaceEndDate: "2026-07-31",
  });
  assert.equal(sqlite.prepare("SELECT sales_revision revision FROM sales_overview_cache_state WHERE id=1").get()?.revision, 3);
  assert.deepEqual(sqlite.prepare(
    "SELECT order_no orderNo, allocated_amount_cents amount, last_import_batch_id batchId FROM sales_order_lines ORDER BY order_no",
  ).all().map((row) => ({ ...row })), [{
    orderNo: "ORDER-START",
    amount: 300,
    batchId: "2".repeat(64),
  }]);
  sqlite.close();
});

test.skip("已退役的 D1 销售渠道替换由 Django 写服务契约测试替代", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as unknown as SalesDatabase;
  await ensureSalesSchema(db);
  const alibabaChannels = ["阿里巴巴-炊之王店", "阿里巴巴-亿用店", "阿里巴巴-震坤行"];
  await saveSalesImport(db, {
    fileHash: "3".repeat(64),
    fileName: "sales-all.xlsx",
    fileSizeBytes: 1,
    sheetName: "销售",
    rows: [
      salesLine("JD-KEEP", "2026-07-01", 100, "京东-志高商用厨电旗舰店"),
      salesLine("ALI-OLD", "2026-07-01", 200, "阿里巴巴-炊之王店"),
    ],
    warnings: [],
    totals: {},
    contentHash: "3".repeat(64),
    replaceStartDate: "2026-07-01",
    replaceEndDate: "2026-07-31",
  });
  await saveSalesImport(db, {
    fileHash: "4".repeat(64),
    fileName: "sales-alibaba.xlsx",
    fileSizeBytes: 1,
    sheetName: "销售",
    rows: [salesLine("ALI-NEW", "2026-07-01", 300, "阿里巴巴-亿用店")],
    warnings: [],
    totals: {},
    contentHash: "4".repeat(64),
    replaceStartDate: "2026-07-01",
    replaceEndDate: "2026-07-31",
    replaceChannels: alibabaChannels,
  });
  assert.deepEqual(sqlite.prepare(
    "SELECT order_no orderNo, channel, allocated_amount_cents amount FROM sales_order_lines ORDER BY order_no",
  ).all().map((row) => ({ ...row })), [
    { orderNo: "ALI-NEW", channel: "阿里巴巴-亿用店", amount: 300 },
    { orderNo: "JD-KEEP", channel: "京东-志高商用厨电旗舰店", amount: 100 },
  ]);
  await assert.rejects(saveSalesImport(db, {
    fileHash: "5".repeat(64),
    fileName: "sales-invalid-scope.xlsx",
    fileSizeBytes: 1,
    sheetName: "销售",
    rows: [salesLine("JD-OUTSIDE", "2026-07-01", 400, "京东-志高商用厨电旗舰店")],
    warnings: [],
    totals: {},
    contentHash: "5".repeat(64),
    replaceStartDate: "2026-07-01",
    replaceEndDate: "2026-07-31",
    replaceChannels: alibabaChannels,
  }), /不属于权威替换范围/);
  sqlite.close();
});

function inventoryRow(productCode: string, sourceRowNumber: number, quantity: number): InventoryStockRow {
  const warehouse = "正常仓";
  return {
    sourceRowNumber,
    rowKey: JSON.stringify([warehouse, productCode]),
    snapshotDate: "2026-08-01",
    warehouse,
    warehouseType: "owned",
    warehouseCategory: "selfOperated",
    includeInInventory: true,
    productCode,
    productName: productCode,
    brand: "测试品牌",
    supplier: "测试供应商",
    specification: "",
    barcode: "",
    category: "测试类目",
    onHandQuantity: quantity,
    availableQuantity: quantity,
    lockedQuantity: 0,
    inTransitQuantity: 0,
    unitCostCents: 100,
    inventoryAgeDays: 1,
    sales7dQuantity: 1,
    sales30dQuantity: 1,
  };
}

test("库存业务行重排后的维度同步不会新增技术键或翻倍库存", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as unknown as InventoryDatabase;
  await ensureInventorySchema(db);
  const batchId = "8".repeat(64);
  await saveInventoryImport(db, {
    fileHash: batchId,
    fileName: "inventory-a.xlsx",
    fileSizeBytes: 1,
    sheetName: "库存",
    snapshotDate: "2026-08-01",
    rows: [inventoryRow("P1", 2, 10), inventoryRow("P2", 3, 20)],
    warnings: [],
    totals: {},
  });
  await syncInventoryStockDimensions(db, {
    batchId,
    snapshotDate: "2026-08-01",
    rows: [inventoryRow("P2", 2, 20), inventoryRow("P1", 3, 10)],
  });
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT COUNT(*) rowCount, SUM(on_hand_quantity) quantity FROM inventory_stock_lines WHERE batch_id=?",
  ).get(batchId)! }, { rowCount: 2, quantity: 30 });
  assert.deepEqual(sqlite.prepare(
    "SELECT row_key rowKey FROM inventory_stock_lines WHERE batch_id=? ORDER BY row_key",
  ).all(batchId).map((row) => row.rowKey), [JSON.stringify(["正常仓", "P1"]), JSON.stringify(["正常仓", "P2"])]);
  sqlite.close();
});

function netshopRow(input: {
  source: "tmall_product_daily" | "tmall_promotion";
  dataset: "spu_daily" | "promotion_daily";
  shopName: string;
  businessDate: string;
  productCode: string;
  amount: number;
}): NetshopRowInput {
  const identity = [input.source, input.dataset, "天猫", input.shopName, input.businessDate, input.productCode];
  return {
    sourceRowNumber: 1,
    sourceRowKey: JSON.stringify(identity),
    sourceRowHash: input.productCode.padEnd(64, "0").slice(0, 64),
    source: input.source,
    dataset: input.dataset,
    platform: "天猫",
    shopName: input.shopName,
    businessDate: input.businessDate,
    snapshotDate: "",
    productCode: input.productCode,
    productName: input.productCode,
    skuId: input.productCode,
    spuId: input.productCode,
    warehouseType: "",
    metrics: { transactionAmountCents: input.amount },
    raw: { productCode: input.productCode },
  };
}

test("网店同日差异内容完整替换缺失行，并保持跨店铺与数据集隔离", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as unknown as NetshopDatabase;
  await ensureNetshopSchema(db);
  const save = (input: {
    source: "tmall_product_daily" | "tmall_promotion";
    dataset: "spu_daily" | "promotion_daily";
    shopName: string;
    fileHash: string;
    rows: NetshopRowInput[];
  }) => saveNetshopImport(db, {
    ...input,
    platform: "天猫",
    fileName: `${input.fileHash.slice(0, 4)}.xlsx`,
    fileSizeBytes: 1,
    sheetName: "数据",
    warnings: [],
    totals: {},
    note: "",
    replaceScope: { startDate: "2026-08-01", endDate: "2026-08-01" },
  });
  await save({
    source: "tmall_product_daily",
    dataset: "spu_daily",
    shopName: "店铺A",
    fileHash: "a".repeat(64),
    rows: [
      netshopRow({ source: "tmall_product_daily", dataset: "spu_daily", shopName: "店铺A", businessDate: "2026-08-01", productCode: "P1", amount: 100 }),
      netshopRow({ source: "tmall_product_daily", dataset: "spu_daily", shopName: "店铺A", businessDate: "2026-08-01", productCode: "P2", amount: 200 }),
    ],
  });
  await save({
    source: "tmall_product_daily",
    dataset: "spu_daily",
    shopName: "店铺B",
    fileHash: "b".repeat(64),
    rows: [netshopRow({ source: "tmall_product_daily", dataset: "spu_daily", shopName: "店铺B", businessDate: "2026-08-01", productCode: "P2", amount: 300 })],
  });
  await save({
    source: "tmall_promotion",
    dataset: "promotion_daily",
    shopName: "店铺A",
    fileHash: "c".repeat(64),
    rows: [netshopRow({ source: "tmall_promotion", dataset: "promotion_daily", shopName: "店铺A", businessDate: "2026-08-01", productCode: "P2", amount: 400 })],
  });
  await save({
    source: "tmall_promotion",
    dataset: "promotion_daily",
    shopName: "店铺A",
    fileHash: "c".repeat(64),
    rows: [netshopRow({ source: "tmall_promotion", dataset: "promotion_daily", shopName: "店铺A", businessDate: "2026-08-01", productCode: "P3", amount: 999 })],
  });
  await save({
    source: "tmall_product_daily",
    dataset: "spu_daily",
    shopName: "店铺A",
    fileHash: "d".repeat(64),
    rows: [netshopRow({ source: "tmall_product_daily", dataset: "spu_daily", shopName: "店铺A", businessDate: "2026-08-01", productCode: "P1", amount: 500 })],
  });
  assert.deepEqual(sqlite.prepare(
    `SELECT source, dataset, shop_name shopName, product_code productCode,
            CAST(json_extract(metrics_json, '$.transactionAmountCents') AS INTEGER) amount
     FROM netshop_rows ORDER BY source, shop_name, product_code`,
  ).all().map((row) => ({ ...row })), [
    { source: "tmall_product_daily", dataset: "spu_daily", shopName: "店铺A", productCode: "P1", amount: 500 },
    { source: "tmall_product_daily", dataset: "spu_daily", shopName: "店铺B", productCode: "P2", amount: 300 },
    { source: "tmall_promotion", dataset: "promotion_daily", shopName: "店铺A", productCode: "P2", amount: 400 },
  ]);
  assert.deepEqual(sqlite.prepare(
    `SELECT p.platform, p.shop_name shopName, p.business_date businessDate,
            p.product_id productId, p.net_transaction_amount_cents amount,
            state.ready
     FROM netshop_promotion_product_daily p
     INNER JOIN netshop_promotion_aggregate_state state
       ON state.platform = p.platform
      AND state.shop_name = p.shop_name
      AND state.business_date = p.business_date
     ORDER BY p.platform, p.shop_name, p.business_date, p.product_id`,
  ).all().map((row) => ({ ...row })), [
    { platform: "天猫", shopName: "店铺A", businessDate: "2026-08-01", productId: "P2", amount: 0, ready: 1 },
  ]);
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT data_version dataVersion FROM netshop_product_daily_revisions WHERE platform='天猫'",
  ).get()! }, { dataVersion: 3 });
  assert.deepEqual(sqlite.prepare(`SELECT shop_name shopName,data_version dataVersion
    FROM netshop_product_daily_scope_revisions WHERE platform='天猫' ORDER BY shop_name`).all()
    .map((row) => ({ ...row })), [
    { shopName: "店铺A", dataVersion: 2 },
    { shopName: "店铺B", dataVersion: 1 },
  ]);
  sqlite.close();
});

test("网店当前事实回读把数据库 NULL 日期还原为空字符串", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as unknown as NetshopDatabase;
  await ensureNetshopSchema(db);
  const row: NetshopRowInput = {
    sourceRowNumber: 1,
    sourceRowKey: "tmall-master-null-date",
    sourceRowHash: "9".repeat(64),
    source: "tmall_product_master",
    dataset: "product_master",
    platform: "天猫",
    shopName: "测试店铺",
    businessDate: "",
    snapshotDate: "2026-08-01",
    productCode: "P1",
    productName: "商品1",
    skuId: "SKU1",
    spuId: "SPU1",
    warehouseType: "",
    metrics: {},
    raw: { 商品ID: "SPU1" },
  };
  await saveNetshopImport(db, {
    source: row.source,
    dataset: row.dataset,
    platform: row.platform,
    shopName: row.shopName,
    fileHash: "9".repeat(64),
    fileName: "master.xlsx",
    fileSizeBytes: 1,
    sheetName: "商品",
    rows: [row],
    warnings: [],
    totals: {},
    note: "",
    replaceScope: { snapshotDate: row.snapshotDate },
  });
  const currentRows = await readNetshopScopeRows(db, {
    source: row.source,
    dataset: row.dataset,
    platform: row.platform,
    shopName: row.shopName,
    snapshotDate: row.snapshotDate,
  });
  assert.equal(currentRows.length, 1);
  assert.equal(currentRows[0]?.businessDate, "");
  assert.equal(currentRows[0]?.snapshotDate, row.snapshotDate);
  sqlite.close();
});

function financeWorkbook(month: string, amountCents: number): ParsedFinanceWorkbook {
  return {
    sourceSheetCount: 1,
    warnings: [],
    months: [{
      month,
      sheetName: month,
      businessName: "志高事业部",
      shopCount: 1,
      subjectCount: 0,
      lines: [{
        month,
        section: "summary",
        metricKey: "net_sales",
        subjectName: "实际销售金额",
        scopeKey: "business:all",
        scopeType: "business",
        scopeName: "志高事业部",
        groupName: "",
        valueType: "amount",
        amountCents,
        rateBps: null,
        rawValue: String(amountCents / 100),
        sourceRowCount: 1,
        sortOrder: 1,
        isTotal: true,
      }],
    }],
  };
}

test("财务差异内容原子替换月份，相同尝试不重写，失败可用同一尝试恢复", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let failAugust = false;
  const db = sqliteAdapter(sqlite, {
    beforeRun(sql, values) {
      if (failAugust && sql.includes("INSERT INTO finance_lines") && String(values[0]).includes("2026-08")) {
        throw new Error("injected finance publish failure");
      }
    },
  }) as unknown as FinanceDatabase;
  await ensureFinanceSchema(db);

  const first = await saveFinanceImport(db, {
    fileHash: "a".repeat(64), fileName: "finance-a.xlsx", fileSizeBytes: 1,
    parsed: financeWorkbook("2026-07", 10_000),
  });
  assert.equal(first.created, true);
  const duplicate = await saveFinanceImport(db, {
    fileHash: "a".repeat(64), fileName: "finance-a-copy.xlsx", fileSizeBytes: 2,
    parsed: financeWorkbook("2026-07", 10_000),
  });
  assert.equal(duplicate.created, false);

  const changed = await saveFinanceImport(db, {
    fileHash: "b".repeat(64), fileName: "finance-b.xlsx", fileSizeBytes: 2,
    parsed: financeWorkbook("2026-07", 20_000),
  });
  assert.equal(changed.created, true);
  assert.deepEqual({ ...sqlite.prepare("SELECT amount_cents amount, batch_id batchId FROM finance_lines JOIN finance_months USING(month) WHERE month='2026-07'").get() }, {
    amount: 20_000,
    batchId: "b".repeat(64),
  });

  const twoMonths: ParsedFinanceWorkbook = {
    sourceSheetCount: 2,
    warnings: [],
    months: [financeWorkbook("2026-07", 30_000).months[0]!, financeWorkbook("2026-08", 40_000).months[0]!],
  };
  failAugust = true;
  await assert.rejects(saveFinanceImport(db, {
    fileHash: "c".repeat(64), fileName: "finance-c.xlsx", fileSizeBytes: 3, parsed: twoMonths,
  }), /injected finance publish failure/);
  assert.equal(sqlite.prepare("SELECT amount_cents amount FROM finance_lines WHERE month='2026-07'").get()?.amount, 20_000);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM finance_lines WHERE month='2026-08'").get()?.count, 0);

  failAugust = false;
  const recovered = await saveFinanceImport(db, {
    fileHash: "c".repeat(64), fileName: "finance-c.xlsx", fileSizeBytes: 3, parsed: twoMonths,
  });
  assert.equal(recovered.created, true);
  assert.deepEqual(recovered.importedMonths, ["2026-07", "2026-08"]);
  sqlite.close();
});

test("ERP 全量货品差异导入会删除旧快照残留，并发相同尝试只创建一个批次", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as unknown as ErpReferenceDatabase;
  await ensureErpReferenceSchema(db);
  const row = (productCode: string, productName: string, sourceRowNumber: number) => ({
    sourceRowNumber,
    productCode,
    productName,
    brand: "品牌",
    specification: "规格",
    barcode: "",
    category: "类目",
    supplier: "供应商",
    productStatus: "启用",
  });
  const first = await saveProductMasterImport(db, {
    id: `products:${"d".repeat(64)}`,
    fileName: "products-a.xlsx",
    fileSizeBytes: 1,
    fileHash: "d".repeat(64),
    sheetName: "货品",
    rows: [row("P1", "货品1", 1), row("P2", "货品2", 2)],
    warnings: [],
    totals: {},
    contentHash: "d".repeat(64),
  });
  assert.equal(first.created, true);
  assert.equal(sqlite.prepare("SELECT erp_revision revision FROM erp_product_projection_state WHERE id=1").get()?.revision, 2);
  const changed = await saveProductMasterImport(db, {
    id: `products:${"e".repeat(64)}`,
    fileName: "products-b.xlsx",
    fileSizeBytes: 1,
    fileHash: "e".repeat(64),
    sheetName: "货品",
    rows: [row("P1", "货品1更新", 1)],
    warnings: [],
    totals: {},
    contentHash: "e".repeat(64),
  });
  assert.equal(changed.created, true);
  assert.equal(sqlite.prepare("SELECT erp_revision revision FROM erp_product_projection_state WHERE id=1").get()?.revision, 3);
  assert.deepEqual(sqlite.prepare("SELECT product_code productCode, product_name productName FROM erp_product_master").all().map((item) => ({ ...item })), [
    { productCode: "P1", productName: "货品1更新" },
  ]);
  const duplicateAttempt = await saveProductMasterImport(db, {
    id: `products:${"e".repeat(64)}`,
    fileName: "renamed.xlsx",
    fileSizeBytes: 2,
    fileHash: "e".repeat(64),
    sheetName: "货品",
    rows: [row("P1", "货品1更新", 99)],
    warnings: [],
    totals: {},
    contentHash: "e".repeat(64),
  });
  assert.equal(duplicateAttempt.created, false);
  assert.equal(sqlite.prepare("SELECT erp_revision revision FROM erp_product_projection_state WHERE id=1").get()?.revision, 3);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM erp_reference_import_batches").get()?.count, 2);
  sqlite.close();
});

test("财务旧 owner 的迟到失败不能把 takeover 后的新 owner 批次标记失败", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const base = sqliteAdapter(sqlite);
  let takeoverOnNextBatch = false;
  let takeoverApplied = false;
  const newAttemptId = "finance-new-owner";
  const db = {
    prepare: base.prepare,
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      if (takeoverOnNextBatch && !takeoverApplied) {
        takeoverApplied = true;
        sqlite.prepare(
          `UPDATE import_scope_heads
           SET owner_token = ?, status = 'processing', updated_at = CURRENT_TIMESTAMP
           WHERE domain = 'finance'`,
        ).run(newAttemptId);
      }
      return base.batch(statements);
    },
  } as unknown as FinanceDatabase;
  await ensureFinanceSchema(db);
  await ensureImportFingerprintSchema(db as never);
  const parsed = financeWorkbook("2026-09", 50_000);
  const fingerprint = await buildImportContentFingerprint({
    domain: "finance",
    scope: { source: "monthly-finance-report", months: ["2026-09"] },
    lockScope: { source: "monthly-finance-report" },
    rows: parsed.months[0]!.lines,
  });
  const fileHash = await buildImportAttemptHash({ fingerprint, currentStateToken: "initial" });
  const oldOwner = await reserveImportFingerprint(db as never, {
    ...fingerprint,
    batchId: fileHash,
    importHash: fileHash,
    rawFileHash: "7".repeat(64),
    currentStateToken: "initial",
  });
  takeoverOnNextBatch = true;
  await assert.rejects(saveFinanceImport(db, {
    fileHash,
    fileName: "finance-old-owner.xlsx",
    fileSizeBytes: 1,
    parsed,
    reservationFence: {
      domain: fingerprint.domain,
      scopeKey: fingerprint.scopeKey,
      batchId: fileHash,
      attemptId: oldOwner.attemptId,
    },
  }));
  assert.equal(takeoverApplied, true);
  assert.equal(sqlite.prepare("SELECT status FROM finance_import_batches WHERE id=?").get(fileHash)?.status, "processing");
  assert.equal(sqlite.prepare("SELECT owner_token ownerToken FROM import_scope_heads WHERE domain='finance'").get()?.ownerToken, newAttemptId);

  sqlite.prepare("UPDATE finance_import_batches SET created_at=datetime('now', '-31 minutes') WHERE id=?").run(fileHash);
  const recovered = await saveFinanceImport(db, {
    fileHash,
    fileName: "finance-new-owner.xlsx",
    fileSizeBytes: 1,
    parsed,
    reservationFence: {
      domain: fingerprint.domain,
      scopeKey: fingerprint.scopeKey,
      batchId: fileHash,
      attemptId: newAttemptId,
    },
  });
  assert.equal(recovered.batch.status, "completed");
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM finance_lines WHERE month='2026-09'").get()?.count, 1);
  sqlite.close();
});

function customerParse(agent: string, sourceRowNumber = 1): CustomerServiceParseResult {
  return {
    summary: {
      sessionCount: 1,
      chatSessionCount: 1,
      matchedCount: 1,
      timeOnlyMatchedCount: 0,
      sessionOnlyCount: 0,
      chatOnlyCount: 0,
      ambiguousCount: 0,
    },
    warnings: [],
    conversations: [{
      sourceRowNumber,
      consultedAt: "2026-08-05 10:00:00",
      customerId: "customer-hash",
      customerAlias: "顾客A",
      consultationType: "售前",
      agent,
      transferredAgent: "",
      skillGroup: "售前组",
      productSku: "SKU-1",
      productName: "测试商品",
      firstResponseAt: "2026-08-05 10:00:05",
      responseSeconds: 5,
      durationMinutes: 2,
      customerMessageCount: 1,
      agentMessageCount: 1,
      satisfaction: "满意",
      resolved: "是",
      conversationId: "conversation-1",
      conversationKey: "conversation-1",
      matchStatus: "matched",
      matchConfidence: "exact",
      chatStartedAt: "2026-08-05 10:00:00",
      chatEndedAt: "2026-08-05 10:02:00",
      chatCustomerAlias: "顾客A",
      messages: [{ sender: "顾客", sentAt: "2026-08-05 10:00:00", content: "你好" }],
    }],
  };
}

test.skip("客服以完整标准化内容判重，忽略源行号并原子发布字段变化（行为已由 Django customer_service 测试覆盖）", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let failPublish = false;
  const db = sqliteAdapter(sqlite, {
    beforeRun(sql) {
      if (failPublish && sql.includes("INSERT INTO customer_service_conversations")) {
        throw new Error("injected customer publish failure");
      }
    },
  });

  const first = await saveCustomerServiceImport({
    shopName: "测试店铺", sessionFileName: "session-a.xlsx", chatFileName: "chat-a.txt",
    fileHash: "1".repeat(64), parsed: customerParse("客服A", 1),
  }, db as never);
  assert.equal(first.status, "imported");

  const sameBusinessContent = await saveCustomerServiceImport({
    shopName: "测试店铺", sessionFileName: "renamed.xlsx", chatFileName: "renamed.txt",
    fileHash: "2".repeat(64), parsed: customerParse("客服A", 99),
  }, db as never);
  assert.equal(sameBusinessContent.status, "duplicate");
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM customer_service_import_batches").get()?.count, 1);

  const changed = await saveCustomerServiceImport({
    shopName: "测试店铺", sessionFileName: "session-b.xlsx", chatFileName: "chat-b.txt",
    fileHash: "3".repeat(64), parsed: customerParse("客服B", 1),
  }, db as never);
  assert.equal(changed.status, "imported");
  assert.equal(sqlite.prepare("SELECT agent FROM customer_service_conversations").get()?.agent, "客服B");

  failPublish = true;
  await assert.rejects(saveCustomerServiceImport({
    shopName: "测试店铺", sessionFileName: "session-c.xlsx", chatFileName: "chat-c.txt",
    fileHash: "4".repeat(64), parsed: customerParse("客服C", 1),
  }, db as never), /injected customer publish failure/);
  assert.equal(sqlite.prepare("SELECT agent FROM customer_service_conversations").get()?.agent, "客服B");
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM customer_service_import_batches").get()?.count, 2);
  sqlite.close();
});

test.skip("客服服务层精确重试会恢复事实已发布但指纹回写丢失的尝试（行为已由 Django customer_service 测试覆盖）", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  const first = await saveCustomerServiceImport({
    shopName: "恢复测试店铺",
    sessionFileName: "session-first.xlsx",
    chatFileName: "chat-first.txt",
    fileHash: "5".repeat(64),
    parsed: customerParse("客服A", 1),
  }, db as never);
  assert.equal(first.status, "imported");

  const lostAttemptId = "lost-response-service-attempt";
  sqlite.prepare(
    `INSERT INTO import_content_attempts (
      attempt_id, domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
      content_hash, row_count, outcome
    ) SELECT ?, domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
             content_hash, row_count, 'processing'
      FROM import_content_fingerprints
      WHERE domain = 'customer-service' AND batch_id = ?`,
  ).run(lostAttemptId, first.batch.id);
  sqlite.prepare(
    `UPDATE import_content_fingerprints
     SET status = 'processing', publication_sequence = NULL
     WHERE domain = 'customer-service' AND batch_id = ?`,
  ).run(first.batch.id);
  sqlite.prepare(
    `UPDATE import_scope_heads
     SET state_token = 'stale-before-publish', status = 'processing', owner_token = ?, current_batch_id = NULL
     WHERE domain = 'customer-service'`,
  ).run(lostAttemptId);

  const recovered = await saveCustomerServiceImport({
    shopName: "恢复测试店铺",
    sessionFileName: "session-retry.xlsx",
    chatFileName: "chat-retry.txt",
    fileHash: "6".repeat(64),
    parsed: customerParse("客服A", 99),
  }, db as never);
  assert.equal(recovered.status, "duplicate");
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT status, owner_token ownerToken FROM import_scope_heads WHERE domain='customer-service'",
  ).get()! }, { status: "ready", ownerToken: null });
  assert.equal(sqlite.prepare("SELECT outcome FROM import_content_attempts WHERE attempt_id=?").get(lostAttemptId)?.outcome, "imported");
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT outcome, raw_file_hash rawFileHash, recovered_from_attempt_id recoveredFrom FROM import_content_attempts WHERE recovered_from_attempt_id=?",
  ).get(lostAttemptId)! }, {
    outcome: "duplicate",
    rawFileHash: "6".repeat(64),
    recoveredFrom: lostAttemptId,
  });

  const changed = await saveCustomerServiceImport({
    shopName: "恢复测试店铺",
    sessionFileName: "session-changed.xlsx",
    chatFileName: "chat-changed.txt",
    fileHash: "7".repeat(64),
    parsed: customerParse("客服B", 1),
  }, db as never);
  assert.equal(changed.status, "imported");
  sqlite.close();
});
