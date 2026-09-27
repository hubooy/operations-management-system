import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const migrationDirectory = new URL("../drizzle/", import.meta.url);

function statements(source: string) {
  return source.split("--> statement-breakpoint").map((value) => value.trim()).filter(Boolean);
}

async function apply(sqlite: DatabaseSync, name: string) {
  const source = await readFile(new URL(name, migrationDirectory), "utf8");
  for (const statement of statements(source)) sqlite.exec(statement);
}

async function createPreCutoverSchema(sqlite: DatabaseSync) {
  const normal = (await readdir(migrationDirectory))
    .filter((name) => /^\d{4}_.+\.sql$/.test(name) && Number(name.slice(0, 4)) <= 91)
    .sort();
  for (const name of normal) await apply(sqlite, name);
  await apply(sqlite, "0103_workflow_launch_write_authority.sql");
}

test("workflow launch D1 retirement removes only launch evidence and is terminal", async () => {
  const sqlite = new DatabaseSync(":memory:");
  try {
    await createPreCutoverSchema(sqlite);
    sqlite.exec(`
      INSERT INTO workflow_operation_records
        (id,record_type,title,status,shop_name,occurred_at,created_by,updated_by)
      VALUES
        ('launch-1','launch','新品','待开始','测试店','2026-09-01T00:00:00Z','a','a'),
        ('review-1','review','评价','待回复','测试店','2026-09-01T00:00:00Z','a','a');
      INSERT INTO workflow_operation_activities
        (id,record_id,action,actor_email,actor_role,to_version)
      VALUES
        ('activity-launch','launch-1','created','a','admin',1),
        ('activity-review','review-1','created','a','admin',1);
      UPDATE workflow_launch_write_authority
      SET owner='pending',epoch=2,cutover_id='workflow-test'
      WHERE id=1;
      UPDATE workflow_launch_write_authority
      SET owner='postgresql',epoch=3,cutover_id='workflow-test'
      WHERE id=1;
    `);

    const retirementSource = await readFile(
      new URL("0104_workflow_launch_domain_retirement.sql", migrationDirectory),
      "utf8",
    );
    const retirementStatements = statements(retirementSource);
    for (const statement of retirementStatements.slice(0, 4)) sqlite.exec(statement);
    sqlite.prepare(`INSERT INTO domain_retirement_receipts (
      domain,version,status,cutover_id,plan_id,attestation_sha256,
      smoke_receipt_sha256,preflight_evidence_sha256,migration_sha256,
      audit_id,preserved_evidence_sha256,created_at,completed_at
    ) VALUES ('workflow-launch','workflow-launch-domain-retirement-receipt-v1','approved',?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, NULL)`).run(
      "workflow-test",
      "a".repeat(64),
      "b".repeat(64),
      "c".repeat(64),
      "d".repeat(64),
      "e".repeat(64),
      "f".repeat(64),
      "0".repeat(64),
    );
    for (const statement of retirementStatements.slice(4)) sqlite.exec(statement);

    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM workflow_operation_records WHERE record_type='launch'").get()!.count, 0);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM workflow_operation_activities WHERE record_id='launch-1'").get()!.count, 0);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM workflow_operation_records WHERE id='review-1'").get()!.count, 1);
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM workflow_operation_activities WHERE id='activity-review'").get()!.count, 1);
    assert.deepEqual(
      { ...sqlite.prepare("SELECT type,name FROM sqlite_master WHERE name='workflow_launch_write_authority'").get() },
      { type: "view", name: "workflow_launch_write_authority" },
    );
    assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM workflow_launch_write_authority").get()!.count, 0);
    assert.equal(sqlite.prepare("SELECT status FROM domain_retirement_receipts WHERE domain='workflow-launch'").get()!.status, "completed");
    assert.throws(
      () => sqlite.exec(`INSERT INTO workflow_operation_records
        (id,record_type,title,status,shop_name,occurred_at,created_by,updated_by)
        VALUES ('launch-2','launch','复活','待开始','测试店','2026-09-01T00:00:00Z','a','a')`),
      /workflow_launch_domain_retired/,
    );
    assert.throws(
      () => sqlite.exec("UPDATE workflow_operation_records SET record_type='launch' WHERE id='review-1'"),
      /workflow_launch_domain_retired/,
    );
  } finally {
    sqlite.close();
  }
});

