from __future__ import annotations

import json
import sqlite3
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from workflow.models import (
    WorkflowOperationRecord,
    WorkflowOperationsMigrationRun,
    WorkflowOperationsWriteAuthority,
    WorkflowTask,
    WorkflowTaskAttachment,
    WorkflowTaskComment,
    WorkflowTaskTemplate,
)


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE workflow_tasks (id TEXT PRIMARY KEY,title TEXT,work_content TEXT,category TEXT,owner TEXT,shop_name TEXT,start_date TEXT,due_date TEXT,status TEXT,priority TEXT,created_by TEXT,updated_by TEXT,created_at TEXT,updated_at TEXT);
CREATE TABLE workflow_task_bootstrap (key TEXT PRIMARY KEY,seeded_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE workflow_task_states (task_id TEXT PRIMARY KEY,version INTEGER,mutation_token TEXT,deleted_at TEXT,deleted_by TEXT,FOREIGN KEY(task_id) REFERENCES workflow_tasks(id));
CREATE TABLE workflow_task_comments (id TEXT PRIMARY KEY,task_id TEXT,content TEXT,created_by TEXT,created_at TEXT,FOREIGN KEY(task_id) REFERENCES workflow_tasks(id));
CREATE TABLE workflow_task_activity_logs (id TEXT PRIMARY KEY,task_id TEXT,action TEXT,summary TEXT,metadata_json TEXT,actor_email TEXT,created_at TEXT,FOREIGN KEY(task_id) REFERENCES workflow_tasks(id));
CREATE TABLE workflow_task_reminders (id TEXT PRIMARY KEY,task_id TEXT,remind_at TEXT,note TEXT,status TEXT,created_by TEXT,created_at TEXT,updated_at TEXT,FOREIGN KEY(task_id) REFERENCES workflow_tasks(id));
CREATE TABLE workflow_task_templates (id TEXT PRIMARY KEY,name TEXT,description TEXT,title TEXT,work_content TEXT,category TEXT,owner TEXT,shop_name TEXT,start_offset_days INTEGER,due_offset_days INTEGER,priority TEXT,active INTEGER,created_by TEXT,updated_by TEXT,created_at TEXT,updated_at TEXT);
CREATE TABLE workflow_task_template_states (template_id TEXT PRIMARY KEY,version INTEGER,mutation_token TEXT,FOREIGN KEY(template_id) REFERENCES workflow_task_templates(id));
CREATE TABLE workflow_task_entity_links (id TEXT PRIMARY KEY,task_id TEXT,entity_type TEXT,entity_id TEXT,label TEXT,url TEXT,created_by TEXT,created_at TEXT,FOREIGN KEY(task_id) REFERENCES workflow_tasks(id));
CREATE TABLE workflow_task_attachments (id TEXT PRIMARY KEY,task_id TEXT,file_name TEXT,mime_type TEXT,size_bytes INTEGER,sha256 TEXT,object_key TEXT UNIQUE,created_by TEXT,created_at TEXT,FOREIGN KEY(task_id) REFERENCES workflow_tasks(id));
CREATE TABLE workflow_attachment_cleanup_queue (object_key TEXT PRIMARY KEY,attempts INTEGER,last_error TEXT,enqueued_at TEXT,updated_at TEXT);
CREATE TABLE workflow_operation_records (id TEXT PRIMARY KEY,record_type TEXT,title TEXT,status TEXT,priority TEXT,platform TEXT,channel TEXT,shop_name TEXT,owner TEXT,occurred_at TEXT,due_at TEXT,content TEXT,source TEXT,source_ref TEXT,reference_code TEXT,version INTEGER,mutation_token TEXT,created_by TEXT,updated_by TEXT,created_at TEXT,updated_at TEXT,deleted_at TEXT,deleted_by TEXT);
CREATE TABLE workflow_operation_activities (id TEXT PRIMARY KEY,record_id TEXT,action TEXT,actor_email TEXT,actor_role TEXT,from_version INTEGER,to_version INTEGER,detail_json TEXT,created_at TEXT);
"""


class WorkflowOperationsMigrationTests(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "sealed.sqlite3"
        connection = sqlite3.connect(self.source)
        connection.executescript(SCHEMA)
        stamp = "2026-09-03 00:00:00"
        connection.execute(
            "INSERT INTO workflow_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("task-1", "迁移任务", "完整内容", "工作计划", "运营组", "京东一店", "2026-09-03", "2026-09-05", "工作中", "high", "operator@example.test", "operator@example.test", stamp, stamp),
        )
        connection.execute("INSERT INTO workflow_task_states VALUES (?,?,?,?,?)", ("task-1", 3, "token-task", None, None))
        connection.execute("INSERT INTO workflow_task_comments VALUES (?,?,?,?,?)", ("comment-1", "task-1", "保留评论", "operator@example.test", stamp))
        connection.execute("INSERT INTO workflow_task_activity_logs VALUES (?,?,?,?,?,?,?)", ("activity-1", "task-1", "task.updated", "更新任务", '{"changedFields":["status"]}', "operator@example.test", stamp))
        connection.execute("INSERT INTO workflow_task_reminders VALUES (?,?,?,?,?,?,?,?)", ("reminder-1", "task-1", "2026-09-04T01:00:00Z", "提醒", "pending", "operator@example.test", stamp, stamp))
        connection.execute(
            "INSERT INTO workflow_task_templates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("template-1", "巡店模板", "说明", "每日巡店", "检查", "巡店查询", "运营组", "京东一店", 0, 1, "normal", 1, "admin@example.test", "admin@example.test", stamp, stamp),
        )
        connection.execute("INSERT INTO workflow_task_template_states VALUES (?,?,?)", ("template-1", 2, "token-template"))
        connection.execute("INSERT INTO workflow_task_entity_links VALUES (?,?,?,?,?,?,?,?)", ("link-1", "task-1", "product", "P-1", "货品 P-1", "", "operator@example.test", stamp))
        connection.execute(
            "INSERT INTO workflow_task_attachments VALUES (?,?,?,?,?,?,?,?,?)",
            ("attachment-1", "task-1", "证据.txt", "text/plain", 8, "a" * 64, "workflow-attachments/task-1/attachment-1", "operator@example.test", stamp),
        )
        connection.execute("INSERT INTO workflow_attachment_cleanup_queue VALUES (?,?,?,?,?)", ("workflow-attachments/old-task/old-attachment", 1, "retry", stamp, stamp))
        connection.execute(
            "INSERT INTO workflow_operation_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("record-1", "inspection", "巡店记录", "待处理", "normal", "京东", "线上", "京东一店", "运营组", "2026-09-03T02:00:00Z", None, "异常", "manual", "", "REF-1", 2, "token-record", "operator@example.test", "operator@example.test", stamp, stamp, None, None),
        )
        connection.execute(
            "INSERT INTO workflow_operation_activities VALUES (?,?,?,?,?,?,?,?,?)",
            ("record-activity-1", "record-1", "created", "operator@example.test", "operator", None, 1, '{"changedFields":["title"],"fromStatus":null,"toStatus":"待处理"}', stamp),
        )
        connection.commit()
        connection.close()

    def plan(self) -> dict[str, object]:
        output = StringIO()
        call_command("migrate_workflow_operations_from_d1", source=str(self.source), mode="dry-run", stdout=output)
        return json.loads(output.getvalue())

    def apply_migration(self) -> str:
        run_id = str(self.plan()["runId"])
        call_command(
            "migrate_workflow_operations_from_d1",
            source=str(self.source),
            mode="apply",
            approved_run_id=run_id,
            stdout=StringIO(),
        )
        return run_id

    def install_authority(self) -> None:
        sql_path = Path(__file__).resolve().parents[3] / "drizzle" / "0105_workflow_operations_write_authority.sql"
        connection = sqlite3.connect(self.source)
        try:
            for statement in sql_path.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if statement.strip():
                    connection.execute(statement.strip())
            connection.commit()
        finally:
            connection.close()

    def test_plan_apply_and_verify_preserve_all_business_facts(self) -> None:
        plan = self.plan()
        run_id = str(plan["runId"])
        self.assertTrue(run_id.startswith("workflow-ops-"))
        self.assertEqual(plan["sourceCounts"]["tasks"], 1)
        self.assertFalse(WorkflowTask.objects.exists())

        applied = StringIO()
        call_command(
            "migrate_workflow_operations_from_d1",
            source=str(self.source),
            mode="apply",
            approved_run_id=run_id,
            stdout=applied,
        )
        result = json.loads(applied.getvalue())
        self.assertTrue(result["verified"])
        self.assertEqual(result["sourceSnapshotDigest"], result["targetSnapshotDigest"])
        migrated_task = WorkflowTask.objects.get(id="task-1")
        self.assertEqual(migrated_task.version, 3)
        self.assertEqual(migrated_task.deleted_by, "")
        self.assertEqual(WorkflowTaskComment.objects.get(id="comment-1").content, "保留评论")
        self.assertEqual(WorkflowTaskTemplate.objects.get(id="template-1").version, 2)
        self.assertEqual(WorkflowTaskAttachment.objects.get(id="attachment-1").sha256, "a" * 64)
        migrated_record = WorkflowOperationRecord.objects.get(id="record-1")
        self.assertEqual(migrated_record.version, 2)
        self.assertEqual(migrated_record.deleted_by, "")
        self.assertEqual(WorkflowOperationsWriteAuthority.objects.get(id=1).migration_verify_run_id, run_id)
        self.assertTrue(WorkflowOperationsMigrationRun.objects.filter(id=run_id, status="verified").exists())

        verified = StringIO()
        call_command(
            "migrate_workflow_operations_from_d1",
            source=str(self.source),
            mode="verify-only",
            approved_run_id=run_id,
            stdout=verified,
        )
        self.assertTrue(json.loads(verified.getvalue())["verified"])

    def test_apply_rejects_changed_source_and_invalid_launch_rows(self) -> None:
        run_id = str(self.plan()["runId"])
        connection = sqlite3.connect(self.source)
        connection.execute("UPDATE workflow_tasks SET title='changed' WHERE id='task-1'")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(CommandError, "approved-run-id"):
            call_command(
                "migrate_workflow_operations_from_d1",
                source=str(self.source),
                mode="apply",
                approved_run_id=run_id,
            )

        connection = sqlite3.connect(self.source)
        connection.execute("UPDATE workflow_operation_records SET record_type='launch' WHERE id='record-1'")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(CommandError, "launch"):
            self.plan()

    def test_orphan_operation_activity_fails_closed(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute("UPDATE workflow_operation_activities SET record_id='missing'")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(CommandError, "孤儿"):
            self.plan()

    def test_invalid_operation_deadline_fails_closed(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute(
            "UPDATE workflow_operation_records SET due_at='2026-09-03T01:59:59Z' WHERE id='record-1'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(CommandError, "截止时间早于发生时间"):
            self.plan()

    def test_attachment_object_key_must_bind_task_and_attachment(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute(
            "UPDATE workflow_task_attachments SET object_key='workflow-attachments/task-1/other' WHERE id='attachment-1'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(CommandError, "附件对象键"):
            self.plan()

    def test_authority_prepare_blocks_d1_and_activate_is_terminal(self) -> None:
        run_id = self.apply_migration()
        self.install_authority()
        cutover_id = "workflow-operations-test-cutover"
        call_command(
            "workflow_operations_write_authority",
            source=str(self.source),
            prepare=True,
            approved_run_id=run_id,
            cutover_id=cutover_id,
            stdout=StringIO(),
        )
        source = sqlite3.connect(self.source)
        try:
            self.assertEqual(
                source.execute("SELECT owner FROM workflow_operations_write_authority WHERE id=1").fetchone()[0],
                "pending",
            )
            with self.assertRaisesRegex(sqlite3.DatabaseError, "workflow_operations_authority_not_legacy"):
                source.execute("INSERT INTO workflow_task_bootstrap(key) VALUES ('blocked')")
        finally:
            source.close()

        call_command(
            "workflow_operations_write_authority",
            source=str(self.source),
            activate=True,
            approved_run_id=run_id,
            cutover_id=cutover_id,
            stdout=StringIO(),
        )
        authority = WorkflowOperationsWriteAuthority.objects.get(id=1)
        self.assertEqual(authority.status, "postgres")
        self.assertEqual(authority.cutover_id, cutover_id)
        self.assertIsNotNone(authority.authority_epoch)
        source = sqlite3.connect(self.source)
        try:
            self.assertEqual(
                source.execute("SELECT owner FROM workflow_operations_write_authority WHERE id=1").fetchone()[0],
                "postgresql",
            )
        finally:
            source.close()

    def test_authority_abort_restores_legacy_before_activation(self) -> None:
        run_id = self.apply_migration()
        self.install_authority()
        cutover_id = "workflow-operations-test-abort"
        call_command(
            "workflow_operations_write_authority",
            source=str(self.source),
            prepare=True,
            approved_run_id=run_id,
            cutover_id=cutover_id,
            stdout=StringIO(),
        )
        call_command(
            "workflow_operations_write_authority",
            source=str(self.source),
            abort_pending=True,
            approved_run_id=run_id,
            cutover_id=cutover_id,
            stdout=StringIO(),
        )
        source = sqlite3.connect(self.source)
        try:
            self.assertEqual(
                source.execute("SELECT owner FROM workflow_operations_write_authority WHERE id=1").fetchone()[0],
                "legacy",
            )
            source.execute("INSERT INTO workflow_task_bootstrap(key) VALUES ('allowed')")
            source.commit()
        finally:
            source.close()

    def test_terminal_retirement_requires_plan_and_leaves_tombstones(self) -> None:
        run_id = self.apply_migration()
        self.install_authority()
        cutover_id = "workflow-operations-test-retire"
        call_command(
            "workflow_operations_write_authority",
            source=str(self.source),
            prepare=True,
            approved_run_id=run_id,
            cutover_id=cutover_id,
            stdout=StringIO(),
        )
        call_command(
            "workflow_operations_write_authority",
            source=str(self.source),
            activate=True,
            approved_run_id=run_id,
            cutover_id=cutover_id,
            stdout=StringIO(),
        )
        migration = WorkflowOperationsMigrationRun.objects.get(id=run_id)
        smoke_path = Path(self.temp.name) / "smoke.json"
        smoke_path.write_text(json.dumps({
            "version": "workflow-operations-system-test-receipt-v1",
            "status": "passed",
            "cutoverId": cutover_id,
            "migrationRunId": run_id,
            "sourceDigest": migration.source_snapshot_digest,
            "workerBuildSha256": "b" * 64,
            "checks": {
                name: "passed" for name in (
                    "djangoReader", "djangoWriterNegative", "publicTasks",
                    "publicTaskCollaboration", "publicTaskAttachmentsMetadata",
                    "publicTemplates", "publicOperationRecords", "scopedOperationRecords",
                    "inventoryWorkItemBridge", "globalSearchConsumer", "aiConsumer",
                    "legacyD1Rejected", "attachmentR2Preserved", "otherWorkflowDomainsPreserved",
                )
            },
            "recordedAt": timezone.now().isoformat(),
        }, ensure_ascii=False), encoding="utf-8")

        planned = StringIO()
        call_command(
            "retire_workflow_operations_d1",
            source=str(self.source),
            cutover_id=cutover_id,
            approved_run_id=run_id,
            smoke_receipt=str(smoke_path),
            stdout=planned,
        )
        plan = json.loads(planned.getvalue())
        self.assertEqual(plan["status"], "planned")

        audit_path = Path(self.temp.name) / "retirement-audit.json"
        applied = StringIO()
        call_command(
            "retire_workflow_operations_d1",
            source=str(self.source),
            cutover_id=cutover_id,
            approved_run_id=run_id,
            smoke_receipt=str(smoke_path),
            apply=True,
            approved_plan_id=plan["planId"],
            audit_output=str(audit_path),
            stdout=applied,
        )
        self.assertEqual(json.loads(applied.getvalue())["status"], "retired")
        self.assertTrue(audit_path.is_file())
        source = sqlite3.connect(self.source)
        try:
            objects = {
                (row[0], row[1]) for row in source.execute(
                    "SELECT type,name FROM sqlite_master WHERE name LIKE 'workflow_%'"
                ).fetchall()
            }
            self.assertIn(("view", "workflow_tasks"), objects)
            self.assertIn(("view", "workflow_operation_records"), objects)
            self.assertIn(("view", "workflow_operations_write_authority"), objects)
            self.assertEqual(source.execute("SELECT COUNT(*) FROM workflow_tasks").fetchone()[0], 0)
            with self.assertRaisesRegex(sqlite3.DatabaseError, "workflow_operations_domain_retired"):
                source.execute("INSERT INTO workflow_task_bootstrap(key) VALUES ('forbidden')")
            receipt = source.execute(
                "SELECT status FROM domain_retirement_receipts WHERE domain='workflow-operations'"
            ).fetchone()
            self.assertEqual(receipt[0], "completed")
        finally:
            source.close()
        self.assertTrue(WorkflowTaskAttachment.objects.filter(id="attachment-1").exists())
