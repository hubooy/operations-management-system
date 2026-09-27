from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from netshop.models import (
    NetshopDataRevision,
    NetshopImportAttempt,
    NetshopImportScopeHead,
    NetshopMigrationRun,
    NetshopWriteAuthority,
    NetshopWriteRequestReceipt,
)

from .migrate_netshop_from_d1 import (
    FORMAT_VERSION,
    _open_source,
    _snapshot,
    _source_sections,
    _target_sections,
    _validate_source,
)


CUTOVER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _open_writable(path: Path) -> sqlite3.Connection:
    source = sqlite3.connect(path, timeout=30, isolation_level=None)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA foreign_keys=ON")
    source.execute("BEGIN IMMEDIATE")
    return source


def _authority(source: sqlite3.Connection) -> dict[str, object]:
    row = source.execute(
        "SELECT id,owner,epoch,cutover_id,updated_at "
        "FROM netshop_write_authority WHERE id=1"
    ).fetchone()
    if row is None:
        raise CommandError("D1 网店 authority 尚未安装。")
    return dict(row)


def _source_receipt(
    source: sqlite3.Connection, *, allowed_owners: frozenset[str]
) -> tuple[dict[str, int], dict[str, str], str, str]:
    _validate_source(
        source,
        apply=False,
        allowed_authority_owners=allowed_owners,
    )
    counts, digests, source_digest = _snapshot(_source_sections(source))
    return counts, digests, source_digest, f"netshop-{source_digest[:24]}"


def _assert_postgres_quiet() -> NetshopWriteAuthority:
    authority = NetshopWriteAuthority.objects.select_for_update().filter(id=1).first()
    if authority is None:
        raise CommandError("PostgreSQL 网店 authority 尚未初始化。")
    if (
        NetshopWriteRequestReceipt.objects.filter(status="processing").exists()
        or NetshopImportAttempt.objects.filter(outcome="processing").exists()
        or NetshopImportScopeHead.objects.exclude(status="ready").exists()
        or NetshopImportScopeHead.objects.exclude(owner_token="").exists()
    ):
        raise CommandError("PostgreSQL 网店写入状态不静默，拒绝 authority 变更。")
    return authority


def _verified_migration(
    *,
    run_id: str,
    source_counts: dict[str, int],
    source_digests: dict[str, str],
    source_digest: str,
) -> NetshopMigrationRun:
    run = NetshopMigrationRun.objects.filter(
        id=run_id, mode="apply", status="completed"
    ).first()
    if (
        run is None
        or run.completed_at is None
        or run.completed_at < run.created_at
        or not re.fullmatch(r"[0-9a-f]{64}", run.source_path_digest)
        or run.source_snapshot_digest != source_digest
        or run.target_snapshot_digest != source_digest
        or run.source_counts != source_counts
        or run.target_counts != source_counts
        or run.manifest.get("version") != FORMAT_VERSION
        or run.manifest.get("digests") != source_digests
    ):
        raise CommandError("approved-run-id 不是当前 D1 快照的完成网店迁移。")
    target_counts, target_digests, target_digest = _snapshot(_target_sections())
    revision = NetshopDataRevision.objects.filter(domain="netshop").first()
    if (
        target_counts != source_counts
        or target_digests != source_digests
        or target_digest != source_digest
        or revision is None
        or revision.source_digest != source_digest
    ):
        raise CommandError("PostgreSQL 网店事实已偏离获批迁移快照。")
    return run


class Command(BaseCommand):
    help = "Prepare, abort, activate, or inspect the netshop single-write authority."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        action = parser.add_mutually_exclusive_group()
        action.add_argument("--prepare", action="store_true")
        action.add_argument("--abort-pending", action="store_true")
        action.add_argument("--activate", action="store_true")
        parser.add_argument("--approved-run-id", default="")
        parser.add_argument("--cutover-id", default="")

    def handle(self, *args: Any, **options: Any) -> None:
        if (
            settings.DJANGO_ENVIRONMENT == "production"
            and settings.DJANGO_PROCESS_ROLE != "migration_writer"
        ):
            raise CommandError("生产网店 authority 只能由 migration_writer 进程角色操作。")
        path = Path(str(options["source"])).expanduser().resolve()
        if not path.is_file():
            raise CommandError("D1 网店源文件不存在。")
        prepare = bool(options["prepare"])
        abort = bool(options["abort_pending"])
        activate = bool(options["activate"])
        mutating = prepare or abort or activate
        approved = str(options.get("approved_run_id") or "").strip()
        cutover_id = str(options.get("cutover_id") or "").strip()
        if mutating and (not approved or not CUTOVER_ID_RE.fullmatch(cutover_id)):
            raise CommandError("authority 变更必须提供有效 approved-run-id 和 cutover-id。")
        if not mutating and (approved or cutover_id):
            raise CommandError("只读 status 不接受 approved-run-id 或 cutover-id。")

        source = _open_writable(path) if mutating else _open_source(path)
        try:
            current = _authority(source)
            target = NetshopWriteAuthority.objects.filter(id=1).first()
            if not mutating:
                source.rollback()
                self.stdout.write(
                    json.dumps(
                        {
                            "status": "ok",
                            "d1": {
                                "owner": current["owner"],
                                "epoch": int(current["epoch"]),
                                "cutoverId": current["cutover_id"],
                            },
                            "postgresql": {
                                "status": target.status if target else "missing",
                                "authorityEpoch": (
                                    str(target.authority_epoch)
                                    if target and target.authority_epoch
                                    else ""
                                ),
                                "cutoverId": target.cutover_id if target else "",
                                "migrationRunId": (
                                    target.migration_verify_run_id if target else ""
                                ),
                            },
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                return

            allowed = (
                frozenset({"d1", "pending"})
                if prepare or abort
                else frozenset({"pending", "postgresql"})
            )
            counts, digests, source_digest, run_id = _source_receipt(
                source, allowed_owners=allowed
            )
            if approved != run_id:
                raise CommandError("approved-run-id 与当前冻结网店事实不一致。")

            if prepare:
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status != "d1":
                        raise CommandError("PostgreSQL 网店 authority 已不在 d1。")
                if current["owner"] == "pending" and current["cutover_id"] == cutover_id:
                    source.rollback()
                elif current["owner"] == "d1":
                    changed = source.execute(
                        "UPDATE netshop_write_authority SET owner='pending',epoch=epoch+1,"
                        "cutover_id=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=1 AND owner='d1'",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 网店 authority prepare 未取得唯一所有权。")
                    source.commit()
                else:
                    raise CommandError("D1 网店 authority 无法进入本次 pending。")
                result = {
                    "status": "prepared",
                    "cutoverId": cutover_id,
                    "approvedRunId": run_id,
                    "sourceDigest": source_digest,
                }
            elif abort:
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status != "d1":
                        raise CommandError("PostgreSQL 已取得网店写权，不能回退 pending。")
                if current["cutover_id"] != cutover_id:
                    raise CommandError("D1 不在本次网店 cutover 的 pending 状态。")
                if current["owner"] == "d1":
                    source.rollback()
                else:
                    changed = source.execute(
                        "UPDATE netshop_write_authority SET owner='d1',epoch=epoch+1,"
                        "updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 网店 authority pending 回退失败。")
                    source.commit()
                result = {
                    "status": "aborted",
                    "cutoverId": cutover_id,
                    "approvedRunId": run_id,
                }
            else:
                if current["cutover_id"] != cutover_id:
                    raise CommandError("D1 必须先进入本次网店 cutover 的 pending 状态。")
                _verified_migration(
                    run_id=run_id,
                    source_counts=counts,
                    source_digests=digests,
                    source_digest=source_digest,
                )
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status == "d1":
                        if target.migration_verify_run_id != run_id:
                            raise CommandError("PostgreSQL authority 未绑定当前完成迁移。")
                        target.status = "postgres"
                        target.authority_epoch = uuid.uuid4()
                        target.cutover_id = cutover_id
                        target.activated_at = timezone.now()
                        target.save()
                    elif (
                        target.status != "postgres"
                        or target.cutover_id != cutover_id
                        or target.migration_verify_run_id != run_id
                        or target.authority_epoch is None
                    ):
                        raise CommandError("PostgreSQL 网店 authority 与本次 cutover 冲突。")
                    epoch = str(target.authority_epoch)
                if current["owner"] == "pending":
                    changed = source.execute(
                        "UPDATE netshop_write_authority SET owner='postgresql',epoch=epoch+1,"
                        "updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError(
                            "D1 网店 authority 终态写入失败；PostgreSQL 已安全持有写权。"
                        )
                    source.commit()
                else:
                    source.rollback()
                result = {
                    "status": "activated",
                    "cutoverId": cutover_id,
                    "approvedRunId": run_id,
                    "authorityEpoch": epoch,
                }
            self.stdout.write(
                json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            )
        except sqlite3.DatabaseError as error:
            source.rollback()
            raise CommandError("D1 网店 authority 操作失败。") from error
        finally:
            source.close()
