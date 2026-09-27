import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import { SHARED_IMPORT_RETIREMENT_GUARDS } from "../tools/sales-d1-retirement";

const retiredTables = [
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

const retiredRuntimeModules = [
  "lib/sales/database.ts",
  "lib/sales/summary.ts",
  "lib/sales/category-analysis.ts",
  "lib/sales/category-resolution.ts",
  "lib/sales/product-query.ts",
  "lib/sales/period.ts",
  "lib/sales/overview-cache-schema.ts",
  "lib/sales/overview-response-cache.ts",
  "lib/sales/projection-outbox.ts",
] as const;

async function migration(name: string) {
  return (await readFile(new URL(`../drizzle/${name}.sql`, import.meta.url), "utf8"))
    .replaceAll("--> statement-breakpoint", "");
}

function tableExists(sqlite: DatabaseSync, name: string) {
  return Boolean(sqlite.prepare(
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
  ).get(name));
}

function viewExists(sqlite: DatabaseSync, name: string) {
  return Boolean(sqlite.prepare(
    "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?",
  ).get(name));
}

function triggerExists(sqlite: DatabaseSync, name: string) {
  return Boolean(sqlite.prepare(
    "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
  ).get(name));
}

async function typescriptFiles(directory: URL): Promise<URL[]> {
  const files: URL[] = [];
  const entries = await readdir(directory, { withFileTypes: true });
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const child = new URL(entry.isDirectory() ? `${entry.name}/` : entry.name, directory);
    if (entry.isDirectory()) files.push(...await typescriptFiles(child));
    else if (entry.isFile() && /\.tsx?$/.test(entry.name)) files.push(child);
  }
  return files;
}

async function database() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE sales_order_lines (id INTEGER PRIMARY KEY, product_code TEXT NOT NULL DEFAULT '');
    CREATE TABLE sales_import_batches (id TEXT PRIMARY KEY, status TEXT NOT NULL);
    CREATE TABLE sales_import_uploads (
      id TEXT PRIMARY KEY, status TEXT NOT NULL, expires_at TEXT NOT NULL
    );
    CREATE TABLE sales_import_upload_chunks (
      upload_id TEXT, chunk_index INTEGER, object_key TEXT NOT NULL
    );
    CREATE TABLE sales_overview_cache_state (
      id INTEGER PRIMARY KEY, sales_revision INTEGER NOT NULL, erp_product_revision INTEGER NOT NULL
    );
    INSERT INTO sales_overview_cache_state VALUES (1, 8, 5);
    CREATE TABLE sales_overview_response_cache (cache_key TEXT PRIMARY KEY, payload_json TEXT);
    CREATE TABLE sales_projection_source_state (
      id INTEGER PRIMARY KEY, source_epoch TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO sales_projection_source_state (id, source_epoch) VALUES (1, 'legacy-sales-epoch');
    CREATE TABLE sales_projection_outbox (event_sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL);

    CREATE TABLE import_content_fingerprints (
      sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'completed'
    );
    CREATE TABLE import_content_attempts (
      sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL, outcome TEXT NOT NULL
    );
    CREATE TABLE import_scope_heads (
      domain TEXT NOT NULL, scope_key TEXT NOT NULL, status TEXT NOT NULL,
      PRIMARY KEY (domain, scope_key)
    );

    CREATE TABLE market_monthly_summary_cache_state (
      id INTEGER PRIMARY KEY, source_revision INTEGER NOT NULL, status TEXT NOT NULL
    );
    INSERT INTO market_monthly_summary_cache_state VALUES (1, 1, 'ready');
    CREATE TABLE market_monthly_summary_dirty_products (
      product_code TEXT PRIMARY KEY, dirty_revision INTEGER NOT NULL
    );
    CREATE TRIGGER market_monthly_summary_sales_insert
      AFTER INSERT ON sales_order_lines BEGIN SELECT 1; END;
    CREATE TRIGGER market_monthly_summary_sales_update
      AFTER UPDATE ON sales_order_lines BEGIN SELECT 1; END;
    CREATE TRIGGER market_monthly_summary_sales_delete
      AFTER DELETE ON sales_order_lines BEGIN SELECT 1; END;

    CREATE TABLE erp_reference_projection_source_state (id INTEGER PRIMARY KEY, source_epoch TEXT NOT NULL);
    CREATE TABLE erp_product_projection_state (id INTEGER PRIMARY KEY, erp_revision INTEGER NOT NULL);
    CREATE TABLE erp_reference_projection_outbox (event_sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL);
    CREATE TRIGGER erp_reference_projection_outbox_no_update
      BEFORE UPDATE ON erp_reference_projection_outbox BEGIN SELECT RAISE(ABORT, 'erp append-only'); END;
    CREATE TABLE erp_product_master (product_code TEXT PRIMARY KEY);
    CREATE TABLE inventory_stock_lines (id INTEGER PRIMARY KEY, product_code TEXT NOT NULL);
  `);
  sqlite.exec(await migration("0090_sales_write_authority"));
  return sqlite;
}

function seedRecords(sqlite: DatabaseSync) {
  sqlite.exec(`
    INSERT INTO sales_order_lines (id, product_code) VALUES (1, 'SALE-1');
    INSERT INTO sales_import_batches (id, status) VALUES ('sales-completed', 'completed');
    INSERT INTO sales_import_uploads (id, status, expires_at)
      VALUES ('sales-expired', 'ready', '2000-01-01T00:00:00Z');
    INSERT INTO sales_overview_response_cache (cache_key, payload_json) VALUES ('sales-cache', '{}');
    INSERT INTO sales_projection_outbox (event_sequence, domain) VALUES (1, 'sales');

    INSERT INTO import_content_fingerprints (sequence, domain, status)
      VALUES (1, 'sales', 'completed'), (2, 'inventory', 'completed');
    INSERT INTO import_content_attempts (sequence, domain, outcome)
      VALUES (1, 'sales', 'completed'), (2, 'inventory', 'completed');
    INSERT INTO import_scope_heads (domain, scope_key, status)
      VALUES ('sales', 'sales-scope', 'ready'), ('inventory', 'inventory-scope', 'ready');

    INSERT INTO erp_reference_projection_source_state VALUES (1, 'erp-epoch');
    INSERT INTO erp_product_projection_state VALUES (1, 5);
    INSERT INTO erp_reference_projection_outbox VALUES (1, 'erp');
    INSERT INTO erp_product_master VALUES ('ERP-1');
    INSERT INTO inventory_stock_lines VALUES (1, 'ERP-1');
  `);
}

function activatePostgresql(sqlite: DatabaseSync) {
  sqlite.exec(`
    UPDATE sales_write_authority
    SET owner = 'pending', epoch = 2, cutover_id = 'retirement-test', updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
    UPDATE sales_write_authority
    SET owner = 'postgresql', epoch = 3, cutover_id = 'retirement-test', updated_at = CURRENT_TIMESTAMP
    WHERE id = 1;
  `);
}

async function installApprovedRetirementTicket(sqlite: DatabaseSync) {
  const raw = await readFile(new URL("../drizzle/0092_sales_domain_retirement.sql", import.meta.url), "utf8");
  const statements = raw.split("--> statement-breakpoint").map((value) => value.trim()).filter(Boolean);
  assert.ok(statements.length > 4);
  for (const statement of statements.slice(0, 4)) sqlite.exec(statement);
  sqlite.prepare(`
    INSERT INTO domain_retirement_receipts (
      domain, version, status, cutover_id, plan_id, attestation_sha256,
      smoke_receipt_sha256, preflight_evidence_sha256, migration_sha256,
      audit_id, preserved_evidence_sha256, created_at, completed_at
    ) VALUES ('sales', 'sales-domain-retirement-receipt-v1', 'approved', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
  `).run(
    "retirement-test",
    "1".repeat(64),
    "2".repeat(64),
    "3".repeat(64),
    "4".repeat(64),
    "5".repeat(64),
    "6".repeat(64),
    "7".repeat(64),
    "2026-08-28T15:30:00.000Z",
  );
}

test("legacy D1 sales readers are absent and active sales consumers use Django contracts", async () => {
  for (const relativePath of retiredRuntimeModules) {
    await assert.rejects(
      readFile(new URL(`../${relativePath}`, import.meta.url), "utf8"),
      (error: unknown) => (error as { code?: string }).code === "ENOENT",
      relativePath,
    );
  }

  for (const relativePath of [
    "lib/customer-service/database.ts",
    "lib/finance/database.ts",
    "lib/inventory/overview.ts",
    "lib/products/summary.ts",
    "lib/market/ai-tools.ts",
    "lib/search/global-search.ts",
    "lib/ai/operations-tools.ts",
    "lib/sales/category-ai-tool.ts",
  ]) {
    const source = await readFile(new URL(`../${relativePath}`, import.meta.url), "utf8");
    assert.doesNotMatch(
      source,
      /sales_order_lines|sales_import_batches|ensureSalesSchema|getSalesDatabase|from ["']@\/lib\/sales\/(?:database|summary|category-analysis|product-query|overview-response-cache|projection-outbox)["']/,
      relativePath,
    );
  }

  for (const relativePath of [
    "app/api/sales/summary/route.ts",
    "app/api/sales/category-analysis/route.ts",
    "app/api/sales/category-analysis/detail/route.ts",
  ]) {
    const source = await readFile(new URL(`../${relativePath}`, import.meta.url), "utf8");
    assert.match(source, /routeDjangoSalesReadRequest/, relativePath);
    assert.doesNotMatch(source, /legacy|shadow|getSalesDatabase|ensureSalesSchema/, relativePath);
  }

  for (const relativePath of [
    "lib/sales/read-contract.ts",
    "lib/sales/import-contract.ts",
    "lib/sales/shop-identity.ts",
  ]) {
    const source = await readFile(new URL(`../${relativePath}`, import.meta.url), "utf8");
    assert.ok(source.length > 0, `${relativePath} remains an active cross-runtime contract`);
  }
});

test("sales routes and domain modules contain no direct D1 binding", async () => {
  const roots = [
    new URL("../app/api/imports/sales/", import.meta.url),
    new URL("../app/api/sales/", import.meta.url),
    new URL("../lib/sales/", import.meta.url),
  ];
  const forbiddenBinding = /getD1Database|\bD1Database\b|getCloudflareContext|(?:\.|\b)env\.DB\b|from\s+["'][^"']*(?:database\/d1|d1-database)[^"']*["']/;

  for (const root of roots) {
    for (const file of await typescriptFiles(root)) {
      const source = await readFile(file, "utf8");
      assert.doesNotMatch(source, forbiddenBinding, file.pathname);
    }
  }
});

test("all app and lib TypeScript consumers contain no retired sales SQL or runtime imports", async () => {
  const forbiddenLegacySales = /sales_order_lines|sales_import_batches|@\/lib\/sales\/(?:database|summary|category-analysis|category-resolution|product-query|overview-response-cache|projection-outbox)(?:["'/]|$)/;
  for (const root of [
    new URL("../app/", import.meta.url),
    new URL("../lib/", import.meta.url),
  ]) {
    for (const file of await typescriptFiles(root)) {
      const source = await readFile(file, "utf8");
      // This exact DTO validates the owning PostgreSQL dataset label. It does
      // not query the retired D1 table. Exempt only its two fixed metadata
      // expressions; SQL strings and legacy imports in this file still fail.
      const checked = file.pathname.endsWith("/lib/ai/business-sales-options.ts")
        ? source.replaceAll('sourceDataset: "sales_order_lines"', 'sourceDataset: "owning_dataset"')
          .replaceAll('raw.sourceDataset === "sales_order_lines"', 'raw.sourceDataset === "owning_dataset"')
        : source;
      assert.doesNotMatch(checked, forbiddenLegacySales, file.pathname);
    }
  }
});

test("0092 refuses retirement before PostgreSQL is authoritative without changing sales data or guards", async () => {
  const sqlite = await database();
  try {
    seedRecords(sqlite);
    const sql = await migration("0092_sales_domain_retirement");

    assert.throws(() => sqlite.exec(sql), /integer overflow/);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM sales_order_lines").get()?.count, 1);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_content_fingerprints WHERE domain='sales'").get()?.count, 1);
    assert.equal(sqlite.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "d1");
    assert.equal(triggerExists(sqlite, "sales_authority_order_lines_insert"), true);
    assert.equal(triggerExists(sqlite, "market_monthly_summary_sales_insert"), true);
  } finally {
    sqlite.close();
  }
});

test("0092 preserves expired-upload R2 object keys until controlled cleanup has completed", async () => {
  const sqlite = await database();
  try {
    sqlite.exec(`
      INSERT INTO sales_import_uploads (id, status, expires_at)
      VALUES ('expired-with-object', 'ready', '2000-01-01T00:00:00Z');
      INSERT INTO sales_import_upload_chunks (upload_id, chunk_index, object_key)
      VALUES ('expired-with-object', 0, 'sales/expired-with-object/0');
    `);
    activatePostgresql(sqlite);
    await installApprovedRetirementTicket(sqlite);

    const sql = await migration("0092_sales_domain_retirement");
    assert.throws(() => sqlite.exec(sql), /integer overflow/);
    assert.deepEqual(
      { ...sqlite.prepare("SELECT upload_id uploadId, object_key objectKey FROM sales_import_upload_chunks").get() },
      { uploadId: "expired-with-object", objectKey: "sales/expired-with-object/0" },
    );
    assert.equal(tableExists(sqlite, "sales_import_upload_chunks"), true);
  } finally {
    sqlite.close();
  }
});

test("0092 cannot retire a quiescent PostgreSQL-terminal D1 without the operator ticket", async () => {
  const sqlite = await database();
  try {
    seedRecords(sqlite);
    activatePostgresql(sqlite);
    const sql = await migration("0092_sales_domain_retirement");
    assert.throws(() => sqlite.exec(sql), /integer overflow/);
    assert.equal(tableExists(sqlite, "sales_order_lines"), true);
    assert.equal(
      sqlite.prepare("SELECT COUNT(*) count FROM domain_retirement_receipts WHERE domain='sales'").get()?.count,
      0,
    );
  } finally {
    sqlite.close();
  }
});

test("the standard journal stops before live DDL and explicit pristine bootstrap installs empty tombstones", async () => {
  const sqlite = new DatabaseSync(":memory:");
  try {
    const migrationDirectory = new URL("../drizzle/", import.meta.url);
    const journal = JSON.parse(await readFile(
      new URL("meta/_journal.json", migrationDirectory),
      "utf8",
    )) as { entries: Array<{ tag: string }> };
    assert.equal(journal.entries.some((entry) => entry.tag === "0092_sales_domain_retirement"), false);
    for (const { tag } of journal.entries) {
      sqlite.exec(await readFile(new URL(`${tag}.sql`, migrationDirectory), "utf8"));
    }
    assert.equal(tableExists(sqlite, "sales_order_lines"), true);
    assert.equal(tableExists(sqlite, "sales_write_authority"), true);

    // New databases use the operator-only file as an explicit bootstrap step;
    // the pristine branch requires zero sales facts/audits and no fake cutover.
    sqlite.exec(await readFile(
      new URL("0092_sales_domain_retirement.sql", migrationDirectory),
      "utf8",
    ));
    for (const table of retiredTables) {
      assert.equal(tableExists(sqlite, table), false, table);
      assert.equal(viewExists(sqlite, table), true, table);
      assert.equal(sqlite.prepare(`SELECT COUNT(*) count FROM "${table}"`).get()?.count, 0, table);
    }
    assert.equal(tableExists(sqlite, "inventory_stock_lines"), true);
    assert.equal(tableExists(sqlite, "erp_product_master"), true);
    assert.equal(
      sqlite.prepare("SELECT COUNT(*) count FROM domain_retirement_receipts WHERE domain='sales'").get()?.count,
      0,
    );
  } finally {
    sqlite.close();
  }
});

test("default D1 authority can retire only when every sales-owned and sales-audit state is pristine", async (t) => {
  const blockers = [
    "INSERT INTO sales_order_lines (id, product_code) VALUES (1, 'SALE-1')",
    "INSERT INTO sales_import_batches (id, status) VALUES ('completed-batch', 'completed')",
    "INSERT INTO sales_import_uploads VALUES ('expired-upload', 'ready', '2000-01-01T00:00:00Z')",
    "INSERT INTO sales_import_upload_chunks VALUES ('orphan-upload', 0, 'sales/orphan/0')",
    "INSERT INTO sales_overview_response_cache VALUES ('cache', '{}')",
    "INSERT INTO sales_projection_outbox VALUES (1, 'sales')",
    "INSERT INTO import_content_fingerprints VALUES (10, 'sales', 'completed')",
    "INSERT INTO import_content_attempts VALUES (10, 'sales', 'completed')",
    "INSERT INTO import_scope_heads VALUES ('sales', 'completed-scope', 'ready')",
    "UPDATE sales_overview_cache_state SET sales_revision = 2 WHERE id = 1",
  ];
  const sql = await migration("0092_sales_domain_retirement");
  for (const blocker of blockers) {
    await t.test(blocker, async () => {
      const sqlite = await database();
      try {
        sqlite.exec("UPDATE sales_overview_cache_state SET sales_revision = 1 WHERE id = 1");
        sqlite.exec(blocker);
        assert.throws(() => sqlite.exec(sql), /integer overflow/);
        assert.equal(tableExists(sqlite, "sales_order_lines"), true);
        assert.equal(sqlite.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "d1");
      } finally {
        sqlite.close();
      }
    });
  }
});

test("0092 refuses every in-flight sales blocker even with PostgreSQL authority", async (t) => {
  const blockers = [
    "INSERT INTO sales_import_batches VALUES ('processing-batch', 'processing')",
    "INSERT INTO sales_import_uploads VALUES ('active-upload', 'uploading', '2999-01-01T00:00:00Z')",
    "INSERT INTO sales_import_uploads VALUES ('invalid-expiry-upload', 'ready', 'not-a-timestamp')",
    "INSERT INTO import_content_fingerprints VALUES (10, 'sales', 'processing')",
    "INSERT INTO import_content_attempts VALUES (10, 'sales', 'processing')",
    "INSERT INTO import_scope_heads VALUES ('sales', 'processing-scope', 'processing')",
  ];
  const sql = await migration("0092_sales_domain_retirement");

  for (const blocker of blockers) {
    await t.test(blocker, async () => {
      const sqlite = await database();
      try {
        activatePostgresql(sqlite);
        // A corrupted/recovered source could contain a stale in-flight row
        // despite the authority fence; remove only that test fixture trigger
        // to prove 0092 independently revalidates quiescence.
        if (blocker.includes("sales_import_batches")) sqlite.exec("DROP TRIGGER sales_authority_batches_insert");
        if (blocker.includes("sales_import_uploads")) sqlite.exec("DROP TRIGGER sales_authority_uploads_insert");
        if (blocker.includes("import_content_fingerprints")) sqlite.exec("DROP TRIGGER sales_authority_fingerprints_insert");
        if (blocker.includes("import_content_attempts")) sqlite.exec("DROP TRIGGER sales_authority_attempts_insert");
        if (blocker.includes("import_scope_heads")) sqlite.exec("DROP TRIGGER sales_authority_scope_heads_insert");
        sqlite.exec(blocker);
        await installApprovedRetirementTicket(sqlite);

        assert.throws(() => sqlite.exec(sql), /integer overflow/);
        assert.equal(tableExists(sqlite, "sales_order_lines"), true);
        assert.equal(sqlite.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner, "postgresql");
      } finally {
        sqlite.close();
      }
    });
  }
});

test("0092 removes only legacy D1 sales state and preserves ERP plus other domains", async () => {
  const sqlite = await database();
  try {
    seedRecords(sqlite);
    activatePostgresql(sqlite);
    await installApprovedRetirementTicket(sqlite);
    sqlite.exec(await migration("0092_sales_domain_retirement"));

    for (const table of retiredTables) {
      assert.equal(tableExists(sqlite, table), false, table);
      assert.equal(viewExists(sqlite, table), true, table);
      assert.equal(sqlite.prepare(`SELECT COUNT(*) count FROM "${table}"`).get()?.count, 0, table);
    }
    for (const trigger of [
      "market_monthly_summary_sales_insert",
      "market_monthly_summary_sales_update",
      "market_monthly_summary_sales_delete",
      "sales_authority_fingerprints_delete",
    ]) assert.equal(triggerExists(sqlite, trigger), false, trigger);

    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_content_fingerprints WHERE domain='sales'").get()?.count, 0);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_content_attempts WHERE domain='sales'").get()?.count, 0);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_scope_heads WHERE domain='sales'").get()?.count, 0);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_content_fingerprints WHERE domain='inventory'").get()?.count, 1);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_content_attempts WHERE domain='inventory'").get()?.count, 1);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM import_scope_heads WHERE domain='inventory'").get()?.count, 1);

    for (const table of [
      "erp_reference_projection_source_state",
      "erp_product_projection_state",
      "erp_reference_projection_outbox",
      "erp_product_master",
      "inventory_stock_lines",
    ]) assert.equal(tableExists(sqlite, table), true, table);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM erp_product_master").get()?.count, 1);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM erp_reference_projection_outbox").get()?.count, 1);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM inventory_stock_lines").get()?.count, 1);
    assert.equal(triggerExists(sqlite, "erp_reference_projection_outbox_no_update"), true);
    for (const trigger of SHARED_IMPORT_RETIREMENT_GUARDS) {
      assert.equal(triggerExists(sqlite, trigger), true, trigger);
    }
    assert.equal(sqlite.prepare(
      "SELECT COUNT(*) count FROM sqlite_master WHERE type='trigger' AND name LIKE 'sales_authority_%'",
    ).get()?.count, 0);
    assert.equal(
      sqlite.prepare("SELECT status FROM domain_retirement_receipts WHERE domain='sales'").get()?.status,
      "approved",
    );
  } finally {
    sqlite.close();
  }
});

test("0092 permanently fences exact sales identities in shared import tables while preserving non-sales CRUD", async (t) => {
  const sqlite = await database();
  try {
    activatePostgresql(sqlite);
    await installApprovedRetirementTicket(sqlite);
    sqlite.exec(await migration("0092_sales_domain_retirement"));

    const cases = [
      {
        table: "import_content_fingerprints",
        insertGuard: "sales_retired_fingerprints_insert_guard",
        insertSales: "INSERT INTO import_content_fingerprints VALUES (100, 'sales', 'completed')",
        insertNonSales: "INSERT INTO import_content_fingerprints VALUES (101, 'inventory', 'completed')",
        updateNonSales: "UPDATE import_content_fingerprints SET domain='netshop', status='processing' WHERE sequence=101",
        updateToSales: "UPDATE import_content_fingerprints SET domain='sales' WHERE sequence=101",
        updateSales: "UPDATE import_content_fingerprints SET status='processing' WHERE sequence=100",
        deleteSales: "DELETE FROM import_content_fingerprints WHERE sequence=100",
        deleteNonSales: "DELETE FROM import_content_fingerprints WHERE sequence=101",
      },
      {
        table: "import_content_attempts",
        insertGuard: "sales_retired_attempts_insert_guard",
        insertSales: "INSERT INTO import_content_attempts VALUES (100, 'sales', 'completed')",
        insertNonSales: "INSERT INTO import_content_attempts VALUES (101, 'inventory', 'completed')",
        updateNonSales: "UPDATE import_content_attempts SET domain='netshop', outcome='processing' WHERE sequence=101",
        updateToSales: "UPDATE import_content_attempts SET domain='sales' WHERE sequence=101",
        updateSales: "UPDATE import_content_attempts SET outcome='processing' WHERE sequence=100",
        deleteSales: "DELETE FROM import_content_attempts WHERE sequence=100",
        deleteNonSales: "DELETE FROM import_content_attempts WHERE sequence=101",
      },
      {
        table: "import_scope_heads",
        insertGuard: "sales_retired_scope_heads_insert_guard",
        insertSales: "INSERT INTO import_scope_heads VALUES ('sales', 'sales-scope', 'ready')",
        insertNonSales: "INSERT INTO import_scope_heads VALUES ('inventory', 'inventory-scope', 'ready')",
        updateNonSales: "UPDATE import_scope_heads SET domain='netshop', status='processing' WHERE scope_key='inventory-scope'",
        updateToSales: "UPDATE import_scope_heads SET domain='sales' WHERE scope_key='inventory-scope'",
        updateSales: "UPDATE import_scope_heads SET status='processing' WHERE scope_key='sales-scope'",
        deleteSales: "DELETE FROM import_scope_heads WHERE scope_key='sales-scope'",
        deleteNonSales: "DELETE FROM import_scope_heads WHERE scope_key='inventory-scope'",
      },
    ] as const;

    for (const item of cases) {
      await t.test(item.table, () => {
        assert.throws(() => sqlite.exec(item.insertSales), /sales_domain_retired/);
        sqlite.exec(item.insertNonSales);
        sqlite.exec(item.updateNonSales);
        assert.throws(() => sqlite.exec(item.updateToSales), /sales_domain_retired/);

        const guardSql = String((sqlite.prepare(
          "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
        ).get(item.insertGuard) as { sql?: string }).sql);
        sqlite.exec(`DROP TRIGGER "${item.insertGuard}"`);
        sqlite.exec(item.insertSales);
        sqlite.exec(guardSql);
        assert.throws(() => sqlite.exec(item.updateSales), /sales_domain_retired/);
        assert.throws(() => sqlite.exec(item.deleteSales), /sales_domain_retired/);

        sqlite.exec(item.deleteNonSales);
      });
    }
  } finally {
    sqlite.close();
  }
});

test("0092 tombstones fail closed against every stale sales schema and write primitive", async (t) => {
  const sqlite = await database();
  try {
    activatePostgresql(sqlite);
    await installApprovedRetirementTicket(sqlite);
    sqlite.exec(await migration("0092_sales_domain_retirement"));

    for (const [index, name] of retiredTables.entries()) {
      await t.test(name, () => {
        sqlite.exec(`CREATE TABLE IF NOT EXISTS "${name}" (resurrected TEXT)`);
        assert.equal(tableExists(sqlite, name), false);
        assert.equal(viewExists(sqlite, name), true);

        assert.throws(
          () => sqlite.exec(`CREATE INDEX "legacy_resurrection_${index}" ON "${name}" (retirement_tombstone)`),
          /view|index/i,
        );
        assert.throws(
          () => sqlite.exec(`INSERT INTO "${name}" (retirement_tombstone) VALUES ('forged')`),
          /view|modify/i,
        );
        assert.throws(
          () => sqlite.exec(`UPDATE "${name}" SET retirement_tombstone='forged'`),
          /view|modify/i,
        );
        assert.throws(
          () => sqlite.exec(`DELETE FROM "${name}"`),
          /view|modify/i,
        );
        assert.throws(
          () => sqlite.exec(`DROP TABLE IF EXISTS "${name}"`),
          /view|drop/i,
        );
        assert.equal(tableExists(sqlite, name), false);
        assert.equal(viewExists(sqlite, name), true);
        assert.equal(sqlite.prepare(`SELECT COUNT(*) count FROM "${name}"`).get()?.count, 0);
      });
    }
  } finally {
    sqlite.close();
  }
});
