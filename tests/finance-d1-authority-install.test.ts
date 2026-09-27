import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const execFileAsync = promisify(execFile);

function createFinanceD1(target: string, processing = false) {
  const database = new DatabaseSync(target);
  database.exec(`
    CREATE TABLE finance_import_batches (id TEXT, status TEXT NOT NULL);
    CREATE TABLE finance_months (id TEXT, status TEXT NOT NULL);
    CREATE TABLE finance_lines (id INTEGER);
    CREATE TABLE finance_targets (id TEXT);
    CREATE TABLE finance_target_versions (id TEXT);
    CREATE TABLE finance_target_deletion_audits (id TEXT);
    CREATE TABLE finance_targets_scoped (id TEXT);
    CREATE TABLE finance_target_scoped_versions (id TEXT);
    CREATE TABLE finance_target_scoped_deletion_audits (id TEXT);
    CREATE TABLE finance_target_legacy_migrations (id TEXT);
    CREATE TABLE import_content_fingerprints (domain TEXT);
    CREATE TABLE import_content_attempts (domain TEXT, outcome TEXT);
    CREATE TABLE import_scope_heads (domain TEXT, status TEXT, owner_token TEXT);
  `);
  if (processing) {
    database.prepare(
      "INSERT INTO import_content_attempts (domain, outcome) VALUES (?, ?)",
    ).run("finance", "processing");
  }
  database.close();
}

test("finance authority installer creates a verified pre-change backup and permanent D1 guards", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "teruisi-finance-authority-"));
  try {
    const source = path.join(directory, "source.sqlite");
    const backup = path.join(directory, "backup.sqlite");
    const receipt = path.join(directory, "receipt.json");
    createFinanceD1(source);
    const { stdout } = await execFileAsync("python", [
      path.resolve("tools/finance-d1-authority-install.py"),
      "--source", source,
      "--sql", path.resolve("drizzle/0093_finance_write_authority.sql"),
      "--backup", backup,
      "--receipt", receipt,
    ], { cwd: process.cwd(), windowsHide: true });
    const result = JSON.parse(stdout) as Record<string, unknown>;
    assert.equal(result.status, "installed");
    assert.equal(typeof result.backupSha256, "string");
    assert.equal((result.backupSha256 as string).length, 64);
    assert.ok(Number(result.triggerCount) > 10);
    assert.equal(JSON.parse(await readFile(receipt, "utf8")).status, "installed");

    const backupDb = new DatabaseSync(backup, { readOnly: true });
    try {
      assert.equal(
        backupDb.prepare("SELECT COUNT(*) AS count FROM sqlite_master WHERE name='finance_write_authority'").get()?.count,
        0,
      );
    } finally {
      backupDb.close();
    }

    const sourceDb = new DatabaseSync(source);
    try {
      const authority = sourceDb.prepare(
        "SELECT owner, epoch, cutover_id AS cutoverId FROM finance_write_authority",
      ).get();
      assert.equal(authority?.owner, "d1");
      assert.equal(authority?.epoch, 1);
      assert.equal(authority?.cutoverId, "");
      sourceDb.exec(
        "UPDATE finance_write_authority SET owner='pending', epoch=2, cutover_id='finance-cutover-test' WHERE id=1",
      );
      assert.throws(
        () => sourceDb.exec("INSERT INTO finance_lines (id) VALUES (1)"),
        /finance_write_authority_not_d1/,
      );
    } finally {
      sourceDb.close();
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("finance authority installer fails closed before backup or DDL when a finance write is in flight", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "teruisi-finance-authority-busy-"));
  try {
    const source = path.join(directory, "source.sqlite");
    const backup = path.join(directory, "backup.sqlite");
    const receipt = path.join(directory, "receipt.json");
    createFinanceD1(source, true);
    await assert.rejects(
      execFileAsync("python", [
        path.resolve("tools/finance-d1-authority-install.py"),
        "--source", source,
        "--sql", path.resolve("drizzle/0093_finance_write_authority.sql"),
        "--backup", backup,
        "--receipt", receipt,
      ], { cwd: process.cwd(), windowsHide: true }),
    );
    const database = new DatabaseSync(source, { readOnly: true });
    assert.equal(
      database.prepare("SELECT COUNT(*) AS count FROM sqlite_master WHERE name='finance_write_authority'").get()?.count,
      0,
    );
    database.close();
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
