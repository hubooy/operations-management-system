import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";

import {
  acquireMarketImageCacheJobLease,
  createOrResumeMarketImageCacheJob,
  discoverMarketImageCacheJobItems,
  finishMarketImageCacheJobLease,
  getMarketImageCacheJob,
  heartbeatMarketImageCacheJobLease,
  listMarketImageCacheJobItems,
  quarantineTimedOutMarketImageCacheJobLease,
  terminateTimedOutMarketImageCacheJobLease,
} from "../lib/market/image-cache-job";
import { parseMarketImageCacheGetQuery, parseMarketImageCachePostBody } from "../lib/market/image-cache-request";
import {
  claimMarketImageCache,
  completeMarketImageCacheClaim,
  failMarketImageCacheClaim,
  propagateMarketImageCacheBatch,
  recoverExpiredMarketImageCacheClaims,
} from "../lib/market/image-cache-state";
import { ensureMarketSchemaCached, ensureMarketSchemaCore, type MarketSchemaDatabase } from "../lib/market/schema-core";

type CapturedStatement = { sql: string; values: SQLInputValue[] };

function sqliteAdapter(sqlite: DatabaseSync, captured: CapturedStatement[] = []): MarketSchemaDatabase {
  return {
    prepare(sql: string) {
      const statement = sqlite.prepare(sql);
      let values: SQLInputValue[] = [];
      const capture = () => captured.push({ sql, values: [...values] });
      return {
        bind(...nextValues: unknown[]) { values = nextValues as SQLInputValue[]; return this; },
        async first<T>() { capture(); return (statement.get(...values) ?? null) as T | null; },
        async all<T>() { capture(); return { results: statement.all(...values) as T[] }; },
        async run() {
          capture();
          const result = statement.run(...values);
          return { meta: { changes: Number(result.changes) } };
        },
      };
    },
    async batch(statements) {
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

function executeMigration(sqlite: DatabaseSync, migration: string) {
  sqlite.exec(migration.replaceAll("--> statement-breakpoint", ""));
}

function insertRanking(
  sqlite: DatabaseSync,
  input: { sourceUrl: string; batchId: string; key?: string; category?: string; scope?: string; sku?: string; month?: string },
) {
  sqlite.prepare(`INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,
       operation_mode,sku_code,product_name,image_url,raw_json,last_import_batch_id)
    VALUES (?,1,?,?,'${input.category ?? "后台类目"}',?,'SKU','POP',?, '后台商品',?,'{}',?)`)
    .run(input.key ?? input.sourceUrl, `${input.month ?? "2026-08"}-01`, `${input.month ?? "2026-08"}-24`,
      input.scope ?? "POP", input.sku ?? input.key ?? "BACKGROUND-SKU", input.sourceUrl, input.batchId);
}

test("0068/runtime are order-independent, preserve cache facts, and 0071 indexes upgrade forward", async () => {
  const [migration0068, migration0071] = await Promise.all([
    readFile(new URL("../drizzle/0069_market_image_cache_background_jobs.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0072_market_image_cache_propagation_indexes.sql", import.meta.url), "utf8"),
  ]);
  assert.doesNotMatch(migration0068, /ALTER TABLE market_image_cache\b/);
  assert.match(migration0068, /CREATE TABLE IF NOT EXISTS market_image_cache_jobs/);
  assert.match(migration0068, /CREATE TABLE IF NOT EXISTS market_image_cache_job_items/);
  assert.match(migration0068, /CREATE TABLE IF NOT EXISTS market_image_cache_claims/);
  assert.match(migration0068, /job_lease_token TEXT NOT NULL/);
  assert.match(migration0068, /discovery_cursor TEXT NOT NULL/);

  const runtimeFirst = new DatabaseSync(":memory:");
  const runtimeFirstDb = sqliteAdapter(runtimeFirst);
  await ensureMarketSchemaCore(runtimeFirstDb);
  runtimeFirst.prepare(`INSERT INTO market_image_cache
    (source_url,status,object_key,content_sha256,mime_type,size_bytes,image_source,attempt_count)
    VALUES ('https://img.example/preserved.jpg','ready','market/preserved.jpg',?,'image/jpeg',8,'test',1)`)
    .run("a".repeat(64));
  executeMigration(runtimeFirst, migration0068);
  assert.equal((runtimeFirst.prepare("SELECT status FROM market_image_cache WHERE source_url='https://img.example/preserved.jpg'").get() as { status: string }).status, "ready");
  const cacheColumns = new Set((runtimeFirst.prepare("PRAGMA table_info(market_image_cache)").all() as Array<{ name: string }>).map((row) => row.name));
  assert.equal(cacheColumns.has("claim_job_id"), false);
  assert.equal(cacheColumns.has("propagated_at"), false);
  runtimeFirst.close();

  const migrationFirst = new DatabaseSync(":memory:");
  const migrationFirstDb = sqliteAdapter(migrationFirst);
  await ensureMarketSchemaCore(migrationFirstDb);
  migrationFirst.exec(`DROP TRIGGER market_image_cache_item_insert_counts;
    DROP TRIGGER market_image_cache_item_status_counts;
    DROP TABLE market_image_cache_claims;
    DROP TABLE market_image_cache_job_items;
    DROP TABLE market_image_cache_jobs;`);
  executeMigration(migrationFirst, migration0068);
  await ensureMarketSchemaCore(migrationFirstDb);
  const claimColumns = new Set((migrationFirst.prepare("PRAGMA table_info(market_image_cache_claims)").all() as Array<{ name: string }>).map((row) => row.name));
  assert.equal(claimColumns.has("job_lease_token"), true);
  assert.equal((migrationFirst.prepare("SELECT COUNT(*) count FROM sqlite_master WHERE type='trigger' AND name LIKE 'market_image_cache_item_%_counts'").get() as { count: number }).count, 2);

  migrationFirst.exec(`DROP INDEX market_price_snapshots_pending_image_url_idx;
    DROP INDEX market_price_snapshots_image_hash_idx;`);
  executeMigration(migrationFirst, migration0071);
  const migratedIndexes = new Set((migrationFirst.prepare("PRAGMA index_list(market_price_snapshots)").all() as Array<{ name: string }>).map((row) => row.name));
  assert.equal(migratedIndexes.has("market_price_snapshots_pending_image_url_idx"), true);
  assert.equal(migratedIndexes.has("market_price_snapshots_image_hash_idx"), true);
  migrationFirst.exec(`DROP INDEX market_price_snapshots_pending_image_url_idx;
    DROP INDEX market_price_snapshots_image_hash_idx;`);
  await ensureMarketSchemaCore(migrationFirstDb);
  const runtimeIndexes = new Set((migrationFirst.prepare("PRAGMA index_list(market_price_snapshots)").all() as Array<{ name: string }>).map((row) => row.name));
  assert.equal(runtimeIndexes.has("market_price_snapshots_pending_image_url_idx"), true);
  assert.equal(runtimeIndexes.has("market_price_snapshots_image_hash_idx"), true);
  migrationFirst.close();
});

test("job creation is O(1), global leasing is exclusive, and expired ownership can be taken over", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const captured: CapturedStatement[] = [];
  const db = sqliteAdapter(sqlite, captured);
  await ensureMarketSchemaCached(db);
  captured.length = 0;
  const first = await createOrResumeMarketImageCacheJob(db, { batchId: "lease-a", requestedBy: "a@test" });
  const duplicate = await createOrResumeMarketImageCacheJob(db, { batchId: "lease-a", requestedBy: "again@test" });
  const second = await createOrResumeMarketImageCacheJob(db, { batchId: "lease-b", requestedBy: "b@test" });
  assert.equal(first.id, duplicate.id);
  assert.notEqual(first.id, second.id);
  assert.doesNotMatch(captured.map((entry) => entry.sql).join("\n"), /COUNT\s*\(|DISTINCT|market_ranking_entries|market_image_cache\s+cache/i);

  const firstLease = await acquireMarketImageCacheJobLease(db, { jobId: first.id });
  assert.ok(firstLease);
  assert.equal(await acquireMarketImageCacheJobLease(db, { jobId: second.id }), null);
  sqlite.prepare("UPDATE market_image_cache_jobs SET lease_expires_at=datetime('now','-1 second') WHERE id=?").run(first.id);
  const secondLease = await acquireMarketImageCacheJobLease(db, { jobId: second.id });
  assert.ok(secondLease);
  assert.equal(secondLease.id, second.id);
  assert.equal(await acquireMarketImageCacheJobLease(db, { jobId: first.id }), null);
  sqlite.close();
});

test("expired claim and legacy fetching recovery are index-driven and capped at 8 rows per scheduled turn", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const captured: CapturedStatement[] = [];
  const db = sqliteAdapter(sqlite, captured);
  await ensureMarketSchemaCached(db);
  const job = await createOrResumeMarketImageCacheJob(db, { batchId: "recovery-batch", requestedBy: "test" });
  const lease = await acquireMarketImageCacheJobLease(db, { jobId: job.id });
  assert.ok(lease);
  const insertItem = sqlite.prepare(`INSERT INTO market_image_cache_job_items
    (job_id,source_url,status,attempt_count) VALUES (?,?,'queued',0)`);
  const insertClaim = sqlite.prepare(`INSERT INTO market_image_cache_claims
    (source_url,job_id,claim_token,job_lease_token,job_epoch,attempt_count,lease_expires_at,updated_at)
    VALUES (?,?,'old-claim','old-lease',0,1,'2000-01-01 00:00:00','2000-01-01 00:00:00')`);
  const insertLegacy = sqlite.prepare(`INSERT INTO market_image_cache
    (source_url,status,attempt_count,updated_at) VALUES (?,'fetching',1,'2000-01-01 00:00:00')`);
  for (let index = 0; index < 200; index += 1) {
    const claimUrl = `https://img.example/claim-${String(index).padStart(3, "0")}.jpg`;
    insertItem.run(job.id, claimUrl);
    insertClaim.run(claimUrl, job.id);
    insertLegacy.run(`https://img.example/legacy-${String(index).padStart(3, "0")}.jpg`);
  }
  captured.length = 0;
  const recovered = await recoverExpiredMarketImageCacheClaims(db, {
    jobId: lease.id, leaseToken: lease.leaseToken, jobEpoch: lease.leaseEpoch,
  });
  assert.equal(recovered, 8);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_image_cache_claims WHERE job_id=?").get(job.id) as { count: number }).count, 192);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_image_cache WHERE source_url LIKE 'https://img.example/claim-%' AND status='failed'").get() as { count: number }).count, 8);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_image_cache WHERE source_url LIKE 'https://img.example/legacy-%' AND status='failed'").get() as { count: number }).count, 8);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_image_cache WHERE source_url LIKE 'https://img.example/legacy-%' AND status='fetching'").get() as { count: number }).count, 192);
  assert.deepEqual((sqlite.prepare("SELECT source_url url FROM market_image_cache WHERE source_url LIKE 'https://img.example/claim-%' ORDER BY source_url").all() as Array<{ url: string }>).map((row) => row.url),
    Array.from({ length: 8 }, (_, index) => `https://img.example/claim-${String(index).padStart(3, "0")}.jpg`));
  assert.deepEqual((sqlite.prepare("SELECT source_url url FROM market_image_cache WHERE source_url LIKE 'https://img.example/legacy-%' AND status='failed' ORDER BY source_url").all() as Array<{ url: string }>).map((row) => row.url),
    Array.from({ length: 8 }, (_, index) => `https://img.example/legacy-${String(index).padStart(3, "0")}.jpg`));

  const recoveryStatements = captured.filter((entry) => /expired_claims|stale_legacy/.test(entry.sql));
  assert.equal(recoveryStatements.length, 4);
  for (const statement of recoveryStatements) {
    const plan = sqlite.prepare(`EXPLAIN QUERY PLAN ${statement.sql}`).all(...statement.values) as Array<{ detail: string }>;
    const details = plan.map((row) => row.detail).join("\n");
    if (statement.sql.includes("expired_claims")) {
      assert.match(details, /market_image_cache_claims_job_expiry_idx/);
      assert.doesNotMatch(details, /SCAN claim\b/i);
    } else {
      assert.match(details, /market_image_cache_fetching_recovery_idx/);
      assert.doesNotMatch(details, /SCAN cache\b/i);
    }
  }
  assert.equal(await recoverExpiredMarketImageCacheClaims(db, {
    jobId: lease.id, leaseToken: lease.leaseToken, jobEpoch: lease.leaseEpoch,
  }), 8);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_image_cache_claims WHERE job_id=?").get(job.id) as { count: number }).count, 184);
  sqlite.close();
});

test("cursor discovery scans at most 64 rows, work reads at most 8, and counters stay incremental", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const captured: CapturedStatement[] = [];
  const db = sqliteAdapter(sqlite, captured);
  await ensureMarketSchemaCached(db);
  for (let index = 0; index < 70; index += 1) {
    insertRanking(sqlite, {
      sourceUrl: `https://img.example/${String(index).padStart(3, "0")}.jpg`,
      batchId: "discovery-batch",
      key: `discovery-${index}`,
      sku: `DISCOVERY-${index}`,
    });
  }
  const started = await createOrResumeMarketImageCacheJob(db, { batchId: "discovery-batch", requestedBy: "test" });
  const lease = await acquireMarketImageCacheJobLease(db, { jobId: started.id });
  assert.ok(lease);
  captured.length = 0;
  const firstPage = await discoverMarketImageCacheJobItems(db, lease);
  assert.deepEqual({ scanned: firstPage.scanned, discovered: firstPage.discovered, complete: firstPage.discoveryComplete, lost: firstPage.lostLease },
    { scanned: 64, discovered: 64, complete: false, lost: false });
  const firstJob = await getMarketImageCacheJob(db, { jobId: started.id });
  assert.equal(firstJob?.total, 64);
  assert.equal(firstJob?.pending, 64);
  assert.equal(firstJob?.discoveryComplete, false);
  assert.equal((await listMarketImageCacheJobItems(db, lease, 99)).length, 8);
  const discoverySql = captured.map((entry) => entry.sql).join("\n");
  assert.doesNotMatch(discoverySql, /COUNT\s*\(|SELECT\s+DISTINCT/i);
  assert.match(discoverySql, /market_entries_batch_image_idx/);
  assert.match(discoverySql, /LIMIT \?/);

  const released = await finishMarketImageCacheJobLease(db, lease);
  assert.equal(released?.status, "queued");
  sqlite.prepare("UPDATE market_image_cache_jobs SET next_run_at=NULL WHERE id=?").run(started.id);
  const continuation = await acquireMarketImageCacheJobLease(db, { jobId: started.id });
  assert.ok(continuation);
  const finalPage = await discoverMarketImageCacheJobItems(db, continuation);
  assert.deepEqual({ scanned: finalPage.scanned, discovered: finalPage.discovered, complete: finalPage.discoveryComplete },
    { scanned: 6, discovered: 6, complete: true });
  const fullyDiscovered = await getMarketImageCacheJob(db, { jobId: started.id });
  assert.equal(fullyDiscovered?.total, 70);
  assert.equal(fullyDiscovered?.pending, 70);

  const sourceUrl = "https://img.example/000.jpg";
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const claim = await claimMarketImageCache(db, {
      jobId: continuation.id, leaseToken: continuation.leaseToken, jobEpoch: continuation.leaseEpoch, sourceUrl,
    });
    assert.equal(claim?.attemptCount, attempt);
    assert.equal(await failMarketImageCacheClaim(db, {
      jobId: continuation.id, leaseToken: continuation.leaseToken, jobEpoch: continuation.leaseEpoch,
      sourceUrl, ...claim!, errorCode: "test_failure", errorMessage: `attempt ${attempt}`,
    }), true);
    const status = (sqlite.prepare("SELECT status FROM market_image_cache_job_items WHERE job_id=? AND source_url=?").get(started.id, sourceUrl) as { status: string }).status;
    assert.equal(status, attempt < 3 ? "queued" : "failed");
  }
  const afterFailures = await getMarketImageCacheJob(db, { jobId: started.id });
  assert.equal(afterFailures?.pending, 69);
  assert.equal(afterFailures?.failed, 1);

  const cursorBeforeResume = fullyDiscovered?.discoveryCursor;
  sqlite.prepare("UPDATE market_image_cache_jobs SET status='failed', failure_count=3, lease_token='', lease_expires_at=NULL WHERE id=?").run(started.id);
  const resumed = await createOrResumeMarketImageCacheJob(db, { batchId: "discovery-batch", requestedBy: "resume@test" });
  assert.equal(resumed.status, "queued");
  assert.equal(resumed.discoveryCursor, cursorBeforeResume);
  assert.equal(resumed.total, 70);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_image_cache_job_items WHERE job_id=?").get(started.id) as { count: number }).count, 70);
  sqlite.close();
});

test("completed duplicate starts remain O(1), while timeout release and stale promises cannot write", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const captured: CapturedStatement[] = [];
  const db = sqliteAdapter(sqlite, captured);
  await ensureMarketSchemaCached(db);
  const empty = await createOrResumeMarketImageCacheJob(db, { batchId: "empty-batch", requestedBy: "test" });
  const emptyLease = await acquireMarketImageCacheJobLease(db, { jobId: empty.id });
  assert.ok(emptyLease);
  assert.equal((await discoverMarketImageCacheJobItems(db, emptyLease)).discoveryComplete, true);
  const completed = await finishMarketImageCacheJobLease(db, emptyLease);
  assert.equal(completed?.status, "completed");
  captured.length = 0;
  const duplicate = await createOrResumeMarketImageCacheJob(db, { batchId: "empty-batch", requestedBy: "again@test" });
  assert.equal(duplicate.id, empty.id);
  assert.equal(duplicate.status, "completed");
  assert.equal(duplicate.discoveryComplete, true);
  assert.doesNotMatch(captured.map((entry) => entry.sql).join("\n"), /DELETE|COUNT\s*\(|DISTINCT|market_ranking_entries/i);

  const globalEmpty = await createOrResumeMarketImageCacheJob(db, { requestedBy: "global@test" });
  const globalEmptyLease = await acquireMarketImageCacheJobLease(db, { jobId: globalEmpty.id });
  assert.ok(globalEmptyLease);
  assert.equal((await discoverMarketImageCacheJobItems(db, globalEmptyLease)).discoveryComplete, true);
  assert.equal((await finishMarketImageCacheJobLease(db, globalEmptyLease))?.status, "completed");
  insertRanking(sqlite, { sourceUrl: "https://img.example/new-after-global-completion.jpg", batchId: "later-batch", key: "later" });
  captured.length = 0;
  const reopenedGlobal = await createOrResumeMarketImageCacheJob(db, { requestedBy: "global-recheck@test" });
  assert.equal(reopenedGlobal.id, globalEmpty.id);
  assert.equal(reopenedGlobal.status, "queued");
  assert.equal(reopenedGlobal.discoveryComplete, false);
  assert.equal(reopenedGlobal.discoveryCursor, "");
  assert.doesNotMatch(captured.map((entry) => entry.sql).join("\n"), /DELETE|COUNT\s*\(|DISTINCT|market_ranking_entries/i);
  const reopenedGlobalLease = await acquireMarketImageCacheJobLease(db, { jobId: reopenedGlobal.id });
  assert.ok(reopenedGlobalLease);
  assert.equal((await discoverMarketImageCacheJobItems(db, reopenedGlobalLease)).discovered, 1);
  assert.equal((await finishMarketImageCacheJobLease(db, reopenedGlobalLease))?.status, "queued");

  insertRanking(sqlite, { sourceUrl: "https://img.example/timeout.jpg", batchId: "timeout-batch", key: "timeout" });
  const timeoutJob = await createOrResumeMarketImageCacheJob(db, { batchId: "timeout-batch", requestedBy: "test" });
  const timeoutLease = await acquireMarketImageCacheJobLease(db, { jobId: timeoutJob.id });
  assert.ok(timeoutLease);
  await discoverMarketImageCacheJobItems(db, timeoutLease);
  const timeoutFence = { jobId: timeoutLease.id, leaseToken: timeoutLease.leaseToken, jobEpoch: timeoutLease.leaseEpoch };
  const claim = await claimMarketImageCache(db, { ...timeoutFence, sourceUrl: "https://img.example/timeout.jpg" });
  assert.equal(claim?.attemptCount, 1);
  assert.equal(await quarantineTimedOutMarketImageCacheJobLease(db, timeoutLease), true);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_image_cache_claims WHERE job_id=?").get(timeoutJob.id) as { count: number }).count, 1);
  assert.equal((sqlite.prepare("SELECT status FROM market_image_cache_job_items WHERE job_id=?").get(timeoutJob.id) as { status: string }).status, "queued");
  assert.deepEqual(await completeMarketImageCacheClaim(db, {
    ...timeoutFence, ...claim!, sourceUrl: "https://img.example/timeout.jpg", objectKey: "late.jpg",
    contentHash: "f".repeat(64), mimeType: "image/jpeg", sizeBytes: 10, imageSource: "test",
  }), { completed: false });
  assert.equal(await heartbeatMarketImageCacheJobLease(db, timeoutLease), false);
  const blockedJob = await createOrResumeMarketImageCacheJob(db, { batchId: "blocked-during-quarantine", requestedBy: "test" });
  assert.equal(await acquireMarketImageCacheJobLease(db, { jobId: blockedJob.id }), null);
  sqlite.prepare("UPDATE market_image_cache_jobs SET lease_expires_at=datetime('now','-1 second'), next_run_at=NULL WHERE id=?").run(timeoutJob.id);
  sqlite.prepare("UPDATE market_image_cache_claims SET lease_expires_at=datetime('now','-1 second') WHERE source_url=?").run("https://img.example/timeout.jpg");
  const retryLease = await acquireMarketImageCacheJobLease(db, { jobId: timeoutJob.id });
  assert.ok(retryLease);
  assert.equal(await recoverExpiredMarketImageCacheClaims(db, {
    jobId: retryLease.id, leaseToken: retryLease.leaseToken, jobEpoch: retryLease.leaseEpoch,
  }), 1);
  const retryClaim = await claimMarketImageCache(db, {
    jobId: retryLease.id, leaseToken: retryLease.leaseToken, jobEpoch: retryLease.leaseEpoch,
    sourceUrl: "https://img.example/timeout.jpg",
  });
  assert.equal(retryClaim?.attemptCount, 2);
  sqlite.prepare("UPDATE market_image_cache_jobs SET lease_expires_at=datetime('now','-1 second') WHERE id=?").run(timeoutJob.id);
  sqlite.prepare("UPDATE market_image_cache_claims SET lease_expires_at=datetime('now','-1 second') WHERE source_url=?").run("https://img.example/timeout.jpg");
  const takeover = await acquireMarketImageCacheJobLease(db, { jobId: timeoutJob.id });
  assert.ok(takeover);
  assert.equal(await recoverExpiredMarketImageCacheClaims(db, {
    jobId: takeover.id, leaseToken: takeover.leaseToken, jobEpoch: takeover.leaseEpoch,
  }), 1);
  assert.deepEqual(await completeMarketImageCacheClaim(db, {
    jobId: retryLease.id, leaseToken: retryLease.leaseToken, jobEpoch: retryLease.leaseEpoch,
    ...retryClaim!, sourceUrl: "https://img.example/timeout.jpg", objectKey: "late-takeover.jpg",
    contentHash: "e".repeat(64), mimeType: "image/jpeg", sizeBytes: 10, imageSource: "test",
  }), { completed: false });
  assert.equal((sqlite.prepare("SELECT status FROM market_image_cache_job_items WHERE job_id=?").get(timeoutJob.id) as { status: string }).status, "queued");
  assert.equal((await finishMarketImageCacheJobLease(db, takeover))?.status, "queued");

  const terminalJob = await createOrResumeMarketImageCacheJob(db, { batchId: "three-timeouts", requestedBy: "test" });
  sqlite.prepare("UPDATE market_image_cache_jobs SET failure_count=3 WHERE id=?").run(terminalJob.id);
  const terminalLease = await acquireMarketImageCacheJobLease(db, { jobId: terminalJob.id });
  assert.ok(terminalLease);
  assert.equal(terminalLease.failureCount, 3);
  assert.equal(await terminateTimedOutMarketImageCacheJobLease(db, terminalLease), true);
  assert.deepEqual({ ...sqlite.prepare("SELECT status, failure_count failureCount, lease_token leaseToken FROM market_image_cache_jobs WHERE id=?").get(terminalJob.id) }, {
    status: "failed", failureCount: 3, leaseToken: "",
  });
  assert.ok(await acquireMarketImageCacheJobLease(db, { jobId: blockedJob.id }));
  sqlite.close();
});

test("direct and legacy propagation preserve identity boundaries and use indexed plans", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const captured: CapturedStatement[] = [];
  const db = sqliteAdapter(sqlite, captured);
  await ensureMarketSchemaCached(db);
  const hashA = "a".repeat(64);
  const hashB = "b".repeat(64);
  const hashC = "c".repeat(64);
  const hashD = "d".repeat(64);
  insertRanking(sqlite, { sourceUrl: "https://img.example/direct.jpg", batchId: "prop-batch", key: "direct", category: "类目A", scope: "POP", sku: "SKU-A", month: "2026-08" });
  insertRanking(sqlite, { sourceUrl: "https://img.example/legacy.jpg", batchId: "prop-batch", key: "legacy", category: "类目B", scope: "自营", sku: "SKU-B", month: "2026-08" });
  insertRanking(sqlite, { sourceUrl: "https://img.example/nonstandard.jpg", batchId: "prop-batch", key: "nonstandard", category: "类目C", scope: "POP", sku: "SKU-C", month: "2026-08" });
  const insertSnapshot = sqlite.prepare(`INSERT INTO market_price_snapshots
    (id,category,scope,sku_code,ranking_dimension,month,image_content_sha256,image_url,
     ai_price_type,confirmed_market_price_cents,confirmation_status,confirmed_at)
    VALUES (?,?,?,?, 'SKU', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)`);
  insertSnapshot.run("direct-target", "类目A", "POP", "SKU-A", "2026-08", "", "https://img.example/direct.jpg", "", null, "missing");
  insertSnapshot.run("direct-standard", "类目A", "POP", "SKU-A", "2026-07", hashA, "", "标准售价", 188_800, "confirmed");
  insertSnapshot.run("legacy-target", "类目B", "自营", "SKU-B", "2026-08", "", "", "", null, "missing");
  insertSnapshot.run("legacy-standard", "类目B", "自营", "SKU-B", "2026-07", hashB, "", "标准售价", 288_800, "confirmed");
  insertSnapshot.run("nonstandard-target", "类目C", "POP", "SKU-C", "2026-08", "", "https://img.example/nonstandard.jpg", "", null, "missing");
  insertSnapshot.run("nonstandard-source", "类目C", "POP", "SKU-C", "2026-07", hashC, "", "成交价", 388_800, "confirmed");
  insertSnapshot.run("cross-category", "其他类目", "POP", "SKU-A", "2026-08", hashA, "", "", null, "missing");
  insertSnapshot.run("cross-scope", "类目A", "自营", "SKU-A", "2026-08", hashA, "", "", null, "missing");
  insertSnapshot.run("cross-sku", "类目A", "POP", "SKU-X", "2026-08", hashA, "", "", null, "missing");
  insertSnapshot.run("cross-hash", "类目A", "POP", "SKU-A", "2026-06", hashD, "", "", null, "missing");

  const job = await createOrResumeMarketImageCacheJob(db, { batchId: "prop-batch", requestedBy: "test" });
  const lease = await acquireMarketImageCacheJobLease(db, { jobId: job.id });
  assert.ok(lease);
  assert.equal((await discoverMarketImageCacheJobItems(db, lease)).discovered, 3);
  const fence = { jobId: lease.id, leaseToken: lease.leaseToken, jobEpoch: lease.leaseEpoch };
  for (const [sourceUrl, contentHash] of [
    ["https://img.example/direct.jpg", hashA],
    ["https://img.example/legacy.jpg", hashB],
    ["https://img.example/nonstandard.jpg", hashC],
  ] as const) {
    const claim = await claimMarketImageCache(db, { ...fence, sourceUrl });
    assert.ok(claim);
    assert.deepEqual(await completeMarketImageCacheClaim(db, {
      ...fence, ...claim, sourceUrl, objectKey: `market/${contentHash}.jpg`, contentHash,
      mimeType: "image/jpeg", sizeBytes: 10, imageSource: "test",
    }), { completed: true });
  }
  captured.length = 0;
  const propagation = await propagateMarketImageCacheBatch(db, {
    ...fence,
    images: [
      { sourceUrl: "https://img.example/direct.jpg", contentHash: hashA },
      { sourceUrl: "https://img.example/legacy.jpg", contentHash: hashB },
      { sourceUrl: "https://img.example/nonstandard.jpg", contentHash: hashC },
    ],
  });
  assert.deepEqual(propagation, { snapshotsUpdated: 3, pricesInherited: 2 });
  const snapshot = (id: string) => ({ ...sqlite.prepare(`SELECT image_content_sha256 hash,image_url imageUrl,
      confirmed_market_price_cents price,confirmation_status status FROM market_price_snapshots WHERE id=?`).get(id) as {
        hash: string; imageUrl: string; price: number | null; status: string;
      } });
  assert.deepEqual(snapshot("direct-target"), { hash: hashA, imageUrl: "https://img.example/direct.jpg", price: 188_800, status: "confirmed" });
  assert.deepEqual(snapshot("legacy-target"), { hash: hashB, imageUrl: "https://img.example/legacy.jpg", price: 288_800, status: "confirmed" });
  assert.deepEqual(snapshot("nonstandard-target"), { hash: hashC, imageUrl: "https://img.example/nonstandard.jpg", price: null, status: "missing" });
  for (const id of ["cross-category", "cross-scope", "cross-sku", "cross-hash"]) assert.equal(snapshot(id).price, null);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_image_cache_job_items WHERE job_id=? AND status='completed'").get(job.id) as { count: number }).count, 3);

  const propagationStatements = captured.filter((entry) => /UPDATE market_price_snapshots AS target/.test(entry.sql));
  assert.equal(propagationStatements.length, 2);
  for (const statement of propagationStatements) {
    assert.ok(statement.values.length <= 8, `parameter count ${statement.values.length} should remain D1-bounded`);
    const plan = sqlite.prepare(`EXPLAIN QUERY PLAN ${statement.sql}`).all(...statement.values) as Array<{ detail: string }>;
    const details = plan.map((row) => row.detail).join("\n");
    assert.doesNotMatch(details, /SCAN (?:target|source)\b/i);
    if (statement.sql.includes("direct_candidates")) {
      assert.match(details, /market_price_snapshots_pending_image_url_idx/);
      assert.match(details, /market_entries_image_url_idx/);
      assert.match(details, /market_price_snapshots_sku_month_uq/);
    } else {
      assert.match(details, /market_price_snapshots_image_hash_idx/);
    }
  }

  sqlite.prepare("UPDATE market_image_cache_job_items SET status='ready' WHERE job_id=? AND source_url=?")
    .run(job.id, "https://img.example/direct.jpg");
  sqlite.prepare("UPDATE market_image_cache_jobs SET lease_expires_at=datetime('now','-1 second') WHERE id=?").run(job.id);
  assert.deepEqual(await propagateMarketImageCacheBatch(db, {
    ...fence, images: [{ sourceUrl: "https://img.example/direct.jpg", contentHash: hashA }],
  }), { snapshotsUpdated: 0, pricesInherited: 0 });
  assert.equal((sqlite.prepare("SELECT status FROM market_image_cache_job_items WHERE job_id=? AND source_url=?").get(job.id, "https://img.example/direct.jpg") as { status: string }).status, "ready");
  sqlite.close();
});

test("API validation rejects ambiguous inputs and HTTP/UI/scheduled paths remain bounded", async () => {
  assert.deepEqual(parseMarketImageCachePostBody({}), { ok: true, value: { batchId: undefined } });
  assert.equal(parseMarketImageCachePostBody(null).ok, false);
  assert.equal(parseMarketImageCachePostBody([]).ok, false);
  assert.equal(parseMarketImageCachePostBody({ batchId: 7 }).ok, false);
  assert.deepEqual(parseMarketImageCachePostBody({ batchId: " batch " }), { ok: true, value: { batchId: "batch" } });
  assert.equal(parseMarketImageCacheGetQuery(new URLSearchParams("jobId=a&batchId=b")).ok, false);
  assert.deepEqual(parseMarketImageCacheGetQuery(new URLSearchParams("jobId=a")), { ok: true, value: { jobId: "a", batchId: undefined } });

  const [route, importService, masterExecute, marketView, imageCache, imageCacheJob, imageCacheState, workerEntry] = await Promise.all([
    readFile(new URL("../app/api/market/images/cache/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/import-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/master/execute/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/market-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/image-cache.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/image-cache-job.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/image-cache-state.ts", import.meta.url), "utf8"),
    readFile(new URL("../worker/index.ts", import.meta.url), "utf8"),
  ]);
  assert.match(route, /readBoundedJsonObject\(request, MARKET_IMAGE_CACHE_BODY_BYTES_MAX\)/);
  assert.match(route, /parseMarketImageCachePostBody/);
  assert.match(route, /parseMarketImageCacheGetQuery/);
  assert.doesNotMatch(route, /runScheduledMarketImageCacheBatch|cacheMarketImages/);
  assert.match(importService, /createOrResumeMarketImageCacheJob/);
  assert.doesNotMatch(importService, /cacheMarketImages|fetchAnnotationImage|SALES_IMPORT_FILES|readMarketImageCacheWorkStats/);
  assert.match(masterExecute, /MARKET_IMPORTS_PATH/);
  assert.match(masterExecute, /prepareDjangoMarketImport/);
  assert.match(masterExecute, /imageCacheJob: imported\.data\.imageCacheJob \?\? null/);
  assert.doesNotMatch(masterExecute, /cacheMarketImages|runScheduledMarketImageCacheBatch|readMarketImageCacheWorkStats|createOrResumeMarketImageCacheJob/);

  const uploadBody = marketView.slice(marketView.indexOf("const upload = async"), marketView.indexOf("return <section className=\"panel market-import-card\""));
  assert.match(uploadBody, /startImageCachePolling\(initialJob, importMessage\)/);
  assert.doesNotMatch(uploadBody, /await pollMarketImageCacheJob/);
  assert.match(marketView, /imageCachePollGenerationRef/);
  assert.match(marketView, /后台扫描\/排队中/);
  assert.match(marketView, /importRequestRef\.current === controller[\s\S]*setBusy\(false\)/);
  assert.doesNotMatch(marketView, /round\s*<\s*50|while\s*\([^)]*pending/);

  const getJobBody = imageCacheJob.slice(imageCacheJob.indexOf("export async function getMarketImageCacheJob"), imageCacheJob.indexOf("export async function createOrResumeMarketImageCacheJob"));
  const createJobBody = imageCacheJob.slice(imageCacheJob.indexOf("export async function createOrResumeMarketImageCacheJob"), imageCacheJob.indexOf("export async function acquireMarketImageCacheJobLease"));
  assert.doesNotMatch(getJobBody, /ensureMarketSchema|market_ranking_entries|COUNT\s*\(|DISTINCT/);
  assert.doesNotMatch(createJobBody, /market_ranking_entries|market_image_cache\s+cache|COUNT\s*\(|DISTINCT|DELETE/);
  assert.doesNotMatch(imageCacheJob, /readMarketImageCacheWorkStats/);
  assert.match(imageCacheJob, /const MAX_DISCOVERY_ROWS = 64/);
  assert.match(imageCacheJob, /const MAX_WORK_ITEMS = 8/);
  assert.match(imageCacheJob, /NOT EXISTS \([\s\S]*blocker\.status='running'/);
  assert.match(imageCache, /const MAX_CACHE_BATCH = 8/);
  assert.match(imageCache, /const CACHE_CONCURRENCY = 4/);
  assert.match(imageCache, /const CACHE_EXTERNAL_DEADLINE_MS = 30_000/);
  assert.match(imageCache, /withinCacheExternalDeadline\(Promise\.all\(workers\)\)/);
  assert.doesNotMatch(imageCache, /readMarketImageCacheWorkStats|SELECT DISTINCT|COUNT\s*\(/);
  assert.doesNotMatch(imageCacheState, /claim_job_id|claim_lease_expires_at|propagated_at/);
  assert.match(imageCacheState, /market_image_cache_claims/);
  assert.match(imageCacheState, /job_lease_token/);
  assert.match(workerEntry, /runDjangoMarketImageCacheBatch\(\{ bucket: input\.marketImageBucket \}\)/);
  assert.ok(workerEntry.indexOf("runDjangoMarketImageCacheBatch({ bucket: input.marketImageBucket })")
    < workerEntry.indexOf("runScheduledDjangoMarketAnnotation()"));
});
