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

from finance.models import (
    FinanceDataRevision,
    FinanceImportAttempt,
    FinanceImportScopeHead,
    FinanceMigrationRun,
    FinanceWriteAuthority,
    FinanceWriteRequestReceipt,
)

from .migrate_finance_from_d1 import (
    FORMAT_VERSION,
    _valid_hex,
    _open_source,
    _path_digest,
    _snapshot,
    _target_snapshot,
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
        "SELECT id, owner, epoch, cutover_id, updated_at "
        "FROM finance_write_authority WHERE id=1"
    ).fetchone()
    if row is None:
        raise CommandError("D1 财务 authority 尚未安装。")
    return dict(row)


def _verified_run(identifier: str, path_digest: str) -> FinanceMigrationRun:
    run = FinanceMigrationRun.objects.filter(
        id=identifier, mode="verify", status="succeeded"
    ).first()
    provenance = run.manifest.get("sourceProvenance") if run is not None else None
    if (
        run is None
        or run.manifest.get("formatVersion") != FORMAT_VERSION
        or not run.target_snapshot_digest
        or not isinstance(provenance, dict)
        or provenance.get("liveSourcePathDigest") != path_digest
        or not _valid_hex(provenance.get("sourceArtifactSha256"))
        or provenance.get("formatVersion")
        not in {"finance-d1-rehearsal-snapshot-v1", "finance-direct-source-v1"}
    ):
        raise CommandError("verify-run-id 不是当前 D1 源的成功财务核对运行。")
    return run


def _assert_projection(run: FinanceMigrationRun, source: sqlite3.Connection) -> None:
    snapshot = _snapshot(
        source,
        allowed_authority_owners=frozenset({"d1", "pending", "postgresql"}),
    )
    source_manifest = run.manifest.get("projectionDigests")
    if (
        snapshot.target_digest != run.target_snapshot_digest
        or snapshot.counts != run.source_counts
        or snapshot.digests != source_manifest
    ):
        raise CommandError("D1 财务事实已偏离获批核对快照。")
    target_counts, target_digests, target_digest = _target_snapshot()
    revision = FinanceDataRevision.objects.filter(domain="finance").first()
    if (
        target_counts != snapshot.counts
        or target_digests != snapshot.digests
        or target_digest != snapshot.target_digest
        or revision is None
        or revision.source_digest != target_digest
    ):
        raise CommandError("PostgreSQL 财务事实已偏离获批核对快照。")


def _assert_postgres_quiet() -> FinanceWriteAuthority:
    authority = FinanceWriteAuthority.objects.select_for_update().filter(id=1).first()
    if authority is None:
        raise CommandError("PostgreSQL 财务 authority 尚未初始化。")
    if (
        FinanceWriteRequestReceipt.objects.exists()
        or FinanceImportAttempt.objects.filter(outcome="processing").exists()
        or FinanceImportScopeHead.objects.exclude(status="ready").exists()
        or FinanceImportScopeHead.objects.exclude(owner_token="").exists()
    ):
        raise CommandError("PostgreSQL 财务写入状态不静默，拒绝 authority 变更。")
    return authority


class Command(BaseCommand):
    help = "Prepare, abort, activate, or inspect the finance single-write authority."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        action = parser.add_mutually_exclusive_group()
        action.add_argument("--prepare", action="store_true")
        action.add_argument("--abort-pending", action="store_true")
        action.add_argument("--activate", action="store_true")
        parser.add_argument("--verify-run-id")
        parser.add_argument("--cutover-id")

    def handle(self, *args: Any, **options: Any) -> None:
        if (
            settings.DJANGO_ENVIRONMENT == "production"
            and settings.DJANGO_PROCESS_ROLE != "migration_writer"
        ):
            raise CommandError("生产财务 authority 只能由 migration_writer 进程角色操作。")
        path = Path(str(options["source"])).expanduser().resolve()
        if not path.is_file():
            raise CommandError("D1 源文件不存在。")
        prepare = bool(options["prepare"])
        abort = bool(options["abort_pending"])
        activate = bool(options["activate"])
        mutating = prepare or abort or activate
        verify_run_id = str(options.get("verify_run_id") or "").strip()
        cutover_id = str(options.get("cutover_id") or "").strip()
        if mutating and (not verify_run_id or not CUTOVER_ID_RE.fullmatch(cutover_id)):
            raise CommandError("authority 变更必须提供有效 verify-run-id 和 cutover-id。")
        if not mutating and (verify_run_id or cutover_id):
            raise CommandError("只读 status 不接受 verify-run-id 或 cutover-id。")

        # Status is intentionally a read-only operation.  Only the three
        # explicit transition actions are allowed to take SQLite's immediate
        # writer lock, which keeps an operator health check from interrupting
        # the still-authoritative D1 import path.
        source = _open_writable(path) if mutating else _open_source(path)
        try:
            current = _authority(source)
            target = FinanceWriteAuthority.objects.filter(id=1).first()
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
                        "verifyRunId": target.migration_verify_run_id if target else "",
                    },
                }, ensure_ascii=False, separators=(",", ":")))
                return

            run = _verified_run(verify_run_id, _path_digest(path))
            _assert_projection(run, source)
            if prepare:
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status != "d1":
                        raise CommandError("PostgreSQL 财务 authority 已不在 d1。")
                if current["owner"] == "pending" and current["cutover_id"] == cutover_id:
                    source.rollback()
                elif current["owner"] == "d1":
                    changed = source.execute(
                        "UPDATE finance_write_authority SET owner='pending', epoch=epoch+1, "
                        "cutover_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='d1'",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 财务 authority prepare 未取得唯一所有权。")
                    source.commit()
                else:
                    raise CommandError("D1 财务 authority 无法进入 pending。")
                result = {"status": "prepared", "cutoverId": cutover_id, "verifyRunId": verify_run_id}
            elif abort:
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status != "d1":
                        raise CommandError("PostgreSQL 已取得写权，不能执行 pending 回退。")
                if current["owner"] != "pending" or current["cutover_id"] != cutover_id:
                    raise CommandError("D1 不在本次 cutover 的 pending 状态。")
                changed = source.execute(
                    "UPDATE finance_write_authority SET owner='d1', epoch=epoch+1, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='pending' AND cutover_id=?",
                    (cutover_id,),
                ).rowcount
                if changed != 1:
                    raise CommandError("D1 财务 authority pending 回退失败。")
                source.commit()
                result = {"status": "aborted", "cutoverId": cutover_id, "verifyRunId": verify_run_id}
            else:
                if current["owner"] not in {"pending", "postgresql"} or current["cutover_id"] != cutover_id:
                    raise CommandError("D1 必须先进入本次 cutover 的 pending 状态。")
                with transaction.atomic():
                    target = _assert_postgres_quiet()
                    if target.status == "d1":
                        target.status = "postgres"
                        target.authority_epoch = uuid.uuid4()
                        target.cutover_id = cutover_id
                        target.migration_verify_run_id = verify_run_id
                        target.activated_at = timezone.now()
                        target.save()
                    elif (
                        target.status != "postgres"
                        or target.cutover_id != cutover_id
                        or target.migration_verify_run_id != verify_run_id
                        or target.authority_epoch is None
                    ):
                        raise CommandError("PostgreSQL 财务 authority 与本次 cutover 冲突。")
                    epoch = str(target.authority_epoch)
                if current["owner"] == "pending":
                    changed = source.execute(
                        "UPDATE finance_write_authority SET owner='postgresql', epoch=epoch+1, "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover_id,),
                    ).rowcount
                    if changed != 1:
                        raise CommandError("D1 财务 authority 终态写入失败；PostgreSQL 已安全持有写权。")
                    source.commit()
                else:
                    source.rollback()
                result = {
                    "status": "activated", "cutoverId": cutover_id,
                    "verifyRunId": verify_run_id, "authorityEpoch": epoch,
                }
            self.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        except sqlite3.DatabaseError as error:
            source.rollback()
            raise CommandError("D1 财务 authority 操作失败。") from error
        finally:
            source.close()
