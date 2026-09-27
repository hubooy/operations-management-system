from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from workflow.management.commands.retire_workflow_launch_d1 import (
    REQUIRED_SMOKE_CHECKS,
    R2_PATTERNS,
    _hash_json,
)
from workflow.models import NewProductStage
from workflow.tests.test_migration import create_source, run_json


class WorkflowLaunchRetirementTests(TestCase):
    def test_plan_accepts_verified_migration_after_legitimate_post_cutover_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "workflow.sqlite"
            smoke = root / "workflow-smoke.json"
            r2 = root / "workflow-r2.json"
            create_source(source)

            migration = run_json("migrate_workflow_launch_from_d1", "--source", str(source))
            run_id = str(migration["runId"])
            run_json(
                "migrate_workflow_launch_from_d1", "--source", str(source),
                "--apply", "--approved-run-id", run_id,
            )
            run_json(
                "migrate_workflow_launch_from_d1", "--source", str(source),
                "--verify-only", "--approved-run-id", run_id,
            )
            cutover_id = "workflow-test-retirement"
            for action in ("--prepare", "--activate"):
                run_json(
                    "workflow_write_authority", "--source", str(source), action,
                    "--approved-run-id", run_id, "--cutover-id", cutover_id,
                )

            # PostgreSQL becomes the live authority after activation. Legitimate writes
            # must not invalidate the immutable migration receipt used for D1 retirement.
            stage = NewProductStage.objects.order_by("id").first()
            assert stage is not None
            NewProductStage.objects.filter(id=stage.id).update(
                notes="切换后的正常业务更新",
                version=stage.version + 1,
            )

            smoke.write_text(
                json.dumps(
                    {
                        "version": "workflow-launch-system-test-receipt-v1",
                        "status": "passed",
                        "cutoverId": cutover_id,
                        "migrationRunId": run_id,
                        "sourceDigest": migration["sourceDigest"],
                        "workerBuildSha256": "9" * 64,
                        "checks": {name: "passed" for name in REQUIRED_SMOKE_CHECKS},
                        "recordedAt": timezone.now().isoformat(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            patterns = {
                pattern: {
                    "objectCount": 0,
                    "objectBytes": 0,
                    "multipartUploadCount": 0,
                    "multipartPartCount": 0,
                }
                for pattern in R2_PATTERNS
            }
            r2.write_text(
                json.dumps(
                    {
                        "version": "workflow-launch-r2-retirement-evidence-v1",
                        "status": "passed",
                        "patterns": patterns,
                        "totalObjectCount": 0,
                        "matchesDigest": _hash_json(patterns),
                        "sourcePathSha256": "7" * 64,
                        "recordedAt": timezone.now().isoformat(),
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )

            output = StringIO()
            call_command(
                "retire_workflow_launch_d1",
                source=str(source),
                cutover_id=cutover_id,
                approved_run_id=run_id,
                smoke_receipt=str(smoke),
                r2_evidence=str(r2),
                stdout=output,
            )
            plan = json.loads(output.getvalue())
            self.assertEqual(plan["status"], "planned")
            self.assertEqual(plan["approvedRunId"], run_id)
            self.assertEqual(
                NewProductStage.objects.get(id=stage.id).notes,
                "切换后的正常业务更新",
            )
