import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";

import {
  ensureMarketMasterDatabaseFiltersCacheSchema,
  getCachedMarketMasterDatabaseFilters,
  marketMasterDatabaseFiltersCacheTriggerNames,
  type MarketOverviewResponseCacheDatabase,
} from "../lib/market/overview-response-cache";

class AsyncStatement {
  constructor(
    private readonly statement: ReturnType<DatabaseSync["prepare"]>,
    private readonly values: SQLInputValue[] = [],
  ) {}

  bind(...values: unknown[]) {
    return new AsyncStatement(this.statement, values as SQLInputValue[]);
  }

  async first<T>() {
    return (this.statement.get(...this.values) as T | undefined) ?? null;
  }

  async run() {
    return this.statement.run(...this.values);
  }
}

function asyncDatabase(sqlite: DatabaseSync): MarketOverviewResponseCacheDatabase {
  return {
    prepare: (sql: string) => new AsyncStatement(sqlite.prepare(sql)),
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

function fixture() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE market_ranking_entries (
      id INTEGER PRIMARY KEY, category TEXT NOT NULL, subcategory TEXT NOT NULL,
      sku_code TEXT NOT NULL, product_name TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE market_subcategory_taxonomy (
      id INTEGER PRIMARY KEY, category TEXT NOT NULL, subcategory TEXT NOT NULL,
      status TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE market_image_cache (id INTEGER PRIMARY KEY, status TEXT NOT NULL);
    CREATE TABLE market_price_snapshots (id INTEGER PRIMARY KEY, confirmation_status TEXT NOT NULL);
    CREATE TABLE market_overview_response_cache (
      cache_key TEXT PRIMARY KEY NOT NULL, revision_key TEXT NOT NULL, payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
  `);
  return { sqlite, db: asyncDatabase(sqlite) };
}

const migrationPromise = readFile(
  new URL("../drizzle/0081_market_master_database_filters_cache.sql", import.meta.url),
  "utf8",
);
const journalPromise = readFile(new URL("../drizzle/meta/_journal.json", import.meta.url), "utf8");

function applyMigration(sqlite: DatabaseSync, migration: string) {
  sqlite.exec(migration.replaceAll("--> statement-breakpoint", ""));
}

function revision(sqlite: DatabaseSync) {
  return Number(sqlite.prepare("SELECT source_revision revision FROM market_master_database_filters_cache_state WHERE id=1").get()?.revision ?? 0);
}

test("0081 has one monotonically ordered migration journal entry", async () => {
  const journal = JSON.parse(await journalPromise) as {
    entries: Array<{ idx: number; version: string; when: number; tag: string; breakpoints: boolean }>;
  };
  const entries = journal.entries.filter((entry) => entry.idx === 80 || entry.tag === "0081_market_master_database_filters_cache");
  assert.deepEqual(entries, [{
    idx: 80,
    version: "6",
    when: 1787630400000,
    tag: "0081_market_master_database_filters_cache",
    breakpoints: true,
  }]);
  const previous = journal.entries.find((entry) => entry.idx === 79);
  assert.ok(previous && entries[0]!.when > previous.when);
});

test("0080 and runtime setup are idempotent in both installation orders", async (context) => {
  const migration = await migrationPromise;
  const installedTriggerSql: string[][] = [];
  for (const order of ["migration-first", "runtime-first"] as const) {
    await context.test(order, async () => {
      const { sqlite, db } = fixture();
      if (order === "migration-first") applyMigration(sqlite, migration);
      await ensureMarketMasterDatabaseFiltersCacheSchema(db);
      if (order === "runtime-first") applyMigration(sqlite, migration);
      applyMigration(sqlite, migration);
      await ensureMarketMasterDatabaseFiltersCacheSchema(db);

      assert.equal(sqlite.prepare(`SELECT COUNT(*) count FROM sqlite_master
        WHERE type='trigger' AND name LIKE 'market_master_filters_v1_%'`).get()?.count,
      marketMasterDatabaseFiltersCacheTriggerNames.length);
      assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM market_master_database_filters_cache_state").get()?.count, 1);
      installedTriggerSql.push(sqlite.prepare(`SELECT sql FROM sqlite_master
        WHERE type='trigger' AND name LIKE 'market_master_filters_v1_%' ORDER BY name`).all()
        .map((row) => String(row.sql ?? "").replaceAll("`", "").replace(/\s+/g, " ").trim().toLowerCase()));
      sqlite.close();
    });
  }
  assert.deepEqual(installedTriggerSql[0], installedTriggerSql[1], "migration and runtime must install equivalent trigger DDL");
});

test("master filter revision tracks every real dependency and ignores image, price, and unrelated updates", async () => {
  const { sqlite, db } = fixture();
  await ensureMarketMasterDatabaseFiltersCacheSchema(db);
  let current = revision(sqlite);
  const changed = () => { const next = revision(sqlite); assert.equal(next, current + 1); current = next; };
  const unchanged = () => assert.equal(revision(sqlite), current);

  sqlite.exec("INSERT INTO market_ranking_entries VALUES (1,'A','A1','SKU-1','one')"); changed();
  sqlite.exec("UPDATE market_ranking_entries SET product_name='two' WHERE id=1"); unchanged();
  sqlite.exec("UPDATE market_ranking_entries SET category=category,subcategory=subcategory,sku_code=sku_code WHERE id=1"); unchanged();
  sqlite.exec("UPDATE market_ranking_entries SET subcategory='A2' WHERE id=1"); changed();
  sqlite.exec("DELETE FROM market_ranking_entries WHERE id=1"); changed();

  sqlite.exec("INSERT INTO market_subcategory_taxonomy VALUES (1,'A','A1','active',0)"); changed();
  sqlite.exec("UPDATE market_subcategory_taxonomy SET sort_order=10 WHERE id=1"); unchanged();
  sqlite.exec("UPDATE market_subcategory_taxonomy SET status=status,category=category,subcategory=subcategory WHERE id=1"); unchanged();
  sqlite.exec("UPDATE market_subcategory_taxonomy SET status='disabled' WHERE id=1"); changed();
  sqlite.exec("DELETE FROM market_subcategory_taxonomy WHERE id=1"); changed();

  sqlite.exec("INSERT INTO market_image_cache VALUES (1,'ready'); UPDATE market_image_cache SET status='failed' WHERE id=1"); unchanged();
  sqlite.exec("INSERT INTO market_price_snapshots VALUES (1,'missing'); UPDATE market_price_snapshots SET confirmation_status='confirmed' WHERE id=1"); unchanged();
  sqlite.close();
});

test("master database filters cache is bounded, canonical, single-flight, and revision fenced", async () => {
  const { sqlite, db } = fixture();
  const validate = (value: unknown) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
  let loads = 0;
  const load = async () => ({ categories: [{ value: "A", count: ++loads }], subcategories: [] });

  const first = await getCachedMarketMasterDatabaseFilters(db, ["B", "A", "A"], load, validate);
  const hit = await getCachedMarketMasterDatabaseFilters(db, ["A", "B"], load, validate);
  assert.deepEqual(hit, first);
  assert.equal(loads, 1);
  const stored = sqlite.prepare("SELECT LENGTH(cache_key) key_length,LENGTH(payload_json) payload_length FROM market_overview_response_cache").get();
  assert.equal(stored?.key_length, 64);
  assert.ok(Number(stored?.payload_length) < 64 * 1024);

  sqlite.exec("INSERT INTO market_ranking_entries VALUES (1,'A','A1','SKU-1','one')");
  const afterRevision = await getCachedMarketMasterDatabaseFilters(db, ["A", "B"], load, validate);
  assert.notDeepEqual(afterRevision, first);
  assert.equal(loads, 2);

  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let concurrentLoads = 0;
  const concurrentLoad = async () => { concurrentLoads += 1; await gate; return { categories: [], subcategories: [] }; };
  const left = getCachedMarketMasterDatabaseFilters(db, ["single-flight"], concurrentLoad, validate);
  const right = getCachedMarketMasterDatabaseFilters(db, ["single-flight"], concurrentLoad, validate);
  await new Promise<void>((resolve) => setImmediate(resolve));
  release?.();
  assert.deepEqual(await left, await right);
  assert.equal(concurrentLoads, 1);

  let fencedLoads = 0;
  const fenced = await getCachedMarketMasterDatabaseFilters(db, ["revision-fence"], async () => {
    fencedLoads += 1;
    if (fencedLoads === 1) sqlite.exec("INSERT INTO market_ranking_entries VALUES (2,'A','A1','SKU-2','two')");
    return { categories: [{ value: "A", count: fencedLoads }], subcategories: [] };
  }, validate);
  assert.equal(fencedLoads, 2);
  assert.equal((fenced as { categories: Array<{ count: number }> }).categories[0]?.count, 2);
  sqlite.close();
});
