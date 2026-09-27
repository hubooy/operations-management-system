import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

test("product asset migration ignores malformed historical JSON and installs bounded upload schema", async () => {
  const migration = await readFile(new URL("../drizzle/0068_netshop_product_assets.sql", import.meta.url), "utf8");
  const runtime = await readFile(new URL("../lib/netshop/database.ts", import.meta.url), "utf8");
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`CREATE TABLE netshop_rows (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    dataset TEXT NOT NULL,
    platform TEXT NOT NULL,
    shop_name TEXT NOT NULL,
    spu_id TEXT NOT NULL,
    snapshot_date TEXT,
    raw_json TEXT NOT NULL
  )`);
  sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,spu_id,snapshot_date,raw_json)
    VALUES (?,?,?,?,?,?,?,?)`)
    .run(1, "jd_product_master", "product_master", "京东", "历史店铺", "1", "2026-08-01", "{malformed");
  sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,spu_id,snapshot_date,raw_json)
    VALUES (?,?,?,?,?,?,?,?)`)
    .run(2, "tmall_product_assets", "spu_assets", "天猫", "测试店铺", "2", "2026-08-01", "{malformed");

  for (const statement of migration.split("--> statement-breakpoint").map((part) => part.trim()).filter(Boolean)) {
    sqlite.exec(statement);
  }

  const indexNames = new Set((sqlite.prepare("PRAGMA index_list('netshop_rows')").all() as Array<{ name: string }>).map((row) => row.name));
  assert.ok(indexNames.has("netshop_rows_product_assets_identity_idx"));
  assert.ok(indexNames.has("netshop_rows_product_assets_hash_idx"));
  assert.ok(sqlite.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='netshop_asset_uploads'").get());
  assert.ok(sqlite.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='netshop_asset_upload_chunks'").get());
  assert.ok(sqlite.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='netshop_asset_upload_results'").get());
  assert.match(migration, /WHERE source = 'tmall_product_assets' AND dataset = 'spu_assets' AND json_valid\(raw_json\)/);
  assert.match(runtime, /WHERE source = 'tmall_product_assets' AND dataset = 'spu_assets' AND json_valid\(raw_json\)/);
  sqlite.close();
});
