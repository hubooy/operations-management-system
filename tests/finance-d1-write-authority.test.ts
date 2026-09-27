import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const guardedTables = [
  "finance_import_batches",
  "finance_months",
  "finance_lines",
  "finance_targets",
  "finance_target_versions",
  "finance_target_deletion_audits",
  "finance_targets_scoped",
  "finance_target_scoped_versions",
  "finance_target_scoped_deletion_audits",
  "finance_target_legacy_migrations",
] as const;

test("D1 finance authority is neutral in d1 and fences every finance write after pending", async () => {
  const sqlite = new DatabaseSync(":memory:");
  for (const table of guardedTables) sqlite.exec(`CREATE TABLE ${table} (id TEXT PRIMARY KEY)`);
  sqlite.exec("CREATE TABLE import_content_fingerprints (id INTEGER PRIMARY KEY, domain TEXT NOT NULL)");
  sqlite.exec("CREATE TABLE import_content_attempts (id INTEGER PRIMARY KEY, domain TEXT NOT NULL)");
  sqlite.exec("CREATE TABLE import_scope_heads (id INTEGER PRIMARY KEY, domain TEXT NOT NULL)");
  sqlite.exec(await readFile(new URL("../drizzle/0093_finance_write_authority.sql", import.meta.url), "utf8"));

  sqlite.exec("INSERT INTO finance_lines VALUES ('before-cutover')");
  sqlite.exec("INSERT INTO import_content_attempts VALUES (1, 'inventory')");
  sqlite.exec(`UPDATE finance_write_authority
    SET owner='pending', epoch=epoch+1, cutover_id='finance-cutover-1', updated_at=CURRENT_TIMESTAMP
    WHERE id=1 AND owner='d1'`);

  for (const table of guardedTables) {
    assert.throws(
      () => sqlite.exec(`INSERT INTO ${table} VALUES ('blocked-${table}')`),
      /finance_write_authority_not_d1/,
    );
  }
  assert.throws(
    () => sqlite.exec("INSERT INTO import_content_fingerprints VALUES (2, 'finance')"),
    /finance_write_authority_not_d1/,
  );
  assert.throws(
    () => sqlite.exec("UPDATE import_content_attempts SET domain='finance' WHERE id=1"),
    /finance_write_authority_not_d1/,
  );
  sqlite.exec("INSERT INTO import_scope_heads VALUES (3, 'inventory')");
  const financeLineCount = sqlite.prepare("SELECT COUNT(*) AS count FROM finance_lines")
    .get() as { count: number } | undefined;
  assert.ok(financeLineCount);
  assert.equal(financeLineCount.count, 1);

  assert.throws(
    () => sqlite.exec("UPDATE finance_write_authority SET owner='d1', epoch=epoch+1, cutover_id='different-cutover' WHERE id=1"),
    /finance_write_authority_invalid_transition/,
  );
  sqlite.exec(`UPDATE finance_write_authority
    SET owner='d1', epoch=epoch+1, cutover_id='finance-cutover-1', updated_at=CURRENT_TIMESTAMP
    WHERE id=1 AND owner='pending' AND cutover_id='finance-cutover-1'`);
  sqlite.exec(`UPDATE finance_write_authority
    SET owner='pending', epoch=epoch+1, cutover_id='finance-cutover-1', updated_at=CURRENT_TIMESTAMP
    WHERE id=1 AND owner='d1'`);
  sqlite.exec(`UPDATE finance_write_authority
    SET owner='postgresql', epoch=epoch+1, cutover_id='finance-cutover-1', updated_at=CURRENT_TIMESTAMP
    WHERE id=1 AND owner='pending' AND cutover_id='finance-cutover-1'`);
  assert.deepEqual(
    { ...sqlite.prepare("SELECT owner, epoch, cutover_id AS cutoverId FROM finance_write_authority").get() },
    { owner: "postgresql", epoch: 5, cutoverId: "finance-cutover-1" },
  );
  assert.throws(
    () => sqlite.exec("DELETE FROM finance_write_authority WHERE id=1"),
    /finance_write_authority_delete_forbidden/,
  );
  sqlite.close();
});

test("legacy finance schema setup checks authority before replaying mutating backfills", async () => {
  const source = await readFile(new URL("../lib/finance/database.ts", import.meta.url), "utf8");
  const authorityCheck = source.indexOf("SELECT owner FROM finance_write_authority");
  const schemaBatch = source.indexOf("db.batch(financeSchemaStatements");
  assert.ok(authorityCheck > 0 && schemaBatch > authorityCheck);
  assert.match(source, /authority\.owner !== "d1"/);
});
