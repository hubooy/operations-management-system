from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from django.utils import timezone

from sales.cutover_attestation import (
    ATTESTATION_VERSION,
    CLEANUP_MANIFEST_VERSION,
    EXPECTED_D1_AUTHORITY_SCHEMA_SHA256,
    _canonical_json,
    _read_d1_core_evidence,
    _target_snapshot,
)
from sales.management.commands.migrate_sales_from_d1 import CANONICAL_FORMAT_VERSION
from sales.models import (
    ErpProductMaster,
    SalesCutoverAttestation,
    SalesDataRevision,
    SalesMigrationRun,
)
from erp_reference.import_service import (
    SOURCE_LABELS,
    SOURCE_SCOPE_KEYS,
    combined_database_digest,
    combo_rows_from_database,
    content_hash,
    product_rows_from_database,
)
from erp_reference.models import (
    ErpReferenceImportBatch,
    ErpReferenceImportScopeHead,
    ErpReferenceWriteAuthority,
)


DRY_RUN_ID = "d" * 32
APPLY_RUN_ID = "a" * 32
VERIFY_RUN_ID = "e" * 32


def install_lightweight_attestation(cutover_id: str) -> SalesCutoverAttestation:
    now = timezone.now()
    counts = {key: 0 for key in (
        "sales_import_batches", "erp_product_master", "sales_order_lines",
        "sales_query_projection", "import_content_fingerprints",
        "import_content_attempts", "import_scope_heads", "sales_import_uploads",
        "sales_import_upload_chunks",
    )}
    digests = {key: "3" * 64 for key in counts}
    migration = {
        "applyRunId": APPLY_RUN_ID,
        "verifyRunId": VERIFY_RUN_ID,
        "canonicalFormatVersion": "sales-projection-v4",
        "sourceRevision": "8:5",
        "targetCounts": counts,
        "targetDigests": digests,
    }
    cleanup = {
        "manifestId": "4" * 64,
        "manifestSha256": "5" * 64,
        "sessionCount": 0,
        "objectCount": 0,
        "coreEvidenceSha256": "6" * 64,
        "lockedVerifyRunId": VERIFY_RUN_ID,
        "completedAt": now.isoformat(),
    }
    payload = {
        "schemaVersion": ATTESTATION_VERSION,
        "cutoverId": cutover_id,
        "observedAt": now.isoformat(),
        "d1Authority": {"owner": "postgresql", "epoch": 3, "updatedAt": now.isoformat()},
        "d1Blockers": {
            "processingBatches": 0, "activeUploads": 0, "uploadChunks": 0,
            "processingFingerprints": 0, "processingAttempts": 0,
            "processingScopeHeads": 0,
        },
        "source": {
            "pathSha256": "1" * 64,
            "fileIdentitySha256": "2" * 64,
            "sizeBytes": 1,
            "authoritySchemaSha256": EXPECTED_D1_AUTHORITY_SCHEMA_SHA256,
        },
        "postgresqlMigration": migration,
        "legacyCleanup": cleanup,
    }
    return SalesCutoverAttestation.objects.create(
        cutover_id=cutover_id,
        d1_authority_epoch=3,
        source_path_digest="1" * 64,
        migration_apply_run_id=APPLY_RUN_ID,
        migration_verify_run_id=VERIFY_RUN_ID,
        cleanup_manifest_id=cleanup["manifestId"],
        cleanup_manifest_sha256=cleanup["manifestSha256"],
        payload=payload,
        payload_sha256=hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest(),
        observed_at=now,
    )


def install_writer_runtime_guard(cutover_id: str) -> SalesCutoverAttestation:
    attestation = install_lightweight_attestation(cutover_id)
    ErpProductMaster.objects.create(
        product_code="GUARD-ERP-1",
        product_name="Runtime guard fixture",
        source_row_number=1,
        last_import_batch_id="guard-baseline",
        created_at="2026-08-28 00:00:00",
        updated_at="2026-08-28 00:00:00",
    )
    rows_by_source = {
        "products": product_rows_from_database(),
        "combos": combo_rows_from_database(),
    }
    now = timezone.now()
    for source, rows in rows_by_source.items():
        batch_id = f"guard-{source}-baseline"
        digest = content_hash(source, rows)
        ErpReferenceImportBatch.objects.create(
            id=batch_id,
            source_key=source,
            source_label=SOURCE_LABELS[source],
            file_name=f"{source}.xlsx",
            file_size_bytes=1,
            file_hash=digest,
            raw_file_hash=digest,
            content_hash=digest,
            scope_key=SOURCE_SCOPE_KEYS[source],
            published_state_token=digest,
            sheet_name=source,
            status="completed",
            row_count=len(rows),
            inserted_count=len(rows),
            actor_email="fixture@example.test",
            completed_at=now,
        )
        ErpReferenceImportScopeHead.objects.filter(source_key=source).update(
            state_token=digest,
            status="ready",
            owner_token="",
            current_batch_id=batch_id,
            owner_started_at=None,
            heartbeat_at=None,
        )
    run_id = f"erp-reference-{uuid.uuid4().hex}"
    ErpReferenceWriteAuthority.objects.filter(id=1).update(
        status="postgres",
        authority_epoch=uuid.uuid4(),
        cutover_id="erp-reference-test-cutover",
        migration_verify_run_id=run_id,
        activated_at=now,
    )
    SalesDataRevision.objects.update_or_create(
        domain="erp",
        defaults={"revision": 5, "source_digest": combined_database_digest()},
    )
    return attestation


def install_migration_run_evidence(source_digest: str) -> dict[str, object]:
    SalesDataRevision.objects.update_or_create(
        domain="sales", defaults={"revision": 8, "source_digest": "1" * 64}
    )
    SalesDataRevision.objects.update_or_create(
        domain="erp", defaults={"revision": 5, "source_digest": "2" * 64}
    )
    counts, digests = _target_snapshot()
    from sales.management.commands.migrate_sales_from_d1 import _domain_digest

    SalesDataRevision.objects.filter(domain="sales").update(
        source_digest=_domain_digest("sales", digests)
    )
    SalesDataRevision.objects.filter(domain="erp").update(
        source_digest=_domain_digest("erp", digests)
    )
    now = timezone.now()
    common = {
        "source_fingerprint": "f" * 64,
        "source_path_digest": source_digest,
        "generation": "g" * 32,
        "source_revision": "8:5",
        "canonical_format_version": CANONICAL_FORMAT_VERSION,
        "source_counts": counts,
        "source_digests": digests,
        "completed_at": now,
    }
    SalesMigrationRun.objects.create(
        id=DRY_RUN_ID,
        status="dry_run_completed",
        dry_run=True,
        consumed_by_run_id=APPLY_RUN_ID,
        approval_consumed_at=now,
        **common,
    )
    SalesMigrationRun.objects.create(
        id=APPLY_RUN_ID,
        status="completed",
        dry_run=False,
        approved_run_id=DRY_RUN_ID,
        target_revision="8:5",
        target_counts=counts,
        target_digests=digests,
        **common,
    )
    SalesMigrationRun.objects.create(
        id=VERIFY_RUN_ID,
        status="verified",
        dry_run=False,
        target_revision="8:5",
        target_counts=counts,
        target_digests=digests,
        **common,
    )
    return {
        "applyRunId": APPLY_RUN_ID,
        "verifyRunId": VERIFY_RUN_ID,
        "canonicalFormatVersion": CANONICAL_FORMAT_VERSION,
        "sourceRevision": "8:5",
        "targetCounts": counts,
        "targetDigests": digests,
    }


def install_cutover_evidence(
    source: Path,
    root: Path,
    cutover_id: str,
    *,
    sessions: list[dict[str, object]] | None = None,
    objects: list[dict[str, object]] | None = None,
) -> tuple[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    source_digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()
    install_migration_run_evidence(source_digest)
    now = timezone.now()
    connection = sqlite3.connect(source)
    try:
        core = _read_d1_core_evidence(connection)
    finally:
        connection.close()
    stamp = now.isoformat()
    identity = {
        "version": CLEANUP_MANIFEST_VERSION,
        "cutoverId": cutover_id,
        "sourcePathDigest": source_digest,
        "bucket": "site-creator-r2",
        "persistPathDigest": "9" * 64,
        "plannedAt": stamp,
        "sessions": sessions or [],
        "objects": objects or [],
        "coreEvidence": core,
    }
    manifest = {
        **identity,
        "manifestId": hashlib.sha256(
            _canonical_json(identity).encode("utf-8")
        ).hexdigest(),
        "status": "completed",
        "verifiedMissingObjectKeys": sorted(
            str(item["objectKey"]) for item in (objects or [])
        ),
        "lockedApplyRunId": APPLY_RUN_ID,
        "lockedVerifyRunId": VERIFY_RUN_ID,
        "lockedVerifyRecordedAt": stamp,
        "metadataDeletedAt": stamp,
        "completedAt": stamp,
    }
    destination = root / "cleanup.json"
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return VERIFY_RUN_ID, destination
