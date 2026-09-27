from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from sales.cutover_attestation import (
    SalesCutoverAttestationError,
    record_d1_terminal_attestation,
    save_attestation_file,
)


class Command(BaseCommand):
    help = "Verify D1 terminal authority and persist a PostgreSQL/file attestation."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--cutover-id", required=True)
        parser.add_argument("--audit-dir", required=True)
        parser.add_argument("--migration-apply-run-id", required=True)
        parser.add_argument("--migration-verify-run-id", required=True)
        parser.add_argument("--cleanup-manifest", required=True)

    def handle(self, *args, **options):
        try:
            attestation = record_d1_terminal_attestation(
                source=options["source"],
                cutover_id=options["cutover_id"],
                migration_apply_run_id=options["migration_apply_run_id"],
                migration_verify_run_id=options["migration_verify_run_id"],
                cleanup_manifest=options["cleanup_manifest"],
            )
            destination = save_attestation_file(
                attestation,
                audit_directory=options["audit_dir"],
            )
        except SalesCutoverAttestationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            json.dumps(
                {
                    "status": "attested",
                    "cutoverId": attestation.cutover_id,
                    "d1AuthorityEpoch": attestation.d1_authority_epoch,
                    "payloadSha256": attestation.payload_sha256,
                    "attestationPath": str(destination),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
