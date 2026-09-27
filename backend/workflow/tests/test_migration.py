from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile

from django.core.management import call_command
from django.test import TestCase

from workflow.models import (
    NewProductActivity,
    NewProductProject,
    NewProductStage,
    NewProductTarget,
    WorkflowMigrationRun,
    WorkflowWriteAuthority,
)


ROOT = Path(__file__).resolve().parents[3]
AUTHORITY_SQL = ROOT / "drizzle" / "0103_workflow_launch_write_authority.sql"


def create_source(path: Path, *, install_authority: bool = True) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript((ROOT / "drizzle" / "0059_workflow_operations_records.sql").read_text(
            encoding="utf-8"
        ).replace("--> statement-breakpoint", ""))
        records = [
            (
                "11111111-1111-4111-8111-111111111111", "launch", "商用净水器", "工作中",
                "high", "", "线上", "待确认", "新品负责人", "2026-09-01T01:00:00Z", None,
                "保留的旧说明", "manual", "", "", 3, "token-1", "owner@example.test",
                "owner@example.test", "2026-09-01T01:00:00Z", "2026-09-02T02:00:00Z", None, "",
            ),
            (
                "22222222-2222-4222-8222-222222222222", "launch", "切肉机", "已完成",
                "normal", "京东", "线上", "志高切肉机旗舰店", "运营甲", "2026-09-02T03:00:00Z",
                "2026-09-10T04:00:00Z", "", "manual", "", "", 1, "token-2",
                "operator@example.test", "operator@example.test", "2026-09-02T03:00:00Z",
                "2026-09-03T05:00:00Z", None, "",
            ),
            (
                "33333333-3333-4333-8333-333333333333", "review", "非新品记录", "待处理",
                "normal", "京东", "线上", "其他店", "运营乙", "2026-09-02T03:00:00Z", None,
                "不得迁移", "manual", "", "", 1, "token-3", "other@example.test",
                "other@example.test", "2026-09-02T03:00:00Z", "2026-09-02T03:00:00Z", None, "",
            ),
        ]
        connection.executemany(
            "INSERT INTO workflow_operation_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            records,
        )
        connection.executemany(
            "INSERT INTO workflow_operation_activities VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "11111111-1111-4111-8111-111111111111", "created", "owner@example.test",
                    "admin", None, 1, '{"changedFields":["title"]}', "2026-09-01T01:00:00Z",
                ),
                (
                    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    "11111111-1111-4111-8111-111111111111", "status_changed", "owner@example.test",
                    "admin", 2, 3, '{"fromStatus":"待开始","toStatus":"工作中","changedFields":["status"]}',
                    "2026-09-02T02:00:00Z",
                ),
            ],
        )
        if install_authority:
            for raw in AUTHORITY_SQL.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if raw.strip():
                    connection.execute(raw.strip())
        connection.commit()
    finally:
        connection.close()


def run_json(command: str, *arguments: str) -> dict[str, object]:
    output = StringIO()
    call_command(command, *arguments, stdout=output)
    return json.loads(output.getvalue())


class WorkflowLaunchMigrationTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name) / "workflow.sqlite"
        create_source(self.source)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_apply_verify_preserves_history_and_maps_only_listing_stage(self) -> None:
        plan = run_json("migrate_workflow_launch_from_d1", "--source", str(self.source))
        self.assertEqual(plan["mode"], "plan")
        self.assertEqual(plan["counts"], {
            "projects": 2, "liveProjects": 2, "deletedProjects": 0,
            "targets": 2, "stages": 14, "activities": 2, "gapProjects": 2,
        })
        run_id = str(plan["runId"])
        applied = run_json(
            "migrate_workflow_launch_from_d1", "--source", str(self.source),
            "--apply", "--approved-run-id", run_id,
        )
        self.assertEqual(applied["status"], "applied")
        verified = run_json(
            "migrate_workflow_launch_from_d1", "--source", str(self.source),
            "--verify-only", "--approved-run-id", run_id,
        )
        self.assertEqual(verified["targetDigest"], plan["sourceDigest"])
        self.assertEqual(NewProductProject.objects.count(), 2)
        self.assertEqual(NewProductTarget.objects.count(), 2)
        self.assertEqual(NewProductStage.objects.count(), 14)
        self.assertEqual(NewProductActivity.objects.count(), 2)
        project = NewProductProject.objects.get(id="11111111-1111-4111-8111-111111111111")
        self.assertEqual(project.notes, "保留的旧说明")
        self.assertEqual(project.version, 3)
        self.assertEqual(project.targets.get().platform, "待确认")
        self.assertEqual(project.stages.get(stage_key="listing").status, "in_progress")
        self.assertEqual(
            set(project.stages.exclude(stage_key="listing").values_list("status", flat=True)),
            {"not_applicable"},
        )
        run = WorkflowMigrationRun.objects.get(id=run_id)
        self.assertEqual(run.status, "verified")
        self.assertEqual(WorkflowWriteAuthority.objects.get(id=1).migration_verify_run_id, run_id)

    def test_authority_prepare_abort_and_activate_are_fenced(self) -> None:
        plan = run_json("migrate_workflow_launch_from_d1", "--source", str(self.source))
        run_id = str(plan["runId"])
        run_json(
            "migrate_workflow_launch_from_d1", "--source", str(self.source),
            "--apply", "--approved-run-id", run_id,
        )
        run_json(
            "migrate_workflow_launch_from_d1", "--source", str(self.source),
            "--verify-only", "--approved-run-id", run_id,
        )
        cutover_id = "workflow-test-cutover"
        prepared = run_json(
            "workflow_write_authority", "--source", str(self.source), "--prepare",
            "--approved-run-id", run_id, "--cutover-id", cutover_id,
        )
        self.assertEqual(prepared["status"], "prepared")
        aborted = run_json(
            "workflow_write_authority", "--source", str(self.source), "--abort-pending",
            "--approved-run-id", run_id, "--cutover-id", cutover_id,
        )
        self.assertEqual(aborted["status"], "aborted")
        run_json(
            "workflow_write_authority", "--source", str(self.source), "--prepare",
            "--approved-run-id", run_id, "--cutover-id", cutover_id,
        )
        activated = run_json(
            "workflow_write_authority", "--source", str(self.source), "--activate",
            "--approved-run-id", run_id, "--cutover-id", cutover_id,
        )
        self.assertEqual(activated["status"], "activated")
        self.assertEqual(WorkflowWriteAuthority.objects.get(id=1).status, "postgres")
        connection = sqlite3.connect(self.source)
        try:
            owner = connection.execute(
                "SELECT owner FROM workflow_launch_write_authority WHERE id=1"
            ).fetchone()[0]
            self.assertEqual(owner, "postgresql")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE workflow_operation_records SET title='forbidden' "
                    "WHERE id='11111111-1111-4111-8111-111111111111'"
                )
        finally:
            connection.close()

    def test_authority_transition_accepts_live_wal_but_migration_stays_sealed(self) -> None:
        plan = run_json("migrate_workflow_launch_from_d1", "--source", str(self.source))
        run_id = str(plan["runId"])
        run_json(
            "migrate_workflow_launch_from_d1", "--source", str(self.source),
            "--apply", "--approved-run-id", run_id,
        )
        run_json(
            "migrate_workflow_launch_from_d1", "--source", str(self.source),
            "--verify-only", "--approved-run-id", run_id,
        )
        live_connection = sqlite3.connect(self.source)
        try:
            self.assertEqual(live_connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            live_connection.execute(
                "UPDATE workflow_operation_records SET title=title WHERE record_type='review'"
            )
            live_connection.commit()
            self.assertTrue(Path(f"{self.source}-wal").exists())
            prepared = run_json(
                "workflow_write_authority", "--source", str(self.source), "--prepare",
                "--approved-run-id", run_id, "--cutover-id", "workflow-live-wal-test",
            )
            self.assertEqual(prepared["status"], "prepared")
        finally:
            live_connection.close()
