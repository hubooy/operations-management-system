from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from sales.retirement_preflight import (
    RetirementPreflightError,
    verify_retirement_preflight,
)


class Command(BaseCommand):
    help = "Verify live PostgreSQL authority, immutable attestation and a fresh smoke receipt."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--plan-id", required=True)
        parser.add_argument("--cutover-id", required=True)
        parser.add_argument("--attestation-sha256", required=True)
        parser.add_argument("--smoke-receipt", required=True)
        parser.add_argument("--smoke-receipt-sha256", required=True)

    def handle(self, *args, **options):
        try:
            result = verify_retirement_preflight(
                plan_id=options["plan_id"],
                cutover_id=options["cutover_id"],
                attestation_sha256=options["attestation_sha256"],
                smoke_receipt_path=options["smoke_receipt"],
                smoke_receipt_sha256=options["smoke_receipt_sha256"],
            )
        except RetirementPreflightError as error:
            raise CommandError(str(error)) from None
        except Exception:
            # Database drivers and filesystem errors may embed connection strings,
            # SQL values, or absolute paths.  Keep unexpected diagnostics out of
            # both the CLI and any operator audit pipeline.
            raise CommandError("retirement preflight 内部验证失败") from None
        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
