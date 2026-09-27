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

from access_control.models import (
    AccessControlDataRevision,
    AccessControlMigrationRun,
    AccessControlWriteAuthority,
    AccessControlWriteRequestReceipt,
)
from access_control.policy import sha256_json

from .migrate_access_control_from_d1 import GENERATION_VERSION, _counts, _source_snapshot, _target_snapshot


RUN_ID_RE = re.compile(r"^access-control-[0-9a-f]{32}$")
CUTOVER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _source_connection(path: Path, writable: bool) -> sqlite3.Connection:
    if writable:
        source = sqlite3.connect(path, timeout=30, isolation_level=None)
        source.execute("BEGIN IMMEDIATE").close()
    else:
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        source.execute("BEGIN").close()
    source.row_factory = sqlite3.Row
    return source


def _source_authority(source: sqlite3.Connection) -> dict[str, object]:
    row = source.execute(
        "SELECT id,owner,epoch,cutover_id FROM access_control_write_authority WHERE id=1"
    ).fetchone()
    if row is None or str(row["owner"]) not in {"legacy", "pending", "postgresql"} or int(row["epoch"]) < 1:
        raise CommandError("D1 权限 authority 尚未正确安装")
    return dict(row)


def _verified_apply(path: Path, run_id: str) -> AccessControlMigrationRun:
    source = _source_snapshot(path)
    digest = sha256_json(source)
    run = AccessControlMigrationRun.objects.filter(
        id=run_id, mode="apply", status="verified",
        source_snapshot_digest=digest, target_snapshot_digest=digest,
    ).first()
    if (
        run is None or run.source_counts != _counts(source) or run.target_counts != _counts(source)
        or run.manifest.get("version") != GENERATION_VERSION
        or sha256_json(_target_snapshot()) != digest
    ):
        raise CommandError("当前 D1 权限事实没有可激活的已复验迁移")
    return run


class Command(BaseCommand):
    help = "Prepare, abort, activate, or inspect the access-control write authority."

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
            raise CommandError("生产权限 authority 只能由 migration_writer 操作")
        source_input = Path(str(options["source"])).expanduser()
        if not source_input.is_file() or source_input.is_symlink() or source_input.suffix.lower() not in {".sqlite", ".sqlite3"}:
            raise CommandError("D1 权限 authority 源必须是普通 SQLite 文件")
        path = source_input.resolve()
        prepare = bool(options["prepare"])
        abort = bool(options["abort_pending"])
        activate = bool(options["activate"])
        mutating = prepare or abort or activate
        run_id = str(options.get("approved_run_id") or "").strip()
        cutover_id = str(options.get("cutover_id") or "").strip()
        if mutating and (not RUN_ID_RE.fullmatch(run_id) or not CUTOVER_ID_RE.fullmatch(cutover_id)):
            raise CommandError("权限 authority 变更必须提供有效 approved-run-id 与 cutover-id")
        if not mutating and (run_id or cutover_id):
            raise CommandError("权限 authority status 不接受变更参数")
        source = _source_connection(path, mutating)
        try:
            current = _source_authority(source)
            target = AccessControlWriteAuthority.objects.filter(id=1).first()
            if not mutating:
                source.rollback()
                self.stdout.write(json.dumps({
                    "status": "ok",
                    "d1": {"owner": current["owner"], "epoch": current["epoch"], "cutoverId": current["cutover_id"]},
                    "postgresql": {"status": target.status if target else "missing", "authorityEpoch": str(target.authority_epoch or "") if target else "", "cutoverId": target.cutover_id if target else "", "migrationRunId": target.migration_verify_run_id if target else ""},
                }, ensure_ascii=False, separators=(",", ":")))
                return
            run = _verified_apply(path, run_id)
            if AccessControlWriteRequestReceipt.objects.filter(status="processing").exists():
                raise CommandError("PostgreSQL 权限域仍有处理中写请求")
            if prepare:
                target = AccessControlWriteAuthority.objects.get(id=1)
                if target.status != "d1" or target.migration_verify_run_id != run.id:
                    raise CommandError("PostgreSQL 权限 authority 未绑定当前复验迁移")
                if current["owner"] == "legacy":
                    changed = source.execute(
                        "UPDATE access_control_write_authority SET owner='pending',epoch=epoch+1,cutover_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='legacy'",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 权限 authority prepare 失败")
                    source.commit()
                elif current["owner"] == "pending" and current["cutover_id"] == cutover_id:
                    source.rollback()
                else:
                    raise CommandError("D1 权限 authority 无法进入本次 pending")
                result = {"status": "prepared", "cutoverId": cutover_id, "approvedRunId": run.id}
            elif abort:
                target = AccessControlWriteAuthority.objects.get(id=1)
                if target.status != "d1" or current["owner"] != "pending" or current["cutover_id"] != cutover_id:
                    raise CommandError("权限 authority 不允许回退本次 pending")
                changed = source.execute(
                    "UPDATE access_control_write_authority SET owner='legacy',epoch=epoch+1,updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='pending' AND cutover_id=?",
                    (cutover_id,),
                ).rowcount
                if changed != 1:
                    raise CommandError("D1 权限 authority pending 回退失败")
                source.commit()
                result = {"status": "aborted", "cutoverId": cutover_id, "approvedRunId": run.id}
            else:
                if current["owner"] not in {"pending", "postgresql"} or current["cutover_id"] != cutover_id:
                    raise CommandError("D1 必须先进入本次权限 cutover 的 pending 状态")
                with transaction.atomic():
                    AccessControlDataRevision.objects.select_for_update().get(domain="access-control")
                    target = AccessControlWriteAuthority.objects.select_for_update().get(id=1)
                    _verified_apply(path, run_id)
                    if target.status == "d1":
                        target.status = "postgres"
                        target.authority_epoch = uuid.uuid4()
                        target.cutover_id = cutover_id
                        target.activated_at = timezone.now()
                        target.save()
                    elif target.status != "postgres" or target.cutover_id != cutover_id or target.migration_verify_run_id != run.id:
                        raise CommandError("PostgreSQL 权限 authority 与本次 cutover 冲突")
                    epoch = str(target.authority_epoch)
                if current["owner"] == "pending":
                    changed = source.execute(
                        "UPDATE access_control_write_authority SET owner='postgresql',epoch=epoch+1,updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 权限 authority 终态写入失败；PostgreSQL 已安全持有写权")
                    source.commit()
                else:
                    source.rollback()
                result = {"status": "activated", "cutoverId": cutover_id, "approvedRunId": run.id, "authorityEpoch": epoch}
            self.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        except sqlite3.DatabaseError as error:
            source.rollback()
            raise CommandError("D1 权限 authority 操作失败") from error
        finally:
            source.close()
