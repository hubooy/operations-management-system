from __future__ import annotations

import hashlib
import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from sales.authority import (
    SalesWriteAuthorityError,
    activate_write_authority,
    disable_write_authority,
    prepare_write_authority,
)
from sales.cutover_attestation import (
    ATTESTATION_VERSION,
    EXPECTED_D1_AUTHORITY_SCHEMA_SHA256,
)
from sales.models import SalesCutoverAttestation, SalesWriteAuthority
from sales.tests.cutover_fixtures import install_migration_run_evidence


CUTOVER_ID = "sales-cutover-20260828"


def create_attestation() -> SalesCutoverAttestation:
    observed_at = timezone.now()
    migration = install_migration_run_evidence("1" * 64)
    cleanup = {
        "manifestId": "4" * 64,
        "manifestSha256": "5" * 64,
        "sessionCount": 0,
        "objectCount": 0,
        "coreEvidenceSha256": "6" * 64,
        "lockedVerifyRunId": migration["verifyRunId"],
        "completedAt": observed_at.isoformat(),
    }
    payload = {
        "schemaVersion": ATTESTATION_VERSION,
        "cutoverId": CUTOVER_ID,
        "observedAt": observed_at.isoformat(),
        "d1Authority": {
            "owner": "postgresql",
            "epoch": 3,
            "updatedAt": observed_at.isoformat(),
        },
        "d1Blockers": {
            "processingBatches": 0,
            "activeUploads": 0,
            "uploadChunks": 0,
            "processingFingerprints": 0,
            "processingAttempts": 0,
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
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return SalesCutoverAttestation.objects.create(
        cutover_id=CUTOVER_ID,
        d1_authority_epoch=3,
        source_path_digest="1" * 64,
        migration_apply_run_id=migration["applyRunId"],
        migration_verify_run_id=migration["verifyRunId"],
        cleanup_manifest_id=cleanup["manifestId"],
        cleanup_manifest_sha256=cleanup["manifestSha256"],
        payload=payload,
        payload_sha256=digest,
        observed_at=observed_at,
    )


class SalesWriteAuthorityTests(TestCase):
    def test_migration_bootstraps_fail_closed_pending_singleton(self) -> None:
        authority = SalesWriteAuthority.objects.get(id=1)
        self.assertEqual(authority.status, "pending")
        self.assertEqual(authority.cutover_id, "")
        self.assertIsNone(authority.activated_at)

    def test_authority_mutation_acquires_exclusive_fence(self) -> None:
        initial = SalesWriteAuthority.objects.get(id=1)
        with patch(
            "sales.authority.acquire_sales_write_authority_exclusive_lock"
        ) as acquire:
            prepare_write_authority(
                expected_epoch=str(initial.authority_epoch),
                cutover_id=CUTOVER_ID,
            )
        acquire.assert_called_once_with()

    def test_cas_transitions_rotate_epoch_and_disabled_is_terminal(self) -> None:
        initial = SalesWriteAuthority.objects.get(id=1)
        initial_epoch = str(initial.authority_epoch)

        prepared = prepare_write_authority(
            expected_epoch=initial_epoch,
            cutover_id=CUTOVER_ID,
        )
        self.assertEqual(prepared["status"], "pending")
        self.assertNotEqual(prepared["authorityEpoch"], initial_epoch)
        self.assertEqual(prepared["cutoverId"], CUTOVER_ID)

        with self.assertRaisesRegex(SalesWriteAuthorityError, "epoch"):
            activate_write_authority(
                expected_epoch=initial_epoch,
                cutover_id=CUTOVER_ID,
                attestation_sha256="0" * 64,
            )
        with self.assertRaisesRegex(SalesWriteAuthorityError, "已经完成"):
            prepare_write_authority(
                expected_epoch=str(prepared["authorityEpoch"]),
                cutover_id=CUTOVER_ID,
            )

        with self.assertRaisesRegex(SalesWriteAuthorityError, "attestation"):
            activate_write_authority(
                expected_epoch=str(prepared["authorityEpoch"]),
                cutover_id=CUTOVER_ID,
                attestation_sha256="0" * 64,
            )
        attestation = create_attestation()
        active = activate_write_authority(
            expected_epoch=str(prepared["authorityEpoch"]),
            cutover_id=CUTOVER_ID,
            attestation_sha256=attestation.payload_sha256,
        )
        self.assertEqual(active["status"], "active")
        self.assertNotEqual(active["authorityEpoch"], prepared["authorityEpoch"])
        self.assertIsNotNone(active["activatedAt"])

        with self.assertRaisesRegex(SalesWriteAuthorityError, "只有 pending"):
            activate_write_authority(
                expected_epoch=str(active["authorityEpoch"]),
                cutover_id=CUTOVER_ID,
                attestation_sha256=attestation.payload_sha256,
            )

        disabled = disable_write_authority(
            expected_epoch=str(active["authorityEpoch"]),
            cutover_id=CUTOVER_ID,
        )
        self.assertEqual(disabled["status"], "disabled")
        self.assertNotEqual(disabled["authorityEpoch"], active["authorityEpoch"])

        with self.assertRaisesRegex(SalesWriteAuthorityError, "只有 active"):
            disable_write_authority(
                expected_epoch=str(disabled["authorityEpoch"]),
                cutover_id=CUTOVER_ID,
            )
        with self.assertRaisesRegex(SalesWriteAuthorityError, "只有 pending"):
            activate_write_authority(
                expected_epoch=str(disabled["authorityEpoch"]),
                cutover_id=CUTOVER_ID,
                attestation_sha256=attestation.payload_sha256,
            )

    def test_management_command_requires_current_epoch_and_cutover(self) -> None:
        initial = SalesWriteAuthority.objects.get(id=1)
        output = StringIO()
        call_command(
            "sales_write_authority",
            "prepare",
            expected_epoch=str(initial.authority_epoch),
            cutover_id=CUTOVER_ID,
            stdout=output,
        )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["cutoverId"], CUTOVER_ID)
        with self.assertRaises(CommandError):
            call_command(
                "sales_write_authority",
                "activate",
                expected_epoch=str(initial.authority_epoch),
                cutover_id=CUTOVER_ID,
            )

        prepared = SalesWriteAuthority.objects.get(id=1)
        with self.assertRaisesRegex(CommandError, "attestation"):
            call_command(
                "sales_write_authority",
                "activate",
                expected_epoch=str(prepared.authority_epoch),
                cutover_id=CUTOVER_ID,
            )
