"""One-way AI authority adoption. D1 is fenced before PostgreSQL can be activated."""

import json
from pathlib import Path
import re
import sqlite3
import uuid
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from ai_assistant.control_models import (
    AiDataRevision,
    AiWriteAuthority,
    AiMigrationRun,
    AiWriteReceipt,
)
from ai_assistant.migration_service import source_snapshot, target_snapshot, VERSION

RUN_ID_RE = re.compile(r"^ai-apply-[0-9a-f]{32}$")
CUTOVER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def verified_apply(path, run_id):
    source = source_snapshot(path)
    run = AiMigrationRun.objects.filter(
        id=run_id, mode="apply", status="verified"
    ).first()
    target = target_snapshot()
    if (
        not run
        or run.manifest.get("version") != VERSION
        or run.source_snapshot_digest != source["digest"]
        or run.target_snapshot_digest != source["digest"]
        or target["digest"] != source["digest"]
        or run.source_counts != source["counts"]
        or run.target_counts != source["counts"]
    ):
        raise CommandError("AI 当前源/目标没有完全匹配的已复验迁移")
    return run


def checked_path(value):
    path = Path(value)
    if (
        not path.is_absolute()
        or not path.is_file()
        or path.suffix.lower() not in {".sqlite", ".sqlite3"}
        or any(p.is_symlink() or p.is_junction() for p in [path, *path.parents])
    ):
        raise CommandError("AI authority 只接受全链普通文件的精确绝对路径")
    return path.resolve()


class Command(BaseCommand):
    help = "Prepare or activate the AI PostgreSQL authority; reverse transitions are unavailable."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--approved-run-id", required=True)
        parser.add_argument("--cutover-id", required=True)
        actions = parser.add_mutually_exclusive_group(required=True)
        actions.add_argument("--prepare", action="store_true")
        actions.add_argument("--activate", action="store_true")

    def handle(self, *args, **options):
        if (
            settings.DJANGO_ENVIRONMENT == "production"
            and settings.DJANGO_PROCESS_ROLE != "migration_writer"
        ):
            raise CommandError("AI authority 必须由 migration_writer 操作")
        path = checked_path(options["source"])
        run_id, cutover = options["approved_run_id"], options["cutover_id"]
        if not RUN_ID_RE.fullmatch(run_id) or not CUTOVER_ID_RE.fullmatch(cutover):
            raise CommandError("AI authority 必须提供精确 apply run 与 cutover")
        source = sqlite3.connect(path, timeout=30, isolation_level=None)
        try:
            source.execute("BEGIN IMMEDIATE")
            current = source.execute(
                "SELECT owner,cutover_id FROM ai_write_authority WHERE id=1"
            ).fetchone()
            run = verified_apply(path, run_id)
            if AiWriteReceipt.objects.filter(status="processing").exists():
                raise CommandError("AI 存在未完成请求 receipt")
            with transaction.atomic():
                AiDataRevision.objects.select_for_update().get(domain="ai-assistant")
                target = AiWriteAuthority.objects.select_for_update().get(id=1)
                if target.migration_verify_run_id != run.id:
                    raise CommandError("AI authority 未绑定本次迁移")
                if options["prepare"]:
                    if target.status != "d1":
                        raise CommandError("AI 已跨过 PNR，禁止重新 prepare")
                    if current == ("legacy", ""):
                        source.execute(
                            "UPDATE ai_write_authority SET owner='pending',epoch=epoch+1,cutover_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='legacy'",
                            (cutover,),
                        )
                    elif current != ("pending", cutover):
                        raise CommandError("AI D1 pending 与本次 cutover 不一致")
                    source.commit()
                    result = {
                        "status": "prepared",
                        "cutoverId": cutover,
                        "approvedRunId": run.id,
                    }
                else:
                    if current not in {("pending", cutover), ("postgresql", cutover)}:
                        raise CommandError("必须先冻结同一次 AI D1 authority")
                    verified_apply(path, run_id)
                    if target.status == "d1":
                        target.status = "postgres"
                        target.authority_epoch = uuid.uuid4()
                        target.cutover_id = cutover
                        target.activated_at = timezone.now()
                        target.save()
                    elif target.status != "postgres" or target.cutover_id != cutover:
                        raise CommandError("AI PostgreSQL 激活身份冲突")
                    result = {
                        "status": "activated",
                        "cutoverId": cutover,
                        "approvedRunId": run.id,
                        "authorityEpoch": str(target.authority_epoch),
                    }
            # PostgreSQL commits first while pending continues to reject D1 writers.
            # A crash can only leave both sides fenced; repeating this exact activation
            # completes the same cutover without a second authority epoch.
            if options["activate"]:
                if current[0] == "pending":
                    source.execute(
                        "UPDATE ai_write_authority SET owner='postgresql',epoch=epoch+1,updated_at=CURRENT_TIMESTAMP WHERE id=1 AND owner='pending' AND cutover_id=?",
                        (cutover,),
                    )
                source.commit()
            self.stdout.write(
                json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            )
        except sqlite3.DatabaseError as error:
            source.rollback()
            raise CommandError("AI D1 authority 操作失败") from error
        finally:
            source.close()
