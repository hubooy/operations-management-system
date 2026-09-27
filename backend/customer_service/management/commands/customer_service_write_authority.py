from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from customer_service.models import (
    CustomerServiceImportScopeHead,
    CustomerServiceMigrationRun,
    CustomerServiceWriteAuthority,
    CustomerServiceWriteRequestReceipt,
)

from .migrate_customer_service_from_d1 import GENERATION_VERSION, _counts, _sha, _source_snapshot, _target_snapshot


RUN_ID_RE = re.compile(r"^customer-service-[0-9a-f]{32}$")
CUTOVER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _open_source(path: Path, *, writable: bool) -> sqlite3.Connection:
    if writable:
        source = sqlite3.connect(path, timeout=30, isolation_level=None)
        source.execute("PRAGMA foreign_keys=ON")
        source.execute("BEGIN IMMEDIATE")
    else:
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        source.execute("BEGIN")
    source.row_factory = sqlite3.Row
    return source


def _source_authority(source: sqlite3.Connection) -> dict[str, object]:
    row = source.execute(
        "SELECT id,owner,epoch,cutover_id FROM customer_service_write_authority WHERE id=1"
    ).fetchone()
    if row is None:
        raise CommandError("D1 客服 authority 尚未安装")
    result = dict(row)
    if (
        int(result["id"]) != 1
        or str(result["owner"]) not in {"legacy", "pending", "postgresql"}
        or int(result["epoch"]) < 1
    ):
        raise CommandError("D1 客服 authority 状态无效")
    return result


def _verified_run(source_data: dict[str, object], approved_run_id: str) -> CustomerServiceMigrationRun:
    source_digest = _sha(source_data)
    source_counts = _counts(source_data)
    run = CustomerServiceMigrationRun.objects.filter(
        id=approved_run_id,
        mode="apply",
        status="verified",
        source_snapshot_digest=source_digest,
        target_snapshot_digest=source_digest,
    ).first()
    target_data = _target_snapshot(approved_run_id)
    if (
        run is None
        or run.source_counts != source_counts
        or run.target_counts != source_counts
        or run.manifest.get("version") != GENERATION_VERSION
        or run.manifest.get("sourceDigest") != source_digest
        or _sha(target_data) != source_digest
        or _counts(target_data) != source_counts
    ):
        raise CommandError("当前 D1 客服事实没有可激活的已复验迁移")
    return run


def _assert_postgres_quiet() -> CustomerServiceWriteAuthority:
    authority = CustomerServiceWriteAuthority.objects.select_for_update().filter(id=1).first()
    if authority is None:
        raise CommandError("PostgreSQL 客服 authority 尚未初始化")
    if CustomerServiceWriteRequestReceipt.objects.filter(status="processing").exists():
        raise CommandError("PostgreSQL 客服仍有处理中写请求")
    if CustomerServiceImportScopeHead.objects.filter(status="processing").exists():
        raise CommandError("PostgreSQL 客服仍有处理中导入范围")
    return authority


class Command(BaseCommand):
    help = "Prepare, abort, activate, or inspect the customer-service write authority."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        actions = parser.add_mutually_exclusive_group()
        actions.add_argument("--prepare", action="store_true")
        actions.add_argument("--abort-pending", action="store_true")
        actions.add_argument("--activate", action="store_true")
        parser.add_argument("--approved-run-id", default="")
        parser.add_argument("--cutover-id", default="")

    def handle(self, *args, **options) -> None:
        if settings.DJANGO_ENVIRONMENT == "production" and settings.DJANGO_PROCESS_ROLE != "migration_writer":
            raise CommandError("生产客服 authority 只能由 migration_writer 操作")
        source_input = Path(str(options["source"])).expanduser()
        if (
            not source_input.is_file()
            or source_input.is_symlink()
            or source_input.suffix.lower() not in {".sqlite", ".sqlite3"}
        ):
            raise CommandError("D1 客服 authority 源必须是普通 SQLite 文件")
        path = source_input.resolve()
        prepare = bool(options["prepare"])
        abort = bool(options["abort_pending"])
        activate = bool(options["activate"])
        mutating = prepare or abort or activate
        approved = str(options.get("approved_run_id") or "").strip()
        cutover_id = str(options.get("cutover_id") or "").strip()
        if mutating and (not RUN_ID_RE.fullmatch(approved) or not CUTOVER_ID_RE.fullmatch(cutover_id)):
            raise CommandError("客服 authority 变更必须提供有效 approved-run-id 与 cutover-id")
        if not mutating and (approved or cutover_id):
            raise CommandError("客服 authority status 不接受变更参数")

        source = _open_source(path, writable=mutating)
        try:
            current = _source_authority(source)
            target = CustomerServiceWriteAuthority.objects.filter(id=1).first()
            if not mutating:
                source.rollback()
                self.stdout.write(json.dumps({
                    "status": "ok",
                    "d1": {
                        "owner": current["owner"], "epoch": int(current["epoch"]),
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

            source_data = _source_snapshot(path)
            run = _verified_run(source_data, approved)
            source_digest = _sha(source_data)
            if prepare:
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status != "d1" or target.migration_verify_run_id != run.id:
                        raise CommandError("PostgreSQL 客服 authority 未绑定当前复验迁移")
                if current["owner"] == "pending" and current["cutover_id"] == cutover_id:
                    source.rollback()
                elif current["owner"] == "legacy":
                    changed = source.execute(
                        "UPDATE customer_service_write_authority "
                        "SET owner='pending',epoch=epoch+1,cutover_id=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=1 AND owner='legacy'",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 客服 authority prepare 未取得唯一所有权")
                    source.commit()
                else:
                    raise CommandError("D1 客服 authority 无法进入本次 pending")
                result = {"status": "prepared", "cutoverId": cutover_id, "approvedRunId": run.id, "sourceDigest": source_digest}
            elif abort:
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status != "d1":
                        raise CommandError("PostgreSQL 已取得客服写权，不能回退 pending")
                if current["cutover_id"] != cutover_id:
                    raise CommandError("D1 不在本次客服 cutover 的 pending 状态")
                if current["owner"] == "legacy":
                    source.rollback()
                else:
                    changed = source.execute(
                        "UPDATE customer_service_write_authority "
                        "SET owner='legacy',epoch=epoch+1,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 客服 authority pending 回退失败")
                    source.commit()
                result = {"status": "aborted", "cutoverId": cutover_id, "approvedRunId": run.id}
            else:
                if current["cutover_id"] != cutover_id or current["owner"] not in {"pending", "postgresql"}:
                    raise CommandError("D1 必须先进入本次客服 cutover 的 pending 状态")
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    _verified_run(source_data, approved)
                    if target.status == "d1":
                        if target.migration_verify_run_id != run.id:
                            raise CommandError("PostgreSQL 客服 authority 未绑定当前复验迁移")
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
                        raise CommandError("PostgreSQL 客服 authority 与本次 cutover 冲突")
                    epoch = str(target.authority_epoch)
                if current["owner"] == "pending":
                    changed = source.execute(
                        "UPDATE customer_service_write_authority "
                        "SET owner='postgresql',epoch=epoch+1,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 客服 authority 终态写入失败；PostgreSQL 已安全持有写权")
                    source.commit()
                else:
                    source.rollback()
                result = {"status": "activated", "cutoverId": cutover_id, "approvedRunId": run.id, "authorityEpoch": epoch}
            self.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        except sqlite3.DatabaseError as error:
            source.rollback()
            raise CommandError("D1 客服 authority 操作失败") from error
        finally:
            source.close()
