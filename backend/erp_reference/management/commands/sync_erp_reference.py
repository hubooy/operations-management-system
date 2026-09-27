from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections

from erp_reference.sync import (
    ErpReferenceSyncError,
    initialize_checkpoint,
    inspect_sync_status,
    resolve_source_path,
    retry_source_changes,
    sync_reference_once,
)


class Command(BaseCommand):
    help = (
        "Consume the ERP-only D1 product-reference outbox into PostgreSQL. "
        "This command never reads or accepts sales events."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--source",
            required=True,
            help="Exact path to the authoritative, read-only D1 SQLite file",
        )
        parser.add_argument(
            "--initialize-checkpoint",
            action="store_true",
            help="Bind an already verified, identical D1/PG ERP baseline",
        )
        parser.add_argument("--watch", action="store_true")
        parser.add_argument(
            "--status",
            action="store_true",
            help="Read-only caught-up and heartbeat verification",
        )
        parser.add_argument("--max-age-seconds", type=float, default=60.0)
        parser.add_argument("--interval-seconds", type=float, default=15.0)
        parser.add_argument("--max-events", type=int, default=1000)
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--source-change-retries", type=int, default=3)
        parser.add_argument("--transient-db-retries", type=int, default=5)

    def _sync_with_retries(self, source: Path, options) -> dict[str, object]:
        transient_retries = int(options["transient_db_retries"])
        if transient_retries < 0 or transient_retries > 10:
            raise ErpReferenceSyncError("transient DB retry 次数必须在 0 到 10 之间")
        for attempt in range(transient_retries + 1):
            try:
                return retry_source_changes(
                    lambda: sync_reference_once(
                        source,
                        max_events=int(options["max_events"]),
                        batch_size=int(options["batch_size"]),
                    ),
                    attempts=int(options["source_change_retries"]),
                )
            except (OperationalError, sqlite3.OperationalError):
                if attempt >= transient_retries:
                    raise
                close_old_connections()
                time.sleep(min(0.5 * (2**attempt), 10.0))
        raise AssertionError("unreachable")

    def handle(self, *args, **options) -> None:
        initialize = bool(options["initialize_checkpoint"])
        watch = bool(options["watch"])
        status = bool(options["status"])
        if sum((initialize, watch, status)) > 1:
            raise CommandError(
                "--initialize-checkpoint、--watch 和 --status 不能同时使用"
            )
        interval_seconds = float(options["interval_seconds"])
        if (
            not math.isfinite(interval_seconds)
            or interval_seconds < 0.1
            or interval_seconds > 3600
        ):
            raise CommandError("--interval-seconds 必须在 0.1 到 3600 之间")
        try:
            source = resolve_source_path(Path(options["source"]))
            if status:
                result = inspect_sync_status(
                    source,
                    max_age_seconds=float(options["max_age_seconds"]),
                )
                self.stdout.write(json.dumps(result, ensure_ascii=False))
                return
            if initialize:
                result = retry_source_changes(
                    lambda: initialize_checkpoint(source),
                    attempts=int(options["source_change_retries"]),
                )
                self.stdout.write(json.dumps(result, ensure_ascii=False))
                return
            while True:
                result = self._sync_with_retries(source, options)
                self.stdout.write(json.dumps(result, ensure_ascii=False))
                if not watch:
                    return
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            if not watch:
                raise
            self.stderr.write("ERP reference sync watch 已停止")
        except ErpReferenceSyncError as error:
            raise CommandError(str(error)) from error
        except (OperationalError, sqlite3.OperationalError) as error:
            raise CommandError("ERP reference sync 数据库重试耗尽；checkpoint 未推进") from error
        except Exception as error:
            raise CommandError("ERP reference sync 失败；PG 事务已回滚") from error
