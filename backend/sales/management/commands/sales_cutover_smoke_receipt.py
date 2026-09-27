from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from sales.smoke_receipt import (
    SmokeReceiptGenerationError,
    generate_smoke_receipt_bundle,
)


class Command(BaseCommand):
    help = "Run live loopback reader/writer smoke checks and atomically save a retirement receipt."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--plan-id", required=True)
        parser.add_argument("--cutover-id", required=True)
        parser.add_argument("--attestation-sha256", required=True)
        parser.add_argument("--output-directory", required=True)
        parser.add_argument("--reader-base-url", default="http://127.0.0.1:8001")
        parser.add_argument("--writer-base-url", default="http://127.0.0.1:8002")

    def handle(self, *args, **options):
        try:
            result = generate_smoke_receipt_bundle(
                plan_id=options["plan_id"],
                cutover_id=options["cutover_id"],
                attestation_sha256=options["attestation_sha256"],
                output_directory=options["output_directory"],
                reader_base_url=options["reader_base_url"],
                writer_base_url=options["writer_base_url"],
            )
        except SmokeReceiptGenerationError as error:
            raise CommandError(str(error)) from None
        except Exception:
            raise CommandError("sales cutover smoke receipt 生成失败") from None
        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
