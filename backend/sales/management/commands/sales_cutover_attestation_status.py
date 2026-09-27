from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from sales.cutover_attestation import (
    SalesCutoverAttestationError,
    require_valid_cutover_attestation,
    save_attestation_file,
)
from sales.models import SalesCutoverAttestation


class Command(BaseCommand):
    help = "Lightweight validation/recovery of an existing immutable cutover attestation."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--cutover-id", required=True)
        parser.add_argument("--audit-dir", required=True)

    def handle(self, *args, **options):
        try:
            require_valid_cutover_attestation(cutover_id=options["cutover_id"])
            attestation = SalesCutoverAttestation.objects.get(
                cutover_id=options["cutover_id"]
            )
            destination = save_attestation_file(
                attestation, audit_directory=options["audit_dir"]
            )
        except (SalesCutoverAttestationError, SalesCutoverAttestation.DoesNotExist) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps({
            "status": "valid",
            "cutoverId": attestation.cutover_id,
            "payloadSha256": attestation.payload_sha256,
            "attestationPath": str(destination),
        }, ensure_ascii=False, sort_keys=True))
