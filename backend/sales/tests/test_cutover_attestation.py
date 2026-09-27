from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from sales.authority import activate_write_authority, prepare_write_authority
from sales.cutover_attestation import (
    ATTESTED_MIGRATION_FORMAT_VERSION,
    SalesCutoverAttestationError,
    _legacy_manifest_digest,
    _payload_sha256,
    record_d1_terminal_attestation,
    require_valid_cutover_attestation,
    save_attestation_file,
)
from sales.models import (
    ErpProductMaster,
    SalesCutoverAttestation,
    SalesDataRevision,
    SalesLegacyUploadAudit,
    SalesMigrationRun,
    SalesWriteAuthority,
)
from erp_reference.models import ErpReferenceSyncCheckpoint
from sales.management.commands import migrate_sales_from_d1 as migration_command
from sales.tests.cutover_fixtures import APPLY_RUN_ID, install_cutover_evidence


CUTOVER_ID = "cutover-attestation-test"


def create_terminal_d1(
    path: Path,
    *,
    owner: str = "postgresql",
    cutover_id: str = CUTOVER_ID,
    with_chunk: bool = False,
    with_zero_chunk_upload: bool = False,
):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE sales_order_lines (id INTEGER PRIMARY KEY);
        CREATE TABLE sales_import_batches (id TEXT PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE sales_import_uploads (
          id TEXT PRIMARY KEY, status TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE sales_import_upload_chunks (
          upload_id TEXT, chunk_index INTEGER, object_key TEXT, size_bytes INTEGER, sha256 TEXT
        );
        CREATE TABLE sales_overview_cache_state (
          id INTEGER PRIMARY KEY, sales_revision INTEGER, erp_product_revision INTEGER
        );
        INSERT INTO sales_overview_cache_state VALUES (1, 8, 5);
        CREATE TABLE sales_projection_source_state (id INTEGER PRIMARY KEY, source_epoch TEXT);
        INSERT INTO sales_projection_source_state VALUES (1, 'legacy');
        CREATE TABLE sales_overview_response_cache (cache_key TEXT PRIMARY KEY);
        CREATE TABLE sales_projection_outbox (
          event_sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL
        );
        CREATE TABLE import_content_fingerprints (
          sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL, status TEXT NOT NULL
        );
        CREATE TABLE import_content_attempts (
          sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL, outcome TEXT NOT NULL
        );
        CREATE TABLE import_scope_heads (
          domain TEXT NOT NULL, scope_key TEXT NOT NULL, status TEXT NOT NULL
        );
        """
    )
    migration = (
        Path(__file__).resolve().parents[3]
        / "drizzle"
        / "0090_sales_write_authority.sql"
    ).read_text(encoding="utf-8")
    connection.executescript(migration.replace("--> statement-breakpoint", ""))
    if with_chunk:
        connection.execute(
            "INSERT INTO sales_import_upload_chunks VALUES "
            "('expired',0,'sales-upload/expired/part',1,?)",
            ("a" * 64,),
        )
    if with_zero_chunk_upload:
        connection.execute(
            "INSERT INTO sales_import_uploads VALUES (?, 'completed', ?)",
            ("omitted-zero-chunk", "2020-01-01T00:00:00Z"),
        )
    if owner in {"pending", "postgresql"}:
        connection.execute(
            "UPDATE sales_write_authority SET owner='pending',epoch=2,cutover_id=? "
            "WHERE id=1",
            (cutover_id,),
        )
    if owner == "postgresql":
        connection.execute(
            "UPDATE sales_write_authority SET owner='postgresql',epoch=3,cutover_id=? "
            "WHERE id=1",
            (cutover_id,),
        )
    connection.commit()
    connection.close()


class SalesCutoverAttestationTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.sqlite"
        create_terminal_d1(self.source)
        authority = SalesWriteAuthority.objects.get(id=1)
        self.prepared = prepare_write_authority(
            expected_epoch=str(authority.authority_epoch),
            cutover_id=CUTOVER_ID,
        )
        verify_run_id, cleanup_manifest = install_cutover_evidence(
            self.source, self.root, CUTOVER_ID
        )
        self.evidence_args = {
            "migration_apply_run_id": APPLY_RUN_ID,
            "migration_verify_run_id": verify_run_id,
            "cleanup_manifest": cleanup_manifest,
        }

    def test_terminal_d1_is_attested_and_required_for_activation(self) -> None:
        attestation = record_d1_terminal_attestation(
            source=self.source,
            cutover_id=CUTOVER_ID,
            **self.evidence_args,
        )
        payload = require_valid_cutover_attestation(
            cutover_id=CUTOVER_ID,
            payload_sha256=attestation.payload_sha256,
        )
        self.assertEqual(payload["d1Authority"]["owner"], "postgresql")
        self.assertEqual(payload["d1Blockers"]["uploadChunks"], 0)
        destination = save_attestation_file(
            attestation,
            audit_directory=self.root / "audit",
        )
        envelope = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(envelope["payloadSha256"], attestation.payload_sha256)

        active = activate_write_authority(
            expected_epoch=str(self.prepared["authorityEpoch"]),
            cutover_id=CUTOVER_ID,
            attestation_sha256=attestation.payload_sha256,
        )
        self.assertEqual(active["status"], "active")

    def test_record_is_idempotent_and_repairs_a_missing_audit_file(self) -> None:
        first = record_d1_terminal_attestation(
            source=self.source,
            cutover_id=CUTOVER_ID,
            **self.evidence_args,
        )
        second = record_d1_terminal_attestation(
            source=self.source,
            cutover_id=CUTOVER_ID,
            **self.evidence_args,
        )
        self.assertEqual(first.payload_sha256, second.payload_sha256)
        destination = save_attestation_file(second, audit_directory=self.root / "audit")
        destination.unlink()
        repaired = save_attestation_file(second, audit_directory=self.root / "audit")
        self.assertTrue(repaired.exists())

    def test_same_path_replaced_d1_cannot_reuse_old_attestation(self) -> None:
        record_d1_terminal_attestation(
            source=self.source,
            cutover_id=CUTOVER_ID,
            **self.evidence_args,
        )
        self.source.unlink()
        create_terminal_d1(self.source)
        with self.assertRaisesRegex(SalesCutoverAttestationError, "完整 cutover 证据"):
            record_d1_terminal_attestation(
                source=self.source,
                cutover_id=CUTOVER_ID,
                **self.evidence_args,
            )

    def test_wrong_d1_owner_or_any_chunk_fails_closed(self) -> None:
        self.source.unlink()
        create_terminal_d1(self.source, owner="pending")
        with self.assertRaisesRegex(SalesCutoverAttestationError, "尚未"):
            record_d1_terminal_attestation(source=self.source, cutover_id=CUTOVER_ID, **self.evidence_args)

        self.source.unlink()
        create_terminal_d1(self.source, with_chunk=True)
        with self.assertRaisesRegex(SalesCutoverAttestationError, "blocker"):
            record_d1_terminal_attestation(source=self.source, cutover_id=CUTOVER_ID, **self.evidence_args)

    def test_same_name_noop_trigger_cannot_forge_terminal_proof(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.executescript(
            """
            DROP TRIGGER sales_authority_order_lines_insert;
            CREATE TRIGGER sales_authority_order_lines_insert
              BEFORE INSERT ON sales_order_lines BEGIN SELECT 1; END;
            """
        )
        connection.close()
        with self.assertRaisesRegex(SalesCutoverAttestationError, "语义"):
            record_d1_terminal_attestation(source=self.source, cutover_id=CUTOVER_ID, **self.evidence_args)

    def test_tampered_database_attestation_cannot_activate(self) -> None:
        attestation = record_d1_terminal_attestation(
            source=self.source,
            cutover_id=CUTOVER_ID,
            **self.evidence_args,
        )
        tampered = dict(attestation.payload)
        tampered["cutoverId"] = "some-other-cutover"
        SalesCutoverAttestation.objects.filter(cutover_id=CUTOVER_ID).update(
            payload=tampered
        )
        with self.assertRaisesRegex(Exception, "attestation"):
            activate_write_authority(
                expected_epoch=str(self.prepared["authorityEpoch"]),
                cutover_id=CUTOVER_ID,
                attestation_sha256=attestation.payload_sha256,
            )

    def test_strict_payload_rejects_missing_blocker_or_wrong_schema_even_when_rehashed(self) -> None:
        attestation = record_d1_terminal_attestation(
            source=self.source, cutover_id=CUTOVER_ID, **self.evidence_args
        )
        for mutation in ("blocker", "schema"):
            payload = json.loads(json.dumps(attestation.payload))
            if mutation == "blocker":
                del payload["d1Blockers"]["uploadChunks"]
            else:
                payload["source"]["authoritySchemaSha256"] = "0" * 64
            SalesCutoverAttestation.objects.filter(cutover_id=CUTOVER_ID).update(
                payload=payload,
                payload_sha256=_payload_sha256(payload),
            )
            with self.assertRaises(SalesCutoverAttestationError):
                require_valid_cutover_attestation(cutover_id=CUTOVER_ID)
            SalesCutoverAttestation.objects.filter(cutover_id=CUTOVER_ID).update(
                payload=attestation.payload,
                payload_sha256=attestation.payload_sha256,
            )

    def test_empty_or_stale_postgresql_and_missing_cleanup_cannot_attest(self) -> None:
        SalesMigrationRun.objects.all().delete()
        with self.assertRaisesRegex(SalesCutoverAttestationError, "verify run"):
            record_d1_terminal_attestation(
                source=self.source, cutover_id=CUTOVER_ID, **self.evidence_args
            )

        verify_run_id, cleanup_manifest = install_cutover_evidence(
            self.source, self.root / "second", CUTOVER_ID
        )
        SalesDataRevision.objects.filter(domain="sales").update(revision=9)
        with self.assertRaisesRegex(SalesCutoverAttestationError, "revision"):
            record_d1_terminal_attestation(
                source=self.source,
                cutover_id=CUTOVER_ID,
                migration_apply_run_id=APPLY_RUN_ID,
                migration_verify_run_id=verify_run_id,
                cleanup_manifest=cleanup_manifest,
            )
        SalesDataRevision.objects.filter(domain="sales").update(revision=8)
        cleanup_manifest.unlink()
        with self.assertRaisesRegex(SalesCutoverAttestationError, "cleanup manifest"):
            record_d1_terminal_attestation(
                source=self.source,
                cutover_id=CUTOVER_ID,
                migration_apply_run_id=APPLY_RUN_ID,
                migration_verify_run_id=verify_run_id,
                cleanup_manifest=cleanup_manifest,
            )

    def test_runtime_validation_does_not_pin_live_sales_to_cutover_baseline(self) -> None:
        attestation = record_d1_terminal_attestation(
            source=self.source, cutover_id=CUTOVER_ID, **self.evidence_args
        )
        activate_write_authority(
            expected_epoch=str(self.prepared["authorityEpoch"]),
            cutover_id=CUTOVER_ID,
            attestation_sha256=attestation.payload_sha256,
        )
        SalesDataRevision.objects.filter(domain="sales").update(
            revision=9, source_digest="7" * 64
        )
        payload = require_valid_cutover_attestation(cutover_id=CUTOVER_ID)
        self.assertEqual(payload["postgresqlMigration"]["sourceRevision"], "8:5")

    def test_runtime_attestation_v2_remains_bound_to_fixed_v4_after_future_migrator_upgrade(self) -> None:
        self.assertEqual(ATTESTED_MIGRATION_FORMAT_VERSION, "sales-projection-v4")
        attestation = record_d1_terminal_attestation(
            source=self.source, cutover_id=CUTOVER_ID, **self.evidence_args
        )
        with patch.object(
            migration_command,
            "CANONICAL_FORMAT_VERSION",
            "sales-projection-v5",
        ):
            payload = require_valid_cutover_attestation(
                cutover_id=CUTOVER_ID,
                payload_sha256=attestation.payload_sha256,
            )
        self.assertEqual(
            payload["postgresqlMigration"]["canonicalFormatVersion"],
            "sales-projection-v4",
        )

    def test_fresh_existing_erp_checkpoint_is_the_live_activation_digest_contract(self) -> None:
        SalesMigrationRun.objects.all().delete()
        SalesDataRevision.objects.all().delete()
        ErpProductMaster.objects.create(
            product_code="ERP-CUTOVER-1",
            product_name="Cutover ERP baseline",
            source_row_number=1,
            last_import_batch_id="erp-cutover-baseline",
            created_at="2026-08-28 00:00:00",
            updated_at="2026-08-28 00:00:00",
            migration_generation="g" * 32,
        )
        verify_run_id, cleanup_manifest = install_cutover_evidence(
            self.source, self.root / "erp-checkpoint", CUTOVER_ID
        )
        source_digest = hashlib.sha256(
            str(self.source.resolve()).encode("utf-8")
        ).hexdigest()
        checkpoint_digest = "7" * 64
        ErpReferenceSyncCheckpoint.objects.create(
            id=1,
            source_epoch="8" * 32,
            source_path_digest=source_digest,
            last_event_sequence=0,
            last_event_id="",
            erp_revision=5,
            content_hash=checkpoint_digest,
            row_count=1,
            source_batch_id="erp-cutover-baseline",
        )
        SalesDataRevision.objects.filter(domain="erp").update(
            revision=5,
            source_digest=checkpoint_digest,
        )
        attestation = record_d1_terminal_attestation(
            source=self.source,
            cutover_id=CUTOVER_ID,
            migration_apply_run_id=APPLY_RUN_ID,
            migration_verify_run_id=verify_run_id,
            cleanup_manifest=cleanup_manifest,
        )
        self.assertEqual(
            attestation.payload["postgresqlMigration"]["targetCounts"][
                "erp_product_master"
            ],
            1,
        )

    def test_cleanup_manifest_cannot_omit_a_zero_chunk_upload_session(self) -> None:
        self.source.unlink()
        create_terminal_d1(self.source, with_zero_chunk_upload=True)
        with self.assertRaisesRegex(SalesCutoverAttestationError, "仍有上传会话"):
            record_d1_terminal_attestation(
                source=self.source, cutover_id=CUTOVER_ID, **self.evidence_args
            )

    def test_cleanup_chunk_created_at_must_match_archived_manifest_digest(self) -> None:
        SalesMigrationRun.objects.all().delete()
        SalesDataRevision.objects.all().delete()
        now = timezone.now()
        created = now - timedelta(days=3)
        updated = now - timedelta(days=2)
        expires = now - timedelta(days=1)
        upload_id = "archived-session-001"
        chunk = {
            "uploadId": upload_id,
            "chunkIndex": 0,
            "objectKey": f"sales-upload/{upload_id}/0-{'a' * 64}-part",
            "sizeBytes": 10,
            "sha256": "a" * 64,
            "createdAt": updated.isoformat(),
        }
        SalesLegacyUploadAudit.objects.create(
            source_upload_id=upload_id,
            source_fingerprint_sha256="1" * 64,
            file_name_sha256="2" * 64,
            file_size_bytes=10,
            chunk_size_bytes=10,
            declared_chunk_count=1,
            declared_received_chunk_count=1,
            declared_received_bytes=10,
            source_status="ready",
            archive_reason="expired",
            source_created_at=created,
            source_updated_at=updated,
            source_expires_at=expires,
            manifest_chunk_count=1,
            manifest_bytes=10,
            manifest_sha256=_legacy_manifest_digest(upload_id, [chunk]),
            migration_generation="g" * 32,
        )
        session = {
            "id": upload_id,
            "status": "ready",
            "fileSizeBytes": 10,
            "chunkSizeBytes": 10,
            "chunkCount": 1,
            "receivedChunkCount": 1,
            "receivedBytes": 10,
            "createdAt": created.isoformat(),
            "updatedAt": updated.isoformat(),
            "expiresAt": expires.isoformat(),
        }
        verify_id, manifest_path = install_cutover_evidence(
            self.source,
            self.root / "manifest-digest",
            CUTOVER_ID,
            sessions=[session],
            objects=[chunk],
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["objects"][0]["createdAt"] = (updated + timedelta(seconds=1)).isoformat()
        identity_keys = (
            "version", "cutoverId", "sourcePathDigest", "bucket", "persistPathDigest",
            "plannedAt", "sessions", "objects", "coreEvidence",
        )
        identity = {key: manifest[key] for key in identity_keys}
        manifest["manifestId"] = _payload_sha256(identity)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SalesCutoverAttestationError, "legacy audit"):
            record_d1_terminal_attestation(
                source=self.source,
                cutover_id=CUTOVER_ID,
                migration_apply_run_id=APPLY_RUN_ID,
                migration_verify_run_id=verify_id,
                cleanup_manifest=manifest_path,
            )
