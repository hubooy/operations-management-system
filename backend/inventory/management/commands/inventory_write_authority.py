from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from inventory.models import (
    InventoryDataRevision,
    InventoryImportAttempt,
    InventoryImportBatch,
    InventoryImportScopeHead,
    InventoryMigrationRun,
    InventoryRawUploadSession,
    InventoryWriteAuthority,
    InventoryWriteRequestReceipt,
)

from .migrate_inventory_from_d1 import (
    GENERATION_VERSION,
    _counts,
    _exclusions,
    _sha,
    _source_snapshot,
    _target_snapshot,
)


CUTOVER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _path_digest(path: Path) -> str:
    return hashlib.sha256(str(path).lower().encode()).hexdigest()


def _open(path: Path, *, writable: bool) -> sqlite3.Connection:
    if writable:
        connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.execute("BEGIN")
    connection.row_factory = sqlite3.Row
    return connection


def _source_authority(source: sqlite3.Connection) -> dict[str, object]:
    row = source.execute(
        "SELECT id,owner,epoch,cutover_id,updated_at "
        "FROM inventory_write_authority WHERE id=1"
    ).fetchone()
    if row is None:
        raise CommandError("D1 库存 authority 尚未安装")
    return dict(row)


def _assert_postgres_quiet() -> InventoryWriteAuthority:
    authority = InventoryWriteAuthority.objects.select_for_update().filter(id=1).first()
    if authority is None:
        raise CommandError("PostgreSQL 库存 authority 尚未初始化")
    if (
        InventoryWriteRequestReceipt.objects.filter(status="processing").exists()
        or InventoryImportBatch.objects.filter(status="processing").exists()
        or InventoryImportAttempt.objects.filter(outcome="processing").exists()
        or InventoryImportScopeHead.objects.exclude(status="ready").exists()
        or InventoryImportScopeHead.objects.exclude(owner_token="").exists()
        or InventoryRawUploadSession.objects.exclude(status="completed").exists()
    ):
        raise CommandError("PostgreSQL 库存写入状态不静默，拒绝 authority 变更")
    return authority


def _verified_run(
    path: Path,
    source_digest: str,
    counts: dict[str, int],
    exclusions: dict[str, int],
) -> InventoryMigrationRun:
    run = InventoryMigrationRun.objects.filter(
        mode="apply",
        status="verified",
        source_path_digest=_path_digest(path),
        source_snapshot_digest=source_digest,
        target_snapshot_digest=source_digest,
    ).order_by("-completed_at").first()
    target = _target_snapshot()
    revision = InventoryDataRevision.objects.filter(domain="inventory").first()
    if (
        run is None
        or run.completed_at is None
        or run.source_counts != counts
        or run.target_counts != counts
        or run.manifest.get("version") != GENERATION_VERSION
        or run.manifest.get("sourceDigest") != source_digest
        or run.manifest.get("exclusions") != exclusions
        or _sha(target) != source_digest
        or _counts(target) != counts
        or _exclusions(target) != exclusions
        or revision is None
        or revision.source_digest != source_digest
    ):
        raise CommandError("当前 D1 快照没有一笔可激活的已复验库存迁移")
    return run


class Command(BaseCommand):
    help = "Prepare, abort, activate, or inspect the inventory single-write authority."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        action = parser.add_mutually_exclusive_group()
        action.add_argument("--prepare", action="store_true")
        action.add_argument("--abort-pending", action="store_true")
        action.add_argument("--activate", action="store_true")
        parser.add_argument("--approved-run-id", default="")
        parser.add_argument("--cutover-id", default="")

    def handle(self, *args, **options) -> None:
        if settings.DJANGO_ENVIRONMENT == "production" and settings.DJANGO_PROCESS_ROLE != "migration_writer":
            raise CommandError("生产库存 authority 只能由 migration_writer 进程角色操作")
        source_input = Path(str(options["source"])).expanduser()
        if not source_input.is_file() or source_input.is_symlink():
            raise CommandError("D1 库存源必须是普通文件")
        path = source_input.resolve()
        prepare = bool(options["prepare"])
        abort = bool(options["abort_pending"])
        activate = bool(options["activate"])
        mutating = prepare or abort or activate
        approved = str(options.get("approved_run_id") or "").strip()
        cutover_id = str(options.get("cutover_id") or "").strip()
        if mutating and (not approved or not CUTOVER_ID_RE.fullmatch(cutover_id)):
            raise CommandError("authority 变更必须提供有效 approved-run-id 和 cutover-id")
        if not mutating and (approved or cutover_id):
            raise CommandError("只读 status 不接受 approved-run-id 或 cutover-id")

        source = _open(path, writable=mutating)
        try:
            current = _source_authority(source)
            target = InventoryWriteAuthority.objects.filter(id=1).first()
            if not mutating:
                source.rollback()
                self.stdout.write(json.dumps({
                    "status": "ok",
                    "d1": {
                        "owner": current["owner"],
                        "epoch": int(current["epoch"]),
                        "cutoverId": current["cutover_id"],
                    },
                    "postgresql": {
                        "status": target.status if target else "missing",
                        "authorityEpoch": str(target.authority_epoch) if target and target.authority_epoch else "",
                        "cutoverId": target.cutover_id if target else "",
                        "migrationRunId": target.migration_verify_run_id if target else "",
                    },
                }, ensure_ascii=False, separators=(",", ":")))
                return

            snapshot = _source_snapshot(path)
            source_digest = _sha(snapshot)
            counts = _counts(snapshot)
            exclusions = _exclusions(snapshot)
            del snapshot
            run = _verified_run(path, source_digest, counts, exclusions)
            if approved != run.id:
                raise CommandError("approved-run-id 与当前冻结库存事实不一致")

            if prepare:
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status != "d1" or target.migration_verify_run_id != run.id:
                        raise CommandError("PostgreSQL 库存 authority 未绑定当前复验迁移")
                if current["owner"] == "pending" and current["cutover_id"] == cutover_id:
                    source.rollback()
                elif current["owner"] == "d1":
                    changed = source.execute(
                        "UPDATE inventory_write_authority SET owner='pending',epoch=epoch+1,"
                        "cutover_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='d1'",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 库存 authority prepare 未取得唯一所有权")
                    source.commit()
                else:
                    raise CommandError("D1 库存 authority 无法进入本次 pending")
                result = {"status": "prepared", "cutoverId": cutover_id, "approvedRunId": run.id, "sourceDigest": source_digest}
            elif abort:
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status != "d1":
                        raise CommandError("PostgreSQL 已取得库存写权，不能回退 pending")
                if current["cutover_id"] != cutover_id:
                    raise CommandError("D1 不在本次库存 cutover 的 pending 状态")
                if current["owner"] == "d1":
                    source.rollback()
                else:
                    changed = source.execute(
                        "UPDATE inventory_write_authority SET owner='d1',epoch=epoch+1,"
                        "updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 库存 authority pending 回退失败")
                    source.commit()
                result = {"status": "aborted", "cutoverId": cutover_id, "approvedRunId": run.id}
            else:
                if current["cutover_id"] != cutover_id or current["owner"] not in {"pending", "postgresql"}:
                    raise CommandError("D1 必须先进入本次库存 cutover 的 pending 状态")
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status == "d1":
                        if target.migration_verify_run_id != run.id:
                            raise CommandError("PostgreSQL authority 未绑定当前复验迁移")
                        target.status = "postgres"
                        target.authority_epoch = uuid.uuid4()
                        target.cutover_id = cutover_id
                        target.activated_at = timezone.now()
                        target.save()
                    elif (
                        target.status != "postgres"
                        or target.cutover_id != cutover_id
                        or target.migration_verify_run_id != run.id
                        or target.authority_epoch is None
                    ):
                        raise CommandError("PostgreSQL 库存 authority 与本次 cutover 冲突")
                    epoch = str(target.authority_epoch)
                if current["owner"] == "pending":
                    changed = source.execute(
                        "UPDATE inventory_write_authority SET owner='postgresql',epoch=epoch+1,"
                        "updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 库存 authority 终态写入失败；PostgreSQL 已安全持有写权")
                    source.commit()
                else:
                    source.rollback()
                result = {"status": "activated", "cutoverId": cutover_id, "approvedRunId": run.id, "authorityEpoch": epoch}
            self.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        except sqlite3.DatabaseError as error:
            source.rollback()
            raise CommandError("D1 库存 authority 操作失败") from error
        finally:
            source.close()
