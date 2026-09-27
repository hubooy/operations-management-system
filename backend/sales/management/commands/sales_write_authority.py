from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from sales.authority import (
    SalesWriteAuthorityError,
    activate_write_authority,
    disable_write_authority,
    prepare_write_authority,
    read_write_authority,
)


class Command(BaseCommand):
    help = "Inspect or CAS-transition the PostgreSQL sales write-authority gate."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "action", choices=["status", "prepare", "activate", "disable"]
        )
        parser.add_argument("--expected-epoch", default="")
        parser.add_argument("--cutover-id", default="")
        parser.add_argument("--attestation-sha256", default="")

    def handle(self, *args, **options):
        action = options["action"]
        try:
            if action == "status":
                payload = read_write_authority()
            else:
                if not options["expected_epoch"] or not options["cutover_id"]:
                    raise CommandError("变更写入权威必须同时提供 --expected-epoch 和 --cutover-id")
                callback = {
                    "prepare": prepare_write_authority,
                    "activate": activate_write_authority,
                    "disable": disable_write_authority,
                }[action]
                arguments = {
                    "expected_epoch": options["expected_epoch"],
                    "cutover_id": options["cutover_id"],
                }
                if action == "activate":
                    if not options["attestation_sha256"]:
                        raise CommandError(
                            "activate 必须提供 --attestation-sha256 证明 D1 terminal owner"
                        )
                    arguments["attestation_sha256"] = options[
                        "attestation_sha256"
                    ]
                elif options["attestation_sha256"]:
                    raise CommandError(
                        "--attestation-sha256 只能与 activate 同时使用"
                    )
                payload = callback(**arguments)
        except SalesWriteAuthorityError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
