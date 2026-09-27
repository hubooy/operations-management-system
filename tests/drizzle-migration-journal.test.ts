import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

type JournalEntry = {
  idx: number;
  version: string;
  when: number;
  tag: string;
  breakpoints: boolean;
};

type Journal = {
  version: string;
  dialect: string;
  entries: JournalEntry[];
};

const operatorOnlyMigrations = new Set([
  "0092_sales_domain_retirement",
  "0093_finance_write_authority",
  "0094_netshop_write_authority",
  "0095_market_netshop_projection",
  "0096_netshop_domain_retirement",
  "0097_market_write_authority",
  "0098_market_domain_retirement",
  "0099_product_write_authority",
  "0100_product_domain_retirement",
  "0101_inventory_write_authority",
  "0102_inventory_domain_retirement",
  "0103_workflow_launch_write_authority",
  "0104_workflow_launch_domain_retirement",
  "0105_workflow_operations_write_authority",
  "0106_workflow_operations_domain_retirement",
  "0107_customer_service_write_authority",
  "0108_customer_service_domain_retirement",
  "0109_erp_reference_write_authority",
  "0110_erp_reference_domain_retirement",
  "0111_access_control_write_authority",
  "0112_access_control_domain_retirement",
  "0113_ai_write_authority",
  "0114_ai_domain_retirement",
]);

test("Drizzle journal registers normal migrations and excludes operator-only post-cutover DDL", async () => {
  const migrationDirectory = new URL("../drizzle/", import.meta.url);
  const [fileNames, journalText] = await Promise.all([
    readdir(migrationDirectory),
    readFile(new URL("meta/_journal.json", migrationDirectory), "utf8"),
  ]);
  const sqlTags = fileNames
    .filter((name) => name.endsWith(".sql"))
    .sort()
    .map((name) => name.slice(0, -4))
    .filter((tag) => !operatorOnlyMigrations.has(tag));
  const journal = JSON.parse(journalText) as Journal;

  assert.equal(journal.dialect, "sqlite");
  assert.equal(fileNames.includes("0092_sales_domain_retirement.sql"), true);
  assert.equal(journal.entries.some((entry) => entry.tag === "0092_sales_domain_retirement"), false);
  assert.equal(fileNames.includes("0093_finance_write_authority.sql"), true);
  assert.equal(journal.entries.some((entry) => entry.tag === "0093_finance_write_authority"), false);
  for (const tag of [
    "0094_netshop_write_authority",
    "0095_market_netshop_projection",
    "0096_netshop_domain_retirement",
    "0097_market_write_authority",
    "0098_market_domain_retirement",
    "0099_product_write_authority",
    "0100_product_domain_retirement",
    "0101_inventory_write_authority",
    "0102_inventory_domain_retirement",
    "0103_workflow_launch_write_authority",
    "0104_workflow_launch_domain_retirement",
    "0105_workflow_operations_write_authority",
    "0106_workflow_operations_domain_retirement",
    "0107_customer_service_write_authority",
    "0108_customer_service_domain_retirement",
    "0109_erp_reference_write_authority",
    "0110_erp_reference_domain_retirement",
    "0111_access_control_write_authority",
    "0112_access_control_domain_retirement",
  "0113_ai_write_authority",
  "0114_ai_domain_retirement",
  ]) {
    assert.equal(fileNames.includes(`${tag}.sql`), true);
    assert.equal(journal.entries.some((entry) => entry.tag === tag), false);
  }
  assert.deepEqual(journal.entries.map((entry) => entry.idx), sqlTags.map((_, index) => index));
  assert.deepEqual(journal.entries.map((entry) => entry.tag), sqlTags);
  assert.equal(new Set(journal.entries.map((entry) => entry.when)).size, journal.entries.length);
  for (let index = 1; index < journal.entries.length; index += 1) {
    assert.ok(journal.entries[index]!.when > journal.entries[index - 1]!.when);
  }

  const publishedBoundary = journal.entries.find((entry) => entry.tag === "0066_market_annotation_runnable_work");
  assert.ok(publishedBoundary);
  assert.deepEqual(
    publishedBoundary,
    {
      idx: 65,
      version: "6",
      when: 1787418000000,
      tag: "0066_market_annotation_runnable_work",
      breakpoints: true,
    },
    "已发布的 0066 migration identity 不得被后插迁移重排",
  );
  const promotionBase = journal.entries.find((entry) => entry.tag === "0067_netshop_promotion_daily_aggregates");
  const promotionDependents = [
    "0070_netshop_promotion_aggregate_manifest",
    "0071_netshop_promotion_snapshot_fence",
    "0074_netshop_promotion_maintenance_fence",
  ].map((tag) => journal.entries.find((entry) => entry.tag === tag));
  assert.ok(promotionBase && promotionBase.when > publishedBoundary.when);
  assert.equal(promotionDependents.every((entry) => entry && entry.when > promotionBase.when), true);
});

test("an already-published 0066 database upgrades through every forward migration", async () => {
  const migrationDirectory = new URL("../drizzle/", import.meta.url);
  const fileNames = (await readdir(migrationDirectory))
    .filter((name) => name.endsWith(".sql") && !operatorOnlyMigrations.has(name.slice(0, -4)))
    .sort();
  assert.equal(fileNames[65], "0066_market_annotation_runnable_work.sql");
  assert.equal(fileNames[66], "0067_netshop_promotion_daily_aggregates.sql");

  const sqlite = new DatabaseSync(":memory:");
  try {
    for (const fileName of fileNames) {
      const migration = await readFile(new URL(fileName, migrationDirectory), "utf8");
      for (const statement of migration.split("--> statement-breakpoint").map((value) => value.trim()).filter(Boolean)) {
        sqlite.exec(statement);
      }
      if (fileName === "0066_market_annotation_runnable_work.sql") {
        assert.ok(sqlite.prepare("SELECT 1 FROM sqlite_master WHERE name = 'market_annotation_jobs_active_work_uq'").get());
      }
    }

    for (const name of [
      "netshop_promotion_product_daily",
      "netshop_promotion_aggregate_state",
      "netshop_asset_uploads",
      "market_master_database_filters_cache_state",
    ]) {
      assert.ok(sqlite.prepare("SELECT 1 FROM sqlite_master WHERE name = ?").get(name), `${name} 应在前向升级后存在`);
    }
    const filtersTriggers = sqlite.prepare(
      "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'market_master_filters_v1_%'",
    ).get() as { count: number };
    assert.equal(filtersTriggers.count, 6);
  } finally {
    sqlite.close();
  }
});
