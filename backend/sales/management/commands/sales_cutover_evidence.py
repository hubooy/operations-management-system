from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from sales.cutover_attestation import (
    SalesCutoverAttestationError,
    validate_postgresql_cutover_evidence,
)


class Command(BaseCommand):
    help = "Fail-closed pre-terminal check of PG v4 and atomic legacy-cleanup evidence."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--cutover-id", required=True)
        parser.add_argument("--migration-apply-run-id", required=True)
        parser.add_argument("--migration-verify-run-id", required=True)
        parser.add_argument("--cleanup-manifest", required=True)

    def handle(self, *args, **options):
        try:
            evidence = validate_postgresql_cutover_evidence(
                source=options["source"],
                cutover_id=options["cutover_id"],
                migration_apply_run_id=options["migration_apply_run_id"],
                migration_verify_run_id=options["migration_verify_run_id"],
                cleanup_manifest=options["cleanup_manifest"],
            )
        except SalesCutoverAttestationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps({
            "status": "verified",
            "migrationApplyRunId": evidence["postgresqlMigration"]["applyRunId"],
            "migrationVerifyRunId": evidence["postgresqlMigration"]["verifyRunId"],
            "cleanupManifestId": evidence["legacyCleanup"]["manifestId"],
        }, ensure_ascii=False, sort_keys=True))
