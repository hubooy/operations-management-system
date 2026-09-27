import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return { url: "data:text/javascript,export const env={};", shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { getNetshopPromotionItems, getNetshopPromotionOverview } = await import("../lib/netshop/database");
const { ensurePromotionAggregateSchema, rebuildPromotionAggregates } = await import("../lib/netshop/promotion-aggregate");
const { PublicApiError } = await import("../lib/http/api-error");

type TestPlatform = "京东" | "天猫";
type AfterRead = (sql: string) => void | Promise<void>;

function createDatabase() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`CREATE TABLE netshop_rows (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    dataset TEXT NOT NULL,
    platform TEXT NOT NULL,
    shop_name TEXT NOT NULL,
    business_date TEXT,
    snapshot_date TEXT,
    product_code TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    sku_id TEXT NOT NULL DEFAULT '',
    spu_id TEXT NOT NULL DEFAULT '',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    raw_json TEXT NOT NULL DEFAULT '{}',
    last_import_batch_id TEXT NOT NULL DEFAULT ''
  );
  CREATE INDEX netshop_rows_source_dataset_scope_date_idx
    ON netshop_rows (source,dataset,platform,shop_name,business_date);
  CREATE TABLE netshop_import_batches (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT '',
    dataset TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    platform TEXT NOT NULL,
    shop_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
  );
  CREATE TABLE netshop_product_daily_revisions (
    platform TEXT PRIMARY KEY NOT NULL,
    data_version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
  CREATE TABLE netshop_product_daily_scope_revisions (
    platform TEXT NOT NULL,
    shop_name TEXT NOT NULL,
    data_version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (platform,shop_name)
  );`);
  return sqlite;
}

function adapter(sqlite: DatabaseSync, afterRead?: AfterRead) {
  return {
    prepare(sql: string) {
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) {
          values = nextValues as SQLInputValue[];
          return this;
        },
        async first<T>() {
          const result = (sqlite.prepare(sql).get(...values) ?? null) as T | null;
          await afterRead?.(sql);
          return result;
        },
        async all<T>() {
          const result = { results: sqlite.prepare(sql).all(...values) as T[] };
          await afterRead?.(sql);
          return result;
        },
        async run() {
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

function scopeFor(platform: TestPlatform, shopName?: string) {
  return platform === "京东"
    ? {
      source: "jd_promotion" as const,
      dataset: "ad" as const,
      platform,
      shopName: shopName ?? "京东测试店",
      startDate: "2026-08-01",
      endDate: "2026-08-01",
    }
    : {
      source: "tmall_promotion" as const,
      dataset: "promotion_daily" as const,
      platform,
      shopName: shopName ?? "天猫测试店",
      startDate: "2026-08-01",
      endDate: "2026-08-01",
    };
}

function markPlatformReady(sqlite: DatabaseSync, platform: TestPlatform) {
  sqlite.prepare(`INSERT INTO netshop_promotion_aggregate_manifest (platform,ready,completed_at)
    VALUES (?,1,CURRENT_TIMESTAMP)
    ON CONFLICT(platform) DO UPDATE SET ready=1,completed_at=CURRENT_TIMESTAMP`).run(platform);
  sqlite.prepare(`UPDATE netshop_promotion_aggregate_control
    SET maintenance_token='',maintenance_version=0,maintenance_previous_ready=0,
      maintenance_started_at=NULL,updated_at=CURRENT_TIMESTAMP
    WHERE platform=?`).run(platform);
}

async function createReadyFixture(platforms: readonly TestPlatform[]) {
  const sqlite = createDatabase();
  const db = adapter(sqlite);
  await ensurePromotionAggregateSchema(db as never);
  const insert = sqlite.prepare(`INSERT INTO netshop_rows (
    id,source,dataset,platform,shop_name,business_date,product_name,sku_id,spu_id,
    metrics_json,raw_json,last_import_batch_id
  ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`);
  for (const [index, platform] of platforms.entries()) {
    const scope = scopeFor(platform);
    insert.run(
      index + 1,
      scope.source,
      scope.dataset,
      scope.platform,
      scope.shopName,
      scope.startDate,
      `${platform}商品`,
      platform === "京东" ? "SKU-JD" : "",
      platform === "天猫" ? "SPU-TM" : "",
      JSON.stringify({
        spendCents: platform === "京东" ? 100 : 200,
        netTransactionAmountCents: platform === "京东" ? 500 : 800,
        grossTransactionAmountCents: platform === "京东" ? 500 : 800,
        impressions: 100,
        clicks: 10,
        netOrders: 2,
      }),
      "{}",
      `${platform}-batch-1`,
    );
    await rebuildPromotionAggregates(db as never, scope);
    markPlatformReady(sqlite, platform);
  }
  return sqlite;
}

function invalidateRawScope(sqlite: DatabaseSync, platform: TestPlatform, spendCents: number) {
  sqlite.prepare(`UPDATE netshop_rows SET metrics_json = json_set(metrics_json, '$.spendCents', ?)
    WHERE platform = ?`).run(spendCents, platform);
}

function atomicallyRepublishScope(sqlite: DatabaseSync, platform: TestPlatform, spendCents: number) {
  sqlite.exec("BEGIN IMMEDIATE");
  try {
    invalidateRawScope(sqlite, platform, spendCents);
    sqlite.prepare(`UPDATE netshop_promotion_product_daily
      SET spend_cents=?, rebuilt_at=CURRENT_TIMESTAMP
      WHERE platform=? AND business_date='2026-08-01'`).run(spendCents, platform);
    sqlite.prepare(`UPDATE netshop_promotion_shop_daily
      SET spend_cents=?, rebuilt_at=CURRENT_TIMESTAMP
      WHERE platform=? AND business_date='2026-08-01'`).run(spendCents, platform);
    sqlite.prepare(`UPDATE netshop_promotion_aggregate_state
      SET ready=1, rebuilt_at=CURRENT_TIMESTAMP
      WHERE platform=? AND business_date='2026-08-01'`).run(platform);
    sqlite.exec("COMMIT");
  } catch (error) {
    sqlite.exec("ROLLBACK");
    throw error;
  }
}

function publishProductDailyBatch(
  sqlite: DatabaseSync,
  platform: TestPlatform,
  batchId: string,
  transactionAmountCents: number,
  shopName?: string,
) {
  const source = platform === "京东" ? "jd_sku_daily" : "tmall_product_daily";
  const dataset = platform === "京东" ? "sku_daily" : "spu_daily";
  const scope = scopeFor(platform, shopName);
  const nextId = Number(sqlite.prepare("SELECT COALESCE(MAX(id), 0) + 1 AS id FROM netshop_rows").get()!.id);
  sqlite.exec("BEGIN IMMEDIATE");
  try {
    sqlite.prepare(`INSERT INTO netshop_rows (
      id,source,dataset,platform,shop_name,business_date,product_name,sku_id,spu_id,
      metrics_json,raw_json,last_import_batch_id
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      nextId,
      source,
      dataset,
      platform,
      scope.shopName,
      scope.startDate,
      `${platform}商品日`,
      platform === "京东" ? `DAILY-${batchId}` : "",
      platform === "天猫" ? `DAILY-${batchId}` : "",
      JSON.stringify({ transactionAmountCents }),
      "{}",
      batchId,
    );
    sqlite.prepare(`INSERT INTO netshop_import_batches (
      id,source,dataset,status,platform,shop_name,created_at,completed_at
    ) VALUES (?,?,?,'completed',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)`).run(
      batchId,
      source,
      dataset,
      platform,
      scope.shopName,
    );
    sqlite.prepare(`INSERT INTO netshop_product_daily_revisions (platform,data_version,updated_at)
      VALUES (?,1,CURRENT_TIMESTAMP)
      ON CONFLICT(platform) DO UPDATE SET
         data_version=netshop_product_daily_revisions.data_version+1,
         updated_at=CURRENT_TIMESTAMP`).run(platform);
    sqlite.prepare(`INSERT INTO netshop_product_daily_scope_revisions
      (platform,shop_name,data_version,updated_at)
      VALUES (?,?,1,CURRENT_TIMESTAMP)
      ON CONFLICT(platform,shop_name) DO UPDATE SET
        data_version=netshop_product_daily_scope_revisions.data_version+1,
        updated_at=CURRENT_TIMESTAMP`).run(platform, scope.shopName);
    sqlite.exec("COMMIT");
  } catch (error) {
    sqlite.exec("ROLLBACK");
    throw error;
  }
}

function isFenceRead(sql: string) {
  return /manifest\.data_version[\s\S]*JOIN netshop_promotion_aggregate_manifest manifest/.test(sql);
}

function isConcurrentFenceError(error: unknown) {
  return error instanceof PublicApiError
    && error.status === 503
    && error.code === "service_unavailable"
    && /读取期间(?:已失效或更新|已更新)/.test(error.message);
}

test("overview rejects when raw facts invalidate state immediately after the opening fence", async () => {
  const sqlite = await createReadyFixture(["京东"]);
  let fenceReads = 0;
  const db = adapter(sqlite, (sql) => {
    if (isFenceRead(sql) && ++fenceReads === 1) invalidateRawScope(sqlite, "京东", 300);
  });
  await assert.rejects(() => getNetshopPromotionOverview(db as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东"],
  }), isConcurrentFenceError);
  assert.equal(fenceReads, 2);
  assert.equal(sqlite.prepare(`SELECT ready FROM netshop_promotion_aggregate_state
    WHERE platform='京东'`).get()!.ready, 0);
  sqlite.close();
});

test("items rejects a complete atomic republish after the opening fence even though state is ready again", async () => {
  const sqlite = await createReadyFixture(["京东"]);
  let fenceReads = 0;
  const db = adapter(sqlite, (sql) => {
    if (isFenceRead(sql) && ++fenceReads === 1) atomicallyRepublishScope(sqlite, "京东", 300);
  });
  await assert.rejects(() => getNetshopPromotionItems(db as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东"],
  }), isConcurrentFenceError);
  assert.equal(sqlite.prepare(`SELECT ready FROM netshop_promotion_aggregate_state
    WHERE platform='京东'`).get()!.ready, 1);
  assert.equal(sqlite.prepare(`SELECT data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()!.data_version, 2);
  sqlite.close();
});

test("multi-platform overview rejects when either platform changes between aggregate data statements", async () => {
  const sqlite = await createReadyFixture(["京东", "天猫"]);
  let republished = false;
  let fenceReads = 0;
  const db = adapter(sqlite, (sql) => {
    if (isFenceRead(sql)) fenceReads += 1;
    if (!republished && /WITH daily_series[\s\S]*FROM netshop_promotion_shop_daily s/.test(sql)) {
      republished = true;
      atomicallyRepublishScope(sqlite, "天猫", 900);
    }
  });
  await assert.rejects(() => getNetshopPromotionOverview(db as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东", "天猫"],
  }), isConcurrentFenceError);
  assert.equal(republished, true);
  assert.equal(fenceReads, 2);
  assert.deepEqual(sqlite.prepare(`SELECT platform,data_version
    FROM netshop_promotion_aggregate_manifest ORDER BY platform`).all().map((row) => ({ ...row })), [
    { platform: "京东", data_version: 1 },
    { platform: "天猫", data_version: 2 },
  ]);
  sqlite.close();
});

test("a raw change after the closing fence belongs to the next request", async () => {
  const sqlite = await createReadyFixture(["京东"]);
  let fenceReads = 0;
  const first = await getNetshopPromotionOverview(adapter(sqlite, (sql) => {
    if (isFenceRead(sql) && ++fenceReads === 2) invalidateRawScope(sqlite, "京东", 300);
  }) as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东"],
  });
  assert.equal(first.summary.spendCents, 100);
  assert.equal(fenceReads, 2);
  await assert.rejects(() => getNetshopPromotionOverview(adapter(sqlite) as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东"],
  }), (error: unknown) => error instanceof PublicApiError
    && error.status === 503
    && /尚未完成回填或已失效/.test(error.message));
  sqlite.close();
});

test("items and overview share one token and reject a stale cross-route snapshot", async () => {
  const sqlite = await createReadyFixture(["京东"]);
  const db = adapter(sqlite) as never;
  const input = {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东"],
  };
  const items = await getNetshopPromotionItems(db, input);
  assert.match(items.snapshotToken, /^[a-f0-9]{64}$/);
  const overview = await getNetshopPromotionOverview(db, {
    ...input,
    expectedSnapshotToken: items.snapshotToken,
  });
  assert.equal(overview.snapshotToken, items.snapshotToken);

  publishProductDailyBatch(sqlite, "京东", "daily-batch-1", 500);
  await assert.rejects(() => getNetshopPromotionOverview(db, {
    ...input,
    expectedSnapshotToken: items.snapshotToken,
  }), (error: unknown) => error instanceof PublicApiError
    && error.status === 503
    && /商品与概览数据版本已变化/.test(error.message));

  const refreshedItems = await getNetshopPromotionItems(db, input);
  assert.notEqual(refreshedItems.snapshotToken, items.snapshotToken);
  const refreshedOverview = await getNetshopPromotionOverview(db, {
    ...input,
    expectedSnapshotToken: refreshedItems.snapshotToken,
  });
  assert.equal(refreshedOverview.snapshotToken, refreshedItems.snapshotToken);
  assert.equal(refreshedOverview.summary.platformPaymentAmountCents, 500);
  sqlite.close();
});

test("overview closes its fence over product-daily imports as well as promotion aggregates", async () => {
  const sqlite = await createReadyFixture(["京东"]);
  publishProductDailyBatch(sqlite, "京东", "daily-batch-opening", 300);
  let published = false;
  const db = adapter(sqlite, (sql) => {
    if (!published && /FROM netshop_rows r/.test(sql)) {
      published = true;
      publishProductDailyBatch(sqlite, "京东", "daily-batch-concurrent", 200);
    }
  });
  await assert.rejects(() => getNetshopPromotionOverview(db as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东"],
  }), (error: unknown) => error instanceof PublicApiError
    && error.status === 503
    && /依赖数据在读取期间已更新/.test(error.message));
  assert.equal(published, true);
  sqlite.close();
});

test("shop-scoped promotion tokens ignore another shop but fail closed during and after their own rebuild", async () => {
  const sqlite = await createReadyFixture(["京东"]);
  const db = adapter(sqlite) as never;
  const selected = {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东"],
    outlets: [{ platform: "京东", shopName: "京东测试店" }],
  };
  const opening = await getNetshopPromotionItems(db, selected);

  sqlite.prepare(`INSERT INTO netshop_rows (
    id,source,dataset,platform,shop_name,business_date,product_name,sku_id,spu_id,
    metrics_json,raw_json,last_import_batch_id
  ) VALUES (2,'jd_promotion','ad','京东','京东另一店','2026-08-01','另一商品','SKU-OTHER','',
    '{"spendCents":300}','{}','other-batch')`).run();
  await rebuildPromotionAggregates(db, {
    ...scopeFor("京东", "京东另一店"),
  });
  markPlatformReady(sqlite, "京东");
  const afterOtherShop = await getNetshopPromotionOverview(db, {
    ...selected,
    expectedSnapshotToken: opening.snapshotToken,
  });
  assert.equal(afterOtherShop.snapshotToken, opening.snapshotToken);

  await rebuildPromotionAggregates(db, scopeFor("京东"));
  await assert.rejects(() => getNetshopPromotionItems(db, selected), (error: unknown) => error instanceof PublicApiError
    && error.status === 503
    && /尚未完成回填或已失效/.test(error.message));
  markPlatformReady(sqlite, "京东");
  await assert.rejects(() => getNetshopPromotionOverview(db, {
    ...selected,
    expectedSnapshotToken: opening.snapshotToken,
  }), (error: unknown) => error instanceof PublicApiError
    && error.status === 503
    && /商品与概览数据版本已变化/.test(error.message));
  const refreshed = await getNetshopPromotionItems(db, selected);
  assert.notEqual(refreshed.snapshotToken, opening.snapshotToken);
  sqlite.close();
});

test("shop-scoped product-daily revisions invalidate only the exact imported shop", async () => {
  const sqlite = await createReadyFixture(["京东"]);
  const db = adapter(sqlite) as never;
  const selected = {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东"],
    outlets: [{ platform: "京东", shopName: "京东测试店" }],
  };
  const opening = await getNetshopPromotionItems(db, selected);

  publishProductDailyBatch(sqlite, "京东", "daily-other-shop", 900, "京东另一店");
  const afterOtherShop = await getNetshopPromotionOverview(db, {
    ...selected,
    expectedSnapshotToken: opening.snapshotToken,
  });
  assert.equal(afterOtherShop.snapshotToken, opening.snapshotToken);

  publishProductDailyBatch(sqlite, "京东", "daily-selected-shop", 500, "京东测试店");
  await assert.rejects(() => getNetshopPromotionOverview(db, {
    ...selected,
    expectedSnapshotToken: opening.snapshotToken,
  }), (error: unknown) => error instanceof PublicApiError
    && error.status === 503
    && /商品与概览数据版本已变化/.test(error.message));
  const refreshed = await getNetshopPromotionItems(db, selected);
  assert.notEqual(refreshed.snapshotToken, opening.snapshotToken);
  sqlite.close();
});
