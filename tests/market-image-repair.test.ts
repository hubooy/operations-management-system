import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";

import { applyMarketImageRepairs, listMarketImageRepairCandidates, normalizeJdMarketRepairImageUrl } from "../lib/market/image-repair";
import { ensureMarketSchemaCore, type MarketSchemaDatabase } from "../lib/market/schema-core";
import { cacheRepairedImages, parseJdMarketRepairImageResponse, summarizeJdMarketRepairImageResponse } from "../tools/jd-market-image-repair";

function imageCacheJob(overrides: Record<string, unknown> = {}) {
  return {
    id: "market-image-cache-job-1",
    status: "queued",
    total: 6,
    pending: 4,
    propagationPending: 1,
    cached: 1,
    failed: 0,
    processedCount: 1,
    errorCode: "",
    errorMessage: "",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function sqliteAdapter(sqlite: DatabaseSync): MarketSchemaDatabase {
  return {
    prepare(sql: string) {
      const statement = sqlite.prepare(sql);
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as SQLInputValue[]; return this; },
        async first<T>() { return (statement.get(...values) ?? null) as T | null; },
        async all<T>() { return { results: statement.all(...values) as T[] }; },
        async run() {
          const result = statement.run(...values);
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

test("market image repair lists exact pending identities and materializes a ready historical image", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,sku_code,product_name,image_url,product_url,raw_json,last_import_batch_id)
    VALUES
      ('old',1,'2026-07-01','2026-07-31','净水','POP','SKU','POP','SKU-1','旧商品','https://img10.360buyimg.com/n5/ready.jpg','https://item.jd.com/SKU-1.html','{}','old'),
      ('current',2,'2026-08-12','2026-08-12','净水','POP','SKU','POP','SKU-1','新商品','','https://item.jd.com/SKU-1.html','{}','current'),
      ('foreign',3,'2026-08-12','2026-08-12','净水','自营','SKU','自营','SKU-1','其他范围','','https://item.jd.com/SKU-1.html','{}','foreign');
    INSERT INTO market_price_snapshots
      (id,category,scope,sku_code,ranking_dimension,month,image_url,image_content_sha256,confirmation_status)
    VALUES
      ('snapshot','净水','POP','SKU-1','SKU','2026-08','','','source_table'),
      ('foreign-snapshot','净水','自营','SKU-1','SKU','2026-08','','','source_table');
    INSERT INTO market_image_cache
      (source_url,status,object_key,content_sha256,mime_type,size_bytes,image_source,attempt_count)
    VALUES ('https://img10.360buyimg.com/n5/ready.jpg','ready','market-images/ready.jpg','ready-hash','image/jpeg',10,'n5',1);
  `);

  const candidates = await listMarketImageRepairCandidates(db as never, { pageSize: 20 });
  assert.equal(candidates.pagination.total, 2);
  const target = candidates.items.find((item) => item.scope === "POP");
  assert.equal(target?.reusableImageUrl, "https://img10.360buyimg.com/n5/ready.jpg");
  assert.equal(target?.productUrl, "https://item.jd.com/SKU-1.html");

  const result = await applyMarketImageRepairs(db as never, { repairs: [{
    category: "净水", scope: "POP", rankingDimension: "SKU", skuCode: "SKU-1",
    imageUrl: "https://img10.360buyimg.com/n7/ready.jpg?size=1",
  }] }, { email: "admin@example.com", role: "admin" });
  assert.equal(result.repairCount, 1);
  assert.equal(result.rankingRowsUpdated, 1);
  assert.equal(result.snapshotsUpdated, 1);
  assert.deepEqual({ ...(sqlite.prepare("SELECT image_url imageUrl,image_content_sha256 hash FROM market_price_snapshots WHERE id='snapshot'").get() as Record<string, unknown>) }, {
    imageUrl: "https://img10.360buyimg.com/n5/ready.jpg",
    hash: "ready-hash",
  });
  assert.equal(sqlite.prepare("SELECT image_url FROM market_ranking_entries WHERE natural_key='foreign'").get()?.image_url, "");
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM market_master_audit_logs WHERE action='repair_market_identity_images'").get()?.count, 1);
  sqlite.close();
});

test("market image repair reconnects an orphan pending cache URL and can reset it after a terminal failure", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,sku_code,image_url,raw_json,last_import_batch_id)
    VALUES ('failed',1,'2026-08-12','2026-08-12','净水','POP','SKU','POP','SKU-2','https://img11.360buyimg.com/n5/stale.jpg','{}','current');
    INSERT INTO market_price_snapshots
      (id,category,scope,sku_code,ranking_dimension,month,image_url,image_content_sha256,confirmation_status)
    VALUES ('failed-snapshot','净水','POP','SKU-2','SKU','2026-08','https://img11.360buyimg.com/n5/failed.jpg','','source_table');
    INSERT INTO market_image_cache (source_url,status,attempt_count,error_code,error_message)
    VALUES
      ('https://img11.360buyimg.com/n5/stale.jpg','failed',3,'http_error','old'),
      ('https://img11.360buyimg.com/n5/failed.jpg','pending',0,'','');
  `);
  const candidates = await listMarketImageRepairCandidates(db as never, { pageSize: 20 });
  assert.equal(candidates.pagination.total, 1);
  await applyMarketImageRepairs(db as never, { repairs: [{
    category: "净水", scope: "POP", rankingDimension: "SKU", skuCode: "SKU-2",
    imageUrl: "https://img11.360buyimg.com/n5/failed.jpg",
  }] }, { email: "admin@example.com", role: "admin" });
  assert.deepEqual({ ...(sqlite.prepare("SELECT status,attempt_count attempts,error_code error FROM market_image_cache WHERE source_url='https://img11.360buyimg.com/n5/failed.jpg'").get() as Record<string, unknown>) }, {
    status: "pending", attempts: 0, error: "",
  });
  assert.equal(sqlite.prepare("SELECT image_url FROM market_ranking_entries WHERE natural_key='failed'").get()?.image_url, "https://img11.360buyimg.com/n5/failed.jpg");
  sqlite.prepare("UPDATE market_image_cache SET status='failed',attempt_count=3,error_code='http_error' WHERE source_url='https://img11.360buyimg.com/n5/failed.jpg'").run();
  await applyMarketImageRepairs(db as never, { repairs: [{
    category: "净水", scope: "POP", rankingDimension: "SKU", skuCode: "SKU-2",
    imageUrl: "https://img11.360buyimg.com/n5/failed.jpg",
  }] }, { email: "admin@example.com", role: "admin" });
  assert.equal(sqlite.prepare("SELECT status FROM market_image_cache WHERE source_url='https://img11.360buyimg.com/n5/failed.jpg'").get()?.status, "pending");
  assert.equal(normalizeJdMarketRepairImageUrl("https://evil.example/n5/a.jpg"), "");
  sqlite.close();
});

test("JD image repair accepts keyed image responses and normalizes them to the cacheable n5 source", () => {
  assert.deepEqual([...parseJdMarketRepairImageResponse({ content: { data: {
    "1001": { imgSrc: "//img10.360buyimg.com/n7/jfs/a.jpg?x=1" },
    "1002": { imgSrc: "https://untrusted.example/n5/b.jpg" },
  } } })], [["1001", "https://img10.360buyimg.com/n5/jfs/a.jpg"]]);
  assert.deepEqual(summarizeJdMarketRepairImageResponse({ code: 601, content: { errorCode: "limited", secret: "hidden" } }), {
    rootKeys: ["code", "content"], contentKeys: ["errorCode", "secret"], code: "601", status: "", message: "", success: null,
  });
});

test("JD image repair submits the async cache job once and polls its exact id to completion", async () => {
  const calls: Array<{ url: string; method: string }> = [];
  const responses = [
    jsonResponse({ ok: true, job: imageCacheJob() }, 202),
    jsonResponse({ ok: true, job: imageCacheJob({ status: "running", pending: 2, processedCount: 3 }) }),
    jsonResponse({ ok: true, job: imageCacheJob({ status: "completed", pending: 0, propagationPending: 0, cached: 5, failed: 1, processedCount: 6 }) }),
  ];
  const result = await cacheRepairedImages("http://127.0.0.1:3000", {
    pollIntervalMs: 0,
    maxPolls: 3,
    request: async (input, init) => {
      calls.push({ url: String(input), method: init?.method ?? "GET" });
      assert.equal(init?.signal instanceof AbortSignal, true);
      const response = responses.shift();
      assert.ok(response);
      return response;
    },
  });
  assert.deepEqual(result, { pending: 0, cached: 5, failed: 1, processed: 6 });
  assert.deepEqual(calls, [
    { url: "http://127.0.0.1:3000/api/market/images/cache", method: "POST" },
    { url: "http://127.0.0.1:3000/api/market/images/cache?jobId=market-image-cache-job-1", method: "GET" },
    { url: "http://127.0.0.1:3000/api/market/images/cache?jobId=market-image-cache-job-1", method: "GET" },
  ]);
});

test("JD image repair preserves the legacy synchronous cache result contract", async () => {
  let posts = 0;
  const result = await cacheRepairedImages("http://localhost:3000", {
    pollIntervalMs: 0,
    maxPolls: 1,
    request: async (_input, init) => {
      posts += 1;
      assert.equal(init?.method, "POST");
      return posts === 1
        ? jsonResponse({ result: { pending: 1, cached: 2, failed: 0, processed: 1, cachedThisRun: 1 } })
        : jsonResponse({ result: { pending: 0, cached: 3, failed: 0, processed: 1, cachedThisRun: 1 } });
    },
  });
  assert.equal(posts, 2);
  assert.deepEqual(result, { pending: 0, cached: 3, failed: 0, processed: 1 });
});

test("JD image repair fails closed when an async cache job reaches a failed terminal state", async () => {
  let requests = 0;
  await assert.rejects(
    cacheRepairedImages("http://127.0.0.1:3000", {
      pollIntervalMs: 0,
      maxPolls: 2,
      request: async () => {
        requests += 1;
        return requests === 1
          ? jsonResponse({ job: imageCacheJob() }, 202)
          : jsonResponse({ job: imageCacheJob({ status: "failed", errorCode: "cache_batch_timeout", errorMessage: "图片源连续超时" }) });
      },
    }),
    /市场图片缓存任务失败.*图片源连续超时/,
  );
  assert.equal(requests, 2);
});

test("JD image repair reports bounded cache polling timeouts without resubmitting the job", async () => {
  let posts = 0;
  let gets = 0;
  await assert.rejects(
    cacheRepairedImages("http://127.0.0.1:3000", {
      pollIntervalMs: 0,
      maxPolls: 2,
      request: async (_input, init) => {
        if (init?.method === "POST") {
          posts += 1;
          return jsonResponse({ job: imageCacheJob() }, 202);
        }
        gets += 1;
        return jsonResponse({ job: imageCacheJob({ status: "running" }) });
      },
    }),
    /轮询超时.*market-image-cache-job-1.*后台任务仍会继续运行/,
  );
  assert.equal(posts, 1);
  assert.equal(gets, 2);
});

test("JD image repair aborts an in-flight cache poll with an explicit interruption error", async () => {
  const controller = new AbortController();
  const pending = cacheRepairedImages("http://127.0.0.1:3000", {
    signal: controller.signal,
    pollIntervalMs: 5_000,
    maxPolls: 2,
    request: async () => jsonResponse({ job: imageCacheJob() }, 202),
  });
  setTimeout(() => controller.abort("测试中断"), 5);
  await assert.rejects(pending, (error: unknown) => {
    assert.equal((error as Error).name, "AbortError");
    assert.match((error as Error).message, /轮询已中断：测试中断/);
    return true;
  });
});

test("JD image repair verifies the signed-in header shop instead of ranking-row mall links", async () => {
  const runner = await readFile(new URL("../tools/jd-market-image-repair.ts", import.meta.url), "utf8");
  assert.match(runner, /\.user-info \.shop-name a\[href\*=\"mall\.jd\.com\/index-\"\]/);
  assert.match(runner, /return await fetchJdImages\(frame,/);
});
