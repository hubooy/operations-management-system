from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .test_migration import create_source


CHECKS = {
    "djangoReader": "passed",
    "djangoWriterNegative": "passed",
    "publicConversations": "passed",
    "publicImportHistory": "passed",
    "publicChunkUpload": "passed",
    "globalSearchConsumer": "passed",
    "aiConsumer": "passed",
    "legacyD1Rejected": "passed",
    "legacyR2Rejected": "passed",
    "otherDomainsPreserved": "passed",
}


class CustomerServiceRetirementTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "customer-service.sqlite3"
        create_source(self.source)

    def test_authority_installer_fences_legacy_shared_uploads(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        backup = self.root / "authority-backup.sqlite"
        receipt = self.root / "authority-receipt.json"
        result = subprocess.run(
            [
                sys.executable,
                str(repository / "tools" / "customer-service-d1-authority-install.py"),
                "--source",
                str(self.source),
                "--sql",
                str(repository / "drizzle" / "0107_customer_service_write_authority.sql"),
                "--backup",
                str(backup),
                "--receipt",
                str(receipt),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["triggerCount"], 33)
        self.assertTrue(backup.is_file())
        self.assertTrue(receipt.is_file())

    def _migrate_and_activate(self) -> tuple[str, str, str]:
        planned = StringIO()
        call_command(
            "migrate_customer_service_from_d1",
            source=str(self.source),
            plan=True,
            stdout=planned,
        )
        plan_id = json.loads(planned.getvalue())["planId"]
        applied = StringIO()
        call_command(
            "migrate_customer_service_from_d1",
            source=str(self.source),
            apply=True,
            approved_plan_id=plan_id,
            stdout=applied,
        )
        migration = json.loads(applied.getvalue())
        run_id = migration["runId"]
        source_digest = migration["sourceDigest"]

        authority_sql = (
            Path(__file__).resolve().parents[3]
            / "drizzle"
            / "0107_customer_service_write_authority.sql"
        )
        source = sqlite3.connect(self.source)
        try:
            for statement in authority_sql.read_text(encoding="utf-8").split(
                "--> statement-breakpoint"
            ):
                if statement.strip():
                    source.execute(statement.strip())
            source.execute(
                "INSERT INTO inventory_import_uploads "
                "(id,fingerprint,file_name,file_size_bytes,chunk_size_bytes,chunk_count,"
                "received_chunk_count,received_bytes,status,expires_at) "
                "VALUES ('customer-upload','customer-service:session:done','done.xlsx',1,1,1,1,1,'completed','2999-01-01T00:00:00Z')"
            )
            source.execute(
                "INSERT INTO inventory_import_upload_results(upload_id,result_json) "
                "VALUES ('customer-upload','{\"ok\":true}')"
            )
            source.execute(
                "INSERT INTO inventory_import_uploads "
                "(id,fingerprint,file_name,file_size_bytes,chunk_size_bytes,chunk_count,"
                "received_chunk_count,received_bytes,status,expires_at) "
                "VALUES ('other-upload','other-domain:done','other.xlsx',1,1,1,1,1,'completed','2999-01-01T00:00:00Z')"
            )
            source.execute(
                "INSERT INTO import_content_fingerprints VALUES "
                "(2,'other-domain','other-batch',?,?,?,?,?,0,'completed',1,'2026-09-01 10:01:00')",
                ("1" * 64, "{}", "2" * 64, "3" * 64, "4" * 64),
            )
            source.commit()
        finally:
            source.close()

        cutover_id = "customer-service-retirement-test"
        call_command(
            "customer_service_write_authority",
            source=str(self.source),
            prepare=True,
            approved_run_id=run_id,
            cutover_id=cutover_id,
            stdout=StringIO(),
        )
        call_command(
            "customer_service_write_authority",
            source=str(self.source),
            activate=True,
            approved_run_id=run_id,
            cutover_id=cutover_id,
            stdout=StringIO(),
        )
        return run_id, source_digest, cutover_id

    def test_terminal_retirement_binds_r2_evidence_and_preserves_shared_rows(self) -> None:
        run_id, source_digest, cutover_id = self._migrate_and_activate()
        smoke = self.root / "smoke.json"
        smoke.write_text(
            json.dumps(
                {
                    "version": "customer-service-system-test-receipt-v1",
                    "status": "passed",
                    "cutoverId": cutover_id,
                    "migrationRunId": run_id,
                    "sourceDigest": source_digest,
                    "targetDigest": source_digest,
                    "workerBuildSha256": "a" * 64,
                    "checks": CHECKS,
                    "recordedAt": timezone.now().isoformat(),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        r2 = self.root / "r2.json"
        r2.write_text(
            json.dumps(
                {
                    "version": "customer-service-r2-retirement-evidence-v1",
                    "status": "passed",
                    "prefix": "inventory-upload/",
                    "objectCount": 0,
                    "objectBytes": 0,
                    "multipartUploadCount": 0,
                    "multipartPartCount": 0,
                    "objectsDigest": "b" * 64,
                    "sourcePathSha256": "c" * 64,
                    "recordedAt": timezone.now().isoformat(),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        planned = StringIO()
        call_command(
            "retire_customer_service_d1",
            source=str(self.source),
            cutover_id=cutover_id,
            approved_run_id=run_id,
            smoke_receipt=str(smoke),
            r2_evidence=str(r2),
            stdout=planned,
        )
        plan = json.loads(planned.getvalue())
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["legacyUploads"], {"sessions": 1, "active": 0, "chunks": 0, "results": 1})
        self.assertRegex(plan["r2EvidenceSha256"], r"^[0-9a-f]{64}$")

        audit = self.root / "retirement-audit.json"
        applied = StringIO()
        call_command(
            "retire_customer_service_d1",
            source=str(self.source),
            cutover_id=cutover_id,
            approved_run_id=run_id,
            smoke_receipt=str(smoke),
            r2_evidence=str(r2),
            apply=True,
            approved_plan_id=plan["planId"],
            audit_output=str(audit),
            stdout=applied,
        )
        result = json.loads(applied.getvalue())
        self.assertEqual(result["status"], "retired")
        self.assertTrue(audit.is_file())

        source = sqlite3.connect(self.source)
        try:
            for name in (
                "customer_service_import_batches",
                "customer_service_conversations",
                "customer_service_conversation_versions",
                "customer_service_deletion_audits",
                "customer_service_write_authority",
            ):
                self.assertEqual(source.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0], 0)
            self.assertEqual(
                source.execute(
                    "SELECT status FROM domain_retirement_receipts WHERE domain='customer-service'"
                ).fetchone()[0],
                "completed",
            )
            self.assertEqual(
                source.execute(
                    "SELECT COUNT(*) FROM inventory_import_uploads WHERE id='customer-upload'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                source.execute(
                    "SELECT COUNT(*) FROM inventory_import_uploads WHERE id='other-upload'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                source.execute(
                    "SELECT COUNT(*) FROM import_content_fingerprints WHERE domain='other-domain'"
                ).fetchone()[0],
                1,
            )
            with self.assertRaisesRegex(sqlite3.DatabaseError, "customer_service_domain_retired"):
                source.execute(
                    "INSERT INTO import_content_fingerprints "
                    "(sequence,domain,batch_id,scope_key,scope_json,import_hash,raw_file_hash,content_hash,row_count,status,publication_sequence,created_at) "
                    "VALUES (3,'customer-service','x','x','{}','x','x','x',0,'completed',1,CURRENT_TIMESTAMP)"
                )
        finally:
            source.close()

    def test_r2_evidence_requires_the_entire_retired_prefix_to_be_empty(self) -> None:
        r2_root = self.root / "r2" / "miniflare-R2BucketObject"
        r2_root.mkdir(parents=True)
        metadata = r2_root / "objects.sqlite"
        connection = sqlite3.connect(metadata)
        try:
            connection.executescript(
                """
                CREATE TABLE _mf_objects (key TEXT, size INTEGER, etag TEXT, version TEXT, blob_id TEXT);
                CREATE TABLE _mf_multipart_uploads (key TEXT);
                CREATE TABLE _mf_multipart_parts (object_key TEXT);
                """
            )
            connection.commit()
        finally:
            connection.close()
        tool = (
            Path(__file__).resolve().parents[3]
            / "tools"
            / "customer-service-r2-retirement-evidence.py"
        )
        output = self.root / "r2-empty.json"
        passed = subprocess.run(
            [sys.executable, str(tool), "--r2-root", str(r2_root), "--output", str(output)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["objectCount"], 0)

        connection = sqlite3.connect(metadata)
        try:
            connection.execute(
                "INSERT INTO _mf_objects VALUES ('inventory-upload/orphan',1,'e','v','b')"
            )
            connection.commit()
        finally:
            connection.close()
        blocked_output = self.root / "r2-nonempty.json"
        blocked = subprocess.run(
            [
                sys.executable,
                str(tool),
                "--r2-root",
                str(r2_root),
                "--output",
                str(blocked_output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(blocked.returncode, 1)
        self.assertFalse(blocked_output.exists())
