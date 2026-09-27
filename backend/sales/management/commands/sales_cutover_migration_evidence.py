from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from sales.cutover_attestation import (
    SalesCutoverAttestationError,
    validate_postgresql_migration_evidence,
)


class Command(BaseCommand):
    help = "Validate the explicit v4 apply/verify pair before any legacy R2 deletion."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--migration-apply-run-id", required=True)
        parser.add_argument("--migration-verify-run-id", required=True)

    def handle(self, *args, **options):
        try:
            evidence = validate_postgresql_migration_evidence(
                source=options["source"],
                migration_apply_run_id=options["migration_apply_run_id"],
                migration_verify_run_id=options["migration_verify_run_id"],
            )
        except SalesCutoverAttestationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps({
            "status": "verified",
            "migrationApplyRunId": evidence["applyRunId"],
            "migrationVerifyRunId": evidence["verifyRunId"],
        }, ensure_ascii=False, sort_keys=True))
