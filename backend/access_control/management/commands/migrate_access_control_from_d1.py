from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
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
    AppUser,
    PermissionAuditEvent,
)
from access_control.policy import (
    ROLE_CODES,
    USER_STATUSES,
    canonical_json,
    normalize_display_name,
    normalize_email,
    normalize_scope,
    sha256_json,
)
from access_control.service import BOOTSTRAP_ADMIN_EMAIL, ZERO_DIGEST, domain_snapshot


GENERATION_VERSION = "access-control-d1-to-postgres-v1"


def _path_digest(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def _timestamp(value: object) -> datetime:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(raw)
    except ValueError as error:
        raise CommandError("D1 用户时间戳无效") from error
    if timezone.is_naive(result):
        # SQLite CURRENT_TIMESTAMP is UTC, not the business display timezone.
        result = timezone.make_aware(result, datetime_timezone.utc)
    return result.astimezone(datetime_timezone.utc)


def _source_snapshot(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        cursor = source.cursor()
        try:
            cursor.execute("BEGIN")
            cursor.execute(
                "SELECT type FROM sqlite_master WHERE name='app_users' AND type='table'"
            )
            if cursor.fetchone() is None:
                raise CommandError("D1 app_users 权威表不存在或已退役")
            cursor.execute(
                "SELECT email,display_name,role,status,scope_json,created_at,updated_at "
                "FROM app_users ORDER BY email COLLATE NOCASE"
            )
            rows = [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            del cursor
        source.rollback()
    except sqlite3.DatabaseError as error:
        raise CommandError("读取 D1 用户权限快照失败") from error
    finally:
        if "source" in locals():
            source.close()
            del source
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        email = normalize_email(row["email"])
        if email in seen:
            raise CommandError("D1 用户邮箱规范化后重复")
        seen.add(email)
        role = str(row["role"] or "")
        status = str(row["status"] or "")
        if role not in ROLE_CODES or status not in USER_STATUSES:
            raise CommandError("D1 用户角色或状态无效")
        raw_scope = row["scope_json"]
        if raw_scope is None:
            scope = None
        else:
            try:
                scope = normalize_scope(json.loads(str(raw_scope)))
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                raise CommandError("D1 用户数据范围无效") from error
        created_at = _timestamp(row["created_at"])
        updated_at = _timestamp(row["updated_at"])
        if updated_at < created_at:
            raise CommandError("D1 用户更新时间早于创建时间")
        result.append({
            "email": email,
            "displayName": normalize_display_name(row["display_name"] or email),
            "role": role,
            "status": status,
            "scope": scope,
            "version": 1,
            "createdAt": created_at.isoformat(),
            "updatedAt": updated_at.isoformat(),
        })
    bootstrap = next((item for item in result if item["email"] == BOOTSTRAP_ADMIN_EMAIL), None)
    if not bootstrap or bootstrap["role"] != "admin" or bootstrap["status"] != "active" or bootstrap["scope"] is not None:
        raise CommandError("D1 系统引导管理员契约无效")
    if not any(item["role"] == "admin" and item["status"] == "active" and item["scope"] is None for item in result):
        raise CommandError("D1 必须至少包含一个启用且数据范围不受限的管理员")
    return result


def _target_snapshot() -> list[dict[str, object]]:
    return domain_snapshot()


def _counts(snapshot: list[dict[str, object]]) -> dict[str, int]:
    return {
        "users": len(snapshot),
        "active": sum(item["status"] == "active" for item in snapshot),
        "disabled": sum(item["status"] == "disabled" for item in snapshot),
        "restricted": sum(item["scope"] is not None for item in snapshot),
        "admins": sum(item["role"] == "admin" for item in snapshot),
    }


class Command(BaseCommand):
    help = "Migrate the D1 app_users authority to the Django access-control domain."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--mode", required=True, choices=["dry-run", "apply", "verify-only"])
        parser.add_argument("--approve-run-id", default="")

    def handle(self, *args, **options) -> None:
        if settings.DJANGO_ENVIRONMENT == "production" and settings.DJANGO_PROCESS_ROLE != "migration_writer":
            raise CommandError("生产权限迁移只能由 migration_writer 进程角色执行")
        source_input = Path(str(options["source"])).expanduser()
        if not source_input.is_file() or source_input.is_symlink() or source_input.suffix.lower() not in {".sqlite", ".sqlite3"}:
            raise CommandError("权限迁移源必须是普通 SQLite 文件")
        path = source_input.resolve()
        mode = str(options["mode"])
        approved = str(options.get("approve_run_id") or "").strip()
        if mode == "apply" and not approved.startswith("access-control-dryrun-"):
            raise CommandError("apply 必须提供精确 dry-run 批准 ID")
        if mode != "apply" and approved:
            raise CommandError("仅 apply 接受 approve-run-id")
        source_snapshot = _source_snapshot(path)
        source_digest = sha256_json(source_snapshot)
        source_counts = _counts(source_snapshot)
        path_digest = _path_digest(path)

        if mode == "dry-run":
            run_id = f"access-control-dryrun-{uuid.uuid4().hex}"
            AccessControlMigrationRun.objects.create(
                id=run_id, mode=mode, status="verified", source_path_digest=path_digest,
                source_snapshot_digest=source_digest, source_counts=source_counts,
                manifest={"version": GENERATION_VERSION, "sourceDigest": source_digest},
                completed_at=timezone.now(),
            )
            result = {"status": "verified", "mode": mode, "runId": run_id, "sourceDigest": source_digest, "counts": source_counts}
        elif mode == "apply":
            run_id = f"access-control-{uuid.uuid4().hex}"
            with transaction.atomic():
                dry_run = AccessControlMigrationRun.objects.select_for_update().filter(
                    id=approved, mode="dry-run", status="verified", source_path_digest=path_digest,
                    source_snapshot_digest=source_digest, consumed_by_run_id="",
                ).first()
                if dry_run is None or dry_run.source_counts != source_counts or dry_run.manifest.get("version") != GENERATION_VERSION:
                    raise CommandError("批准的 dry-run 与当前 D1 权限快照不一致或已消费")
                emails = [str(item["email"]) for item in source_snapshot]
                AppUser.objects.exclude(email__in=emails).delete()
                for item in source_snapshot:
                    AppUser.objects.update_or_create(
                        email=item["email"],
                        defaults={
                            "display_name": item["displayName"], "role_id": item["role"],
                            "status": item["status"], "scope": item["scope"], "version": item["version"],
                            "created_at": datetime.fromisoformat(str(item["createdAt"])),
                            "updated_at": datetime.fromisoformat(str(item["updatedAt"])),
                            "migration_generation": run_id,
                        },
                    )
                target_snapshot = _target_snapshot()
                target_digest = sha256_json(target_snapshot)
                if target_digest != source_digest or _counts(target_snapshot) != source_counts:
                    raise CommandError("PostgreSQL 权限迁移事务内回查不一致")
                PermissionAuditEvent.objects.create(
                    request_id=run_id, actor_email="system:migration", actor_role="admin",
                    target_email="", action="d1_users_migrated", before_state=None,
                    after_state={"counts": source_counts, "sourceDigest": source_digest},
                    before_digest=ZERO_DIGEST, after_digest=source_digest, reason="D1 权限域迁移",
                    source="migration", occurred_at=timezone.now(), migration_generation=run_id,
                )
                revision = AccessControlDataRevision.objects.select_for_update().get(domain="access-control")
                revision.revision = max(1, revision.revision + 1)
                revision.source_digest = source_digest
                revision.save(update_fields=["revision", "source_digest", "updated_at"])
                AccessControlMigrationRun.objects.create(
                    id=run_id, mode=mode, status="verified", source_path_digest=path_digest,
                    source_snapshot_digest=source_digest, target_snapshot_digest=target_digest,
                    source_counts=source_counts, target_counts=source_counts, approved_run_id=approved,
                    manifest={"version": GENERATION_VERSION, "sourceDigest": source_digest},
                    completed_at=timezone.now(),
                )
                dry_run.consumed_by_run_id = run_id
                dry_run.save(update_fields=["consumed_by_run_id"])
                authority = AccessControlWriteAuthority.objects.select_for_update().get(id=1)
                if authority.status != "d1":
                    raise CommandError("PostgreSQL 权限 authority 已激活，禁止重复迁移覆盖")
                authority.migration_verify_run_id = run_id
                authority.save(update_fields=["migration_verify_run_id", "updated_at"])
            result = {"status": "verified", "mode": mode, "runId": run_id, "approvedRunId": approved, "sourceDigest": source_digest, "targetDigest": source_digest, "counts": source_counts}
        else:
            target_snapshot = _target_snapshot()
            target_digest = sha256_json(target_snapshot)
            target_counts = _counts(target_snapshot)
            if target_digest != source_digest or target_counts != source_counts:
                raise CommandError("D1 与 PostgreSQL 权限快照不一致")
            run_id = f"access-control-verify-{uuid.uuid4().hex}"
            AccessControlMigrationRun.objects.create(
                id=run_id, mode=mode, status="verified", source_path_digest=path_digest,
                source_snapshot_digest=source_digest, target_snapshot_digest=target_digest,
                source_counts=source_counts, target_counts=target_counts,
                manifest={"version": GENERATION_VERSION, "sourceDigest": source_digest},
                completed_at=timezone.now(),
            )
            result = {"status": "verified", "mode": mode, "runId": run_id, "sourceDigest": source_digest, "targetDigest": target_digest, "counts": source_counts}
        self.stdout.write(canonical_json(result))
