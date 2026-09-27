from __future__ import annotations

from datetime import timezone as datetime_timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from workflow.models import (
    NewProductActivity,
    NewProductProject,
    NewProductStage,
    NewProductTarget,
    WorkflowDataRevision,
    WorkflowMigrationRun,
    WorkflowWriteAuthority,
    WorkflowWriteRequestReceipt,
)
from workflow.new_products import STAGE_DEFINITIONS


GENERATION_VERSION = "workflow-launch-d1-to-postgres-v1"
RUN_ID_RE = re.compile(r"^workflow-[0-9a-f]{32}$")
SHANGHAI = ZoneInfo("Asia/Shanghai")
LEGACY_STATUS_TO_STAGE = {
    "待开始": "not_started",
    "工作中": "in_progress",
    "已完成": "completed",
}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _path_digest(path: Path) -> str:
    return hashlib.sha256(str(path).lower().encode("utf-8")).hexdigest()


def _timestamp(value: object):
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise CommandError("D1 新品记录包含无效时间戳")
    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)
    return parsed.astimezone(datetime_timezone.utc)


def _optional_timestamp(value: object):
    return _timestamp(value) if value else None


def _business_date(value: object) -> str | None:
    return _timestamp(value).astimezone(SHANGHAI).date().isoformat() if value else None


def _uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as error:
        raise CommandError(f"D1 {label} 不是有效 UUID") from error


def _bounded(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise CommandError(f"D1 {label}不能为空")
    if len(result) > maximum:
        raise CommandError(f"D1 {label}超过目标字段上限")
    return result


def _detail(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_snapshot(source: Path, *, require_sealed: bool = True) -> dict[str, object]:
    if require_sealed and (Path(f"{source}-wal").exists() or Path(f"{source}-shm").exists()):
        raise CommandError("D1 新品迁移源必须是无 WAL/SHM 的封存快照")
    try:
        connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise CommandError("无法以只读方式打开 D1 新品快照") from error
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or str(quick_check[0]).lower() != "ok":
            raise CommandError("D1 新品快照 quick_check 未通过")
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {
            "workflow_operation_records",
            "workflow_operation_activities",
            "workflow_launch_write_authority",
        }
        if not required.issubset(tables):
            raise CommandError("D1 新品快照缺少记录、活动或 authority 表")
        authority = connection.execute(
            "SELECT owner,epoch,cutover_id FROM workflow_launch_write_authority WHERE id=1"
        ).fetchone()
        if authority is None or str(authority["owner"]) not in {"legacy", "pending", "postgresql"}:
            raise CommandError("D1 新品快照 authority 状态不允许迁移")
        records = [
            dict(row) for row in connection.execute(
                "SELECT * FROM workflow_operation_records WHERE record_type=? "
                "ORDER BY occurred_at,id", ("launch",)
            ).fetchall()
        ]
        activities = [
            dict(row) for row in connection.execute(
                "SELECT a.* FROM workflow_operation_activities a "
                "JOIN workflow_operation_records r ON r.id=a.record_id "
                "WHERE r.record_type=? ORDER BY a.record_id,a.created_at,a.id", ("launch",)
            ).fetchall()
        ]
        connection.rollback()
    except sqlite3.DatabaseError as error:
        connection.rollback()
        raise CommandError("读取 D1 新品快照失败") from error
    finally:
        connection.close()

    project_ids: set[str] = set()
    projects: list[dict[str, object]] = []
    targets: list[dict[str, object]] = []
    stages: list[dict[str, object]] = []
    gaps: list[dict[str, object]] = []
    for record in records:
        project_id = _uuid(record.get("id"), "新品项目标识")
        if project_id in project_ids:
            raise CommandError("D1 新品项目标识重复")
        project_ids.add(project_id)
        status = _bounded(record.get("status"), "新品状态", 40, required=True)
        if status not in LEGACY_STATUS_TO_STAGE:
            raise CommandError(f"D1 新品状态无法确定性映射：{status}")
        priority = _bounded(record.get("priority"), "优先级", 16, required=True)
        if priority not in {"high", "normal", "low"}:
            raise CommandError("D1 新品优先级无效")
        platform = _bounded(record.get("platform"), "平台", 80) or "待确认"
        shop_name = _bounded(record.get("shop_name"), "店铺", 160) or "待确认"
        proposed_date = _business_date(record.get("occurred_at"))
        if proposed_date is None:
            raise CommandError("D1 新品提出日期缺失")
        target_date = _business_date(record.get("due_at"))
        created_at = _timestamp(record.get("created_at"))
        updated_at = _timestamp(record.get("updated_at"))
        owner = _bounded(record.get("owner"), "负责人", 120)
        record_gaps = ["supplier", "productCodes", "stageBreakdown"]
        if not str(record.get("platform") or "").strip():
            record_gaps.append("platform")
        if shop_name in {"待确认", "全平台"}:
            record_gaps.append("targetShop")
        if target_date is None:
            record_gaps.append("targetLaunchDate")
        if not str(record.get("content") or "").strip():
            record_gaps.append("notes")
        record_gaps = sorted(set(record_gaps))
        gaps.append({"projectId": project_id, "fields": record_gaps})
        projects.append({
            "id": project_id,
            "productName": _bounded(record.get("title"), "商品名称", 200, required=True),
            "supplierName": "",
            "brand": "",
            "category": "",
            "erpProductCode": "",
            "skuCode": "",
            "spuCode": "",
            "productImageUrl": "",
            "proposedBy": _bounded(record.get("created_by"), "提出人", 320)[:120],
            "proposedDate": proposed_date,
            "owner": owner,
            "targetLaunchDate": target_date,
            "lifecycleStatus": "active",
            "priority": priority,
            "recommendedPriceCents": None,
            "approvedPriceCents": None,
            "estimatedGrossMarginBps": None,
            "source": _bounded(record.get("source"), "来源", 24, required=True),
            "sourceRef": f"legacy-launch:{project_id}",
            "notes": str(record.get("content") or ""),
            "version": int(record.get("version") or 1),
            "createdBy": _bounded(record.get("created_by"), "创建人", 320, required=True),
            "updatedBy": _bounded(record.get("updated_by"), "更新人", 320, required=True),
            "createdAt": created_at.isoformat(),
            "updatedAt": updated_at.isoformat(),
            "deletedAt": _optional_timestamp(record.get("deleted_at")).isoformat() if record.get("deleted_at") else None,
            "deletedBy": _bounded(record.get("deleted_by"), "删除人", 320),
        })
        target_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"teruisi:{project_id}:target:{platform}:{shop_name}"))
        targets.append({
            "id": target_id,
            "projectId": project_id,
            "platform": platform,
            "shopName": shop_name,
            "channel": _bounded(record.get("channel"), "渠道", 80),
            "listingSku": "",
            "listingUrl": "",
            "status": "listed" if status == "已完成" else "pending",
            "createdAt": created_at.isoformat(),
            "updatedAt": updated_at.isoformat(),
        })
        for stage_key, _label in STAGE_DEFINITIONS:
            listing = stage_key == "listing"
            stage_status = LEGACY_STATUS_TO_STAGE[status] if listing else "not_applicable"
            stage_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"teruisi:{project_id}:stage:{stage_key}"))
            stages.append({
                "id": stage_id,
                "projectId": project_id,
                "stageKey": stage_key,
                "status": stage_status,
                "owner": owner if listing else "",
                "plannedDueDate": target_date if listing else None,
                "completedAt": updated_at.isoformat() if listing and stage_status == "completed" else None,
                "blocker": "",
                "notes": "由旧新品上架记录整体状态迁移；旧记录未提供七阶段拆分。" if listing else "",
                "evidenceUrl": "",
                "evidenceLabel": "",
                "version": 1,
                "updatedBy": _bounded(record.get("updated_by"), "更新人", 320, required=True),
                "createdAt": created_at.isoformat(),
                "updatedAt": updated_at.isoformat(),
            })

    mapped_activities: list[dict[str, object]] = []
    activity_ids: set[str] = set()
    for activity in activities:
        activity_id = _uuid(activity.get("id"), "活动标识")
        project_id = _uuid(activity.get("record_id"), "活动项目标识")
        if activity_id in activity_ids or project_id not in project_ids:
            raise CommandError("D1 新品活动标识重复或引用不存在项目")
        activity_ids.add(activity_id)
        detail = _detail(activity.get("detail_json"))
        changed = detail.get("changedFields")
        changed_fields = [str(value)[:120] for value in changed] if isinstance(changed, list) else []
        action = str(activity.get("action") or "")
        mapped_activities.append({
            "id": activity_id,
            "projectId": project_id,
            "action": "project.created" if action == "created" else "project.deleted" if action == "deleted" else "project.updated",
            "actorEmail": _bounded(activity.get("actor_email"), "活动操作者", 320, required=True),
            "actorRole": _bounded(activity.get("actor_role"), "活动角色", 16, required=True),
            "fromVersion": int(activity["from_version"]) if activity.get("from_version") is not None else None,
            "toVersion": int(activity.get("to_version") or 1),
            "stageKey": "",
            "fromStatus": _bounded(detail.get("fromStatus"), "原状态", 24),
            "toStatus": _bounded(detail.get("toStatus"), "新状态", 24),
            "changedFields": changed_fields,
            "createdAt": _timestamp(activity.get("created_at")).isoformat(),
        })

    snapshot = {
        "version": GENERATION_VERSION,
        "projects": sorted(projects, key=lambda row: (row["proposedDate"], row["id"])),
        "targets": sorted(targets, key=lambda row: (row["projectId"], row["platform"], row["shopName"])),
        "stages": sorted(stages, key=lambda row: (row["projectId"], row["stageKey"])),
        "activities": sorted(mapped_activities, key=lambda row: (row["projectId"], row["createdAt"], row["id"])),
        "gaps": sorted(gaps, key=lambda row: row["projectId"]),
    }
    return snapshot


def _target_snapshot() -> dict[str, object]:
    projects = [{
        "id": str(row.id), "productName": row.product_name, "supplierName": row.supplier_name,
        "brand": row.brand, "category": row.category, "erpProductCode": row.erp_product_code,
        "skuCode": row.sku_code, "spuCode": row.spu_code, "productImageUrl": row.product_image_url,
        "proposedBy": row.proposed_by, "proposedDate": row.proposed_date.isoformat(), "owner": row.owner,
        "targetLaunchDate": row.target_launch_date.isoformat() if row.target_launch_date else None,
        "lifecycleStatus": row.lifecycle_status, "priority": row.priority,
        "recommendedPriceCents": row.recommended_price_cents, "approvedPriceCents": row.approved_price_cents,
        "estimatedGrossMarginBps": row.estimated_gross_margin_bps, "source": row.source,
        "sourceRef": row.source_ref, "notes": row.notes, "version": int(row.version),
        "createdBy": row.created_by, "updatedBy": row.updated_by,
        "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
        "updatedAt": row.updated_at.astimezone(datetime_timezone.utc).isoformat(),
        "deletedAt": row.deleted_at.astimezone(datetime_timezone.utc).isoformat() if row.deleted_at else None,
        "deletedBy": row.deleted_by,
    } for row in NewProductProject.objects.order_by("proposed_date", "id")]
    targets = [{
        "id": str(row.id), "projectId": str(row.project_id), "platform": row.platform,
        "shopName": row.shop_name, "channel": row.channel, "listingSku": row.listing_sku,
        "listingUrl": row.listing_url, "status": row.status,
        "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
        "updatedAt": row.updated_at.astimezone(datetime_timezone.utc).isoformat(),
    } for row in NewProductTarget.objects.order_by("project_id", "platform", "shop_name")]
    stages = [{
        "id": str(row.id), "projectId": str(row.project_id), "stageKey": row.stage_key,
        "status": row.status, "owner": row.owner,
        "plannedDueDate": row.planned_due_date.isoformat() if row.planned_due_date else None,
        "completedAt": row.completed_at.astimezone(datetime_timezone.utc).isoformat() if row.completed_at else None,
        "blocker": row.blocker, "notes": row.notes, "evidenceUrl": row.evidence_url,
        "evidenceLabel": row.evidence_label, "version": int(row.version), "updatedBy": row.updated_by,
        "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
        "updatedAt": row.updated_at.astimezone(datetime_timezone.utc).isoformat(),
    } for row in NewProductStage.objects.order_by("project_id", "stage_key")]
    activities = [{
        "id": str(row.id), "projectId": str(row.project_id), "action": row.action,
        "actorEmail": row.actor_email, "actorRole": row.actor_role,
        "fromVersion": int(row.from_version) if row.from_version is not None else None,
        "toVersion": int(row.to_version), "stageKey": row.stage_key,
        "fromStatus": row.from_status, "toStatus": row.to_status,
        "changedFields": row.changed_fields,
        "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
    } for row in NewProductActivity.objects.order_by("project_id", "created_at", "id")]
    gaps = []
    for row in projects:
        if str(row["sourceRef"]).startswith("legacy-launch:"):
            fields = ["supplier", "productCodes", "stageBreakdown"]
            target = next((item for item in targets if item["projectId"] == row["id"]), None)
            if target and target["platform"] == "待确认": fields.append("platform")
            if target and target["shopName"] in {"待确认", "全平台"}: fields.append("targetShop")
            if row["targetLaunchDate"] is None: fields.append("targetLaunchDate")
            if not str(row["notes"]).strip(): fields.append("notes")
            gaps.append({"projectId": row["id"], "fields": sorted(set(fields))})
    return {
        "version": GENERATION_VERSION,
        "projects": projects, "targets": targets, "stages": stages,
        "activities": activities, "gaps": sorted(gaps, key=lambda row: row["projectId"]),
    }


def _counts(snapshot: dict[str, object]) -> dict[str, int]:
    projects = snapshot["projects"]
    return {
        "projects": len(projects),
        "liveProjects": sum(1 for row in projects if row["deletedAt"] is None),
        "deletedProjects": sum(1 for row in projects if row["deletedAt"] is not None),
        "targets": len(snapshot["targets"]),
        "stages": len(snapshot["stages"]),
        "activities": len(snapshot["activities"]),
        "gapProjects": len(snapshot["gaps"]),
    }


class Command(BaseCommand):
    help = "Plan, apply, or verify the legacy launch-record migration into PostgreSQL."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--verify-only", action="store_true")
        parser.add_argument("--approved-run-id", default="")

    def handle(self, *args, **options) -> None:
        if settings.DJANGO_ENVIRONMENT == "production" and settings.DJANGO_PROCESS_ROLE != "migration_writer":
            raise CommandError("生产新品迁移只能由 migration_writer 进程角色执行")
        source_input = Path(str(options["source"])).expanduser()
        if not source_input.is_file() or source_input.is_symlink():
            raise CommandError("D1 新品迁移源必须是普通封存文件")
        source = source_input.resolve()
        snapshot = _source_snapshot(source)
        source_digest = _sha(snapshot)
        counts = _counts(snapshot)
        run_id = f"workflow-{source_digest[:32]}"
        mode = "verify" if options["verify_only"] else "apply" if options["apply"] else "plan"
        approved = str(options.get("approved_run_id") or "").strip()
        if mode == "plan":
            if approved:
                raise CommandError("只读 plan 不接受 approved-run-id")
            self.stdout.write(_json({
                "ok": True, "mode": mode, "runId": run_id,
                "sourceDigest": source_digest, "counts": counts,
                "gaps": snapshot["gaps"],
            }))
            return
        if not RUN_ID_RE.fullmatch(approved) or approved != run_id:
            raise CommandError("approved-run-id 与当前 D1 新品快照不一致")
        if mode == "verify":
            target = _target_snapshot()
            target_digest = _sha(target)
            with transaction.atomic():
                run = WorkflowMigrationRun.objects.select_for_update().filter(id=run_id).first()
                authority = WorkflowWriteAuthority.objects.select_for_update().filter(id=1).first()
                if (
                    run is None or run.status not in {"applied", "verified"}
                    or run.source_snapshot_digest != source_digest
                    or run.source_counts != counts or target_digest != source_digest
                    or _counts(target) != counts or authority is None or authority.status != "disabled"
                ):
                    raise CommandError("PostgreSQL 新品迁移复验不一致")
                run.status = "verified"
                run.target_snapshot_digest = target_digest
                run.target_counts = counts
                run.completed_at = timezone.now()
                run.save()
                authority.migration_verify_run_id = run_id
                authority.save()
            self.stdout.write(_json({"ok": True, "mode": mode, "runId": run_id, "targetDigest": target_digest, "counts": counts}))
            return

        existing = WorkflowMigrationRun.objects.filter(id=run_id, status="verified").first()
        if existing and _sha(_target_snapshot()) == source_digest:
            self.stdout.write(_json({"ok": True, "mode": mode, "status": "duplicate", "runId": run_id, "targetDigest": source_digest, "counts": counts}))
            return
        with transaction.atomic():
            authority = WorkflowWriteAuthority.objects.select_for_update().filter(id=1).first()
            if authority is None or authority.status != "disabled" or authority.migration_verify_run_id:
                raise CommandError("PostgreSQL 新品 authority 不处于空白 disabled 状态")
            if any(model.objects.exists() for model in (
                NewProductProject, NewProductTarget, NewProductStage,
                NewProductActivity, WorkflowWriteRequestReceipt,
            )) or WorkflowMigrationRun.objects.exists():
                raise CommandError("PostgreSQL 新品目标不是空白状态")
            run = WorkflowMigrationRun.objects.create(
                id=run_id, mode="apply", status="applying",
                source_path_digest=_path_digest(source), source_snapshot_digest=source_digest,
                source_counts=counts, gap_counts={
                    key: sum(1 for row in snapshot["gaps"] if key in row["fields"])
                    for key in sorted({field for row in snapshot["gaps"] for field in row["fields"]})
                },
                approved_run_id=approved,
                manifest={"version": GENERATION_VERSION, "sourceDigest": source_digest, "gaps": snapshot["gaps"]},
            )
            NewProductProject.objects.bulk_create([NewProductProject(
                id=row["id"], product_name=row["productName"], supplier_name=row["supplierName"],
                brand=row["brand"], category=row["category"], erp_product_code=row["erpProductCode"],
                sku_code=row["skuCode"], spu_code=row["spuCode"], product_image_url=row["productImageUrl"],
                proposed_by=row["proposedBy"], proposed_date=row["proposedDate"], owner=row["owner"],
                target_launch_date=row["targetLaunchDate"], lifecycle_status=row["lifecycleStatus"], priority=row["priority"],
                recommended_price_cents=row["recommendedPriceCents"], approved_price_cents=row["approvedPriceCents"],
                estimated_gross_margin_bps=row["estimatedGrossMarginBps"], source=row["source"], source_ref=row["sourceRef"],
                notes=row["notes"], version=row["version"], created_by=row["createdBy"], updated_by=row["updatedBy"],
                created_at=_timestamp(row["createdAt"]), deleted_at=_optional_timestamp(row["deletedAt"]), deleted_by=row["deletedBy"],
            ) for row in snapshot["projects"]])
            for row in snapshot["projects"]:
                NewProductProject.objects.filter(id=row["id"]).update(
                    created_at=_timestamp(row["createdAt"]), updated_at=_timestamp(row["updatedAt"])
                )
            NewProductTarget.objects.bulk_create([NewProductTarget(
                id=row["id"], project_id=row["projectId"], platform=row["platform"], shop_name=row["shopName"],
                channel=row["channel"], listing_sku=row["listingSku"], listing_url=row["listingUrl"], status=row["status"],
                created_at=_timestamp(row["createdAt"]),
            ) for row in snapshot["targets"]])
            for row in snapshot["targets"]:
                NewProductTarget.objects.filter(id=row["id"]).update(
                    created_at=_timestamp(row["createdAt"]), updated_at=_timestamp(row["updatedAt"])
                )
            NewProductStage.objects.bulk_create([NewProductStage(
                id=row["id"], project_id=row["projectId"], stage_key=row["stageKey"], status=row["status"],
                owner=row["owner"], planned_due_date=row["plannedDueDate"], completed_at=_optional_timestamp(row["completedAt"]),
                blocker=row["blocker"], notes=row["notes"], evidence_url=row["evidenceUrl"], evidence_label=row["evidenceLabel"],
                version=row["version"], updated_by=row["updatedBy"], created_at=_timestamp(row["createdAt"]),
            ) for row in snapshot["stages"]])
            for row in snapshot["stages"]:
                NewProductStage.objects.filter(id=row["id"]).update(
                    created_at=_timestamp(row["createdAt"]), updated_at=_timestamp(row["updatedAt"])
                )
            NewProductActivity.objects.bulk_create([NewProductActivity(
                id=row["id"], project_id=row["projectId"], action=row["action"], actor_email=row["actorEmail"],
                actor_role=row["actorRole"], from_version=row["fromVersion"], to_version=row["toVersion"],
                stage_key=row["stageKey"], from_status=row["fromStatus"], to_status=row["toStatus"],
                changed_fields=row["changedFields"], created_at=_timestamp(row["createdAt"]),
            ) for row in snapshot["activities"]])
            revision = WorkflowDataRevision.objects.select_for_update().get(domain="workflow")
            revision.revision = 1
            revision.source_digest = source_digest
            revision.save()
            target = _target_snapshot()
            target_digest = _sha(target)
            if target_digest != source_digest or _counts(target) != counts:
                raise CommandError("新品迁移落库摘要回查不一致")
            run.status = "applied"
            run.target_snapshot_digest = target_digest
            run.target_counts = counts
            run.completed_at = timezone.now()
            run.save()
        self.stdout.write(_json({"ok": True, "mode": mode, "status": "applied", "runId": run_id, "targetDigest": source_digest, "counts": counts}))
