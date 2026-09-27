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

from workflow.models import (
    WorkflowOperationsMigrationRun,
    WorkflowOperationsWriteAuthority,
    WorkflowWriteRequestReceipt,
)

from .migrate_workflow_operations_from_d1 import (
    GENERATION_VERSION,
    counts,
    digest,
    read_source,
    target_snapshot,
)


CUTOVER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
RUN_ID_RE = re.compile(r"^workflow-ops-[0-9a-f]{32}$")


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


def _source_authority(connection: sqlite3.Connection) -> dict[str, object]:
    row = connection.execute(
        "SELECT id,owner,epoch,cutover_id,updated_at "
        "FROM workflow_operations_write_authority WHERE id=1"
    ).fetchone()
    if row is None:
        raise CommandError("D1 运营事务 authority 尚未安装")
    result = dict(row)
    if (
        int(result["id"]) != 1
        or str(result["owner"]) not in {"legacy", "pending", "postgresql"}
        or int(result["epoch"]) < 1
    ):
        raise CommandError("D1 运营事务 authority 状态无效")
    return result


def _verified_run(
    source_data: dict[str, list[dict[str, object]]],
    approved_run_id: str,
) -> WorkflowOperationsMigrationRun:
    source_hash = digest(source_data)
    source_counts = counts(source_data)
    run = WorkflowOperationsMigrationRun.objects.filter(
        id=approved_run_id,
        mode="apply",
        status="verified",
        source_snapshot_digest=source_hash,
        target_snapshot_digest=source_hash,
        approved_run_id=approved_run_id,
    ).first()
    target_data = target_snapshot()
    if (
        run is None
        or run.source_counts != source_counts
        or run.target_counts != source_counts
        or run.manifest.get("version") != GENERATION_VERSION
        or run.manifest.get("sourceDigest") != source_hash
        or digest(target_data) != source_hash
        or counts(target_data) != source_counts
    ):
        raise CommandError("当前 D1 运营事务事实没有可激活的已复验迁移")
    return run


def _assert_postgres_quiet() -> WorkflowOperationsWriteAuthority:
    authority = WorkflowOperationsWriteAuthority.objects.select_for_update().filter(id=1).first()
    if authority is None:
        raise CommandError("PostgreSQL 运营事务 authority 尚未初始化")
    if WorkflowWriteRequestReceipt.objects.filter(status="processing").exists():
        raise CommandError("PostgreSQL 运营事务仍有处理中写请求")
    return authority


class Command(BaseCommand):
    help = "Prepare, abort, activate, or inspect the remaining workflow authority."

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
            raise CommandError("生产运营事务 authority 只能由 migration_writer 操作")
        source_input = Path(str(options["source"])).expanduser()
        if (
            not source_input.is_file()
            or source_input.is_symlink()
            or source_input.suffix.lower() not in {".sqlite", ".sqlite3"}
        ):
            raise CommandError("D1 运营事务 authority 源必须是普通 SQLite 文件")
        path = source_input.resolve()
        prepare = bool(options["prepare"])
        abort = bool(options["abort_pending"])
        activate = bool(options["activate"])
        mutating = prepare or abort or activate
        approved = str(options.get("approved_run_id") or "").strip()
        cutover_id = str(options.get("cutover_id") or "").strip()
        if mutating and (not RUN_ID_RE.fullmatch(approved) or not CUTOVER_ID_RE.fullmatch(cutover_id)):
            raise CommandError("authority 变更必须提供有效 approved-run-id 与 cutover-id")
        if not mutating and (approved or cutover_id):
            raise CommandError("只读 authority status 不接受变更参数")

        source = _open(path, writable=mutating)
        try:
            current = _source_authority(source)
            target = WorkflowOperationsWriteAuthority.objects.filter(id=1).first()
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

            source_data = read_source(path, existing_connection=source)
            run = _verified_run(source_data, approved)
            source_hash = digest(source_data)

            if prepare:
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status != "disabled" or target.migration_verify_run_id != run.id:
                        raise CommandError("PostgreSQL 运营事务 authority 未绑定当前复验迁移")
                if current["owner"] == "pending" and current["cutover_id"] == cutover_id:
                    source.rollback()
                elif current["owner"] == "legacy":
                    changed = source.execute(
                        "UPDATE workflow_operations_write_authority SET owner='pending',epoch=epoch+1,"
                        "cutover_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='legacy'",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 运营事务 authority prepare 未取得唯一所有权")
                    source.commit()
                else:
                    raise CommandError("D1 运营事务 authority 无法进入本次 pending")
                result = {
                    "status": "prepared",
                    "cutoverId": cutover_id,
                    "approvedRunId": run.id,
                    "sourceDigest": source_hash,
                }
            elif abort:
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status != "disabled":
                        raise CommandError("PostgreSQL 已取得运营事务写权，不能回退 pending")
                if current["cutover_id"] != cutover_id:
                    raise CommandError("D1 不在本次运营事务 cutover 的 pending 状态")
                if current["owner"] == "legacy":
                    source.rollback()
                else:
                    changed = source.execute(
                        "UPDATE workflow_operations_write_authority SET owner='legacy',epoch=epoch+1,"
                        "updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 运营事务 authority pending 回退失败")
                    source.commit()
                result = {"status": "aborted", "cutoverId": cutover_id, "approvedRunId": run.id}
            else:
                if current["cutover_id"] != cutover_id or current["owner"] not in {"pending", "postgresql"}:
                    raise CommandError("D1 必须先进入本次运营事务 cutover 的 pending 状态")
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    _verified_run(source_data, approved)
                    if target.status == "disabled":
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
                        raise CommandError("PostgreSQL 运营事务 authority 与本次 cutover 冲突")
                    epoch = str(target.authority_epoch)
                if current["owner"] == "pending":
                    changed = source.execute(
                        "UPDATE workflow_operations_write_authority SET owner='postgresql',epoch=epoch+1,"
                        "updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 运营事务 authority 终态写入失败；PostgreSQL 已安全持有写权")
                    source.commit()
                else:
                    source.rollback()
                result = {
                    "status": "activated",
                    "cutoverId": cutover_id,
                    "approvedRunId": run.id,
                    "authorityEpoch": epoch,
                }
            self.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        except sqlite3.DatabaseError as error:
            source.rollback()
            raise CommandError("D1 运营事务 authority 操作失败") from error
        finally:
            source.close()
