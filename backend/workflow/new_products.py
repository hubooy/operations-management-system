from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from sales.auth import Principal

from .errors import WorkflowApiError
from .models import NewProductActivity, NewProductProject, NewProductStage, NewProductTarget
from .revisions import bump_revision
from .write_requests import lock_active_authority


STAGE_DEFINITIONS = (
    ("modeling", "建模"),
    ("pricing", "分析定价"),
    ("image", "图片"),
    ("video", "视频"),
    ("listing", "上架"),
    ("stocking", "备货"),
    ("review", "上新复盘"),
)
STAGE_KEYS = {key for key, _label in STAGE_DEFINITIONS}
STAGE_STATUSES = {"not_started", "in_progress", "blocked", "completed", "not_applicable"}
PROJECT_PRIORITIES = {"high", "normal", "low"}
PROJECT_LIFECYCLE_STATUSES = {"active", "paused", "cancelled"}
PROJECT_SOURCES = {"manual", "system", "import", "integration"}
TARGET_STATUSES = {"pending", "ready", "listed", "paused"}
DERIVED_STATUSES = {"not_started", "in_progress", "blocked", "completed", "paused", "cancelled"}
MAX_PROJECT_SCAN = 10_000


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> WorkflowApiError:
    return WorkflowApiError(message, code=code, status=status)


def _strict_object(value: object, allowed: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _error(f"{label}必须是 JSON 对象")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _error(f"{label}包含不支持的字段：{'、'.join(unknown[:5])}")
    return value


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise _error(f"{label}必须是文本")
    result = value.strip()
    if len(result) > maximum:
        raise _error(f"{label}不能超过 {maximum} 个字符")
    if required and not result:
        raise _error(f"{label}不能为空")
    return result


def _calendar_date(value: object, label: str, *, required: bool = False) -> date | None:
    text = _text(value, label, 10, required=required)
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise _error(f"{label}必须为真实的 YYYY-MM-DD 日期") from error
    if parsed.isoformat() != text:
        raise _error(f"{label}必须为真实的 YYYY-MM-DD 日期")
    return parsed


def _integer(value: object, label: str, maximum: int, *, optional: bool = True) -> int | None:
    if value is None or value == "":
        if optional:
            return None
        raise _error(f"{label}不能为空")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise _error(f"{label}必须是 0 至 {maximum} 的整数")
    return value


def _url(value: object, label: str) -> str:
    text = _text(value, label, 1000)
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError as error:
        raise _error(f"{label}不是有效链接") from error
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise _error(f"{label}必须是无账号信息的 http(s) 链接")
    return text


def _choice(value: object, label: str, choices: set[str], fallback: str | None = None) -> str:
    if value is None and fallback is not None:
        return fallback
    text = _text(value, label, 40, required=True)
    if text not in choices:
        raise _error(f"{label}无效")
    return text


def _version(value: object, label: str = "expectedVersion") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 9_007_199_254_740_991:
        raise _error(f"{label}必须是正整数")
    return value


def _normalize_target(value: object) -> dict[str, str]:
    payload = _strict_object(
        value,
        {"platform", "shopName", "channel", "listingSku", "listingUrl", "status"},
        "目标店铺",
    )
    return {
        "platform": _text(payload.get("platform"), "目标平台", 80, required=True),
        "shop_name": _text(payload.get("shopName"), "目标店铺", 160, required=True),
        "channel": _text(payload.get("channel"), "目标渠道", 80),
        "listing_sku": _text(payload.get("listingSku"), "平台 SKU", 160),
        "listing_url": _url(payload.get("listingUrl"), "上架链接"),
        "status": _choice(payload.get("status"), "目标店铺状态", TARGET_STATUSES, "pending"),
    }


def _normalize_targets(value: object, *, required: bool) -> list[dict[str, str]]:
    if value is None:
        if required:
            raise _error("请至少填写一个目标店铺")
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise _error("目标店铺必须是最多 20 项的数组")
    if required and not value:
        raise _error("请至少填写一个目标店铺")
    targets = [_normalize_target(item) for item in value]
    identities = [(item["platform"], item["shop_name"]) for item in targets]
    if len(set(identities)) != len(identities):
        raise _error("同一项目不能重复添加相同平台与店铺")
    return targets


def _normalize_stage_seed(value: object) -> dict[str, object]:
    payload = _strict_object(
        value,
        {"stageKey", "status", "owner", "plannedDueDate", "blocker", "notes", "evidenceUrl", "evidenceLabel"},
        "阶段",
    )
    stage_key = _choice(payload.get("stageKey"), "阶段标识", STAGE_KEYS)
    status = _choice(payload.get("status"), "阶段状态", STAGE_STATUSES, "not_started")
    blocker = _text(payload.get("blocker"), "阻塞原因", 500)
    return {
        "stage_key": stage_key,
        "status": status,
        "owner": _text(payload.get("owner"), "阶段负责人", 120),
        "planned_due_date": _calendar_date(payload.get("plannedDueDate"), "阶段截止日期"),
        "blocker": blocker,
        "notes": _text(payload.get("notes"), "阶段备注", 2_000),
        "evidence_url": _url(payload.get("evidenceUrl"), "阶段证据链接"),
        "evidence_label": _text(payload.get("evidenceLabel"), "阶段证据名称", 160),
    }


def _normalize_stage_seeds(value: object, *, owner: str, target_date: date | None) -> list[dict[str, object]]:
    supplied: dict[str, dict[str, object]] = {}
    if value is not None:
        if not isinstance(value, list) or len(value) > len(STAGE_DEFINITIONS):
            raise _error(f"阶段必须是最多 {len(STAGE_DEFINITIONS)} 项的数组")
        for raw in value:
            item = _normalize_stage_seed(raw)
            key = str(item["stage_key"])
            if key in supplied:
                raise _error("阶段标识不能重复")
            supplied[key] = item
    results: list[dict[str, object]] = []
    for key, _label in STAGE_DEFINITIONS:
        item = supplied.get(key) or {
            "stage_key": key,
            "status": "not_started",
            "owner": owner,
            "planned_due_date": target_date + timedelta(days=7) if key == "review" and target_date else target_date,
            "blocker": "",
            "notes": "",
            "evidence_url": "",
            "evidence_label": "",
        }
        results.append(item)
    return results


def _project_status(project: NewProductProject, stages: list[NewProductStage]) -> str:
    if project.lifecycle_status == "cancelled":
        return "cancelled"
    if project.lifecycle_status == "paused":
        return "paused"
    applicable = [stage for stage in stages if stage.status != "not_applicable"]
    if applicable and all(stage.status == "completed" for stage in applicable):
        return "completed"
    if any(stage.status == "blocked" for stage in applicable):
        return "blocked"
    if any(stage.status in {"in_progress", "completed"} for stage in applicable):
        return "in_progress"
    return "not_started"


def _stage_payload(stage: NewProductStage) -> dict[str, object]:
    return {
        "id": str(stage.id),
        "stageKey": stage.stage_key,
        "label": dict(STAGE_DEFINITIONS).get(stage.stage_key, stage.stage_key),
        "status": stage.status,
        "owner": stage.owner,
        "plannedDueDate": stage.planned_due_date.isoformat() if stage.planned_due_date else None,
        "completedAt": stage.completed_at.isoformat() if stage.completed_at else None,
        "blocker": stage.blocker,
        "notes": stage.notes,
        "evidenceUrl": stage.evidence_url,
        "evidenceLabel": stage.evidence_label,
        "version": int(stage.version),
        "updatedBy": stage.updated_by,
        "updatedAt": stage.updated_at.isoformat(),
    }


def _target_payload(target: NewProductTarget) -> dict[str, object]:
    return {
        "id": str(target.id),
        "platform": target.platform,
        "shopName": target.shop_name,
        "channel": target.channel,
        "listingSku": target.listing_sku,
        "listingUrl": target.listing_url,
        "status": target.status,
    }


def _activity_payload(activity: NewProductActivity) -> dict[str, object]:
    return {
        "id": str(activity.id),
        "action": activity.action,
        "actorEmail": activity.actor_email,
        "actorRole": activity.actor_role,
        "fromVersion": activity.from_version,
        "toVersion": int(activity.to_version),
        "stageKey": activity.stage_key or None,
        "fromStatus": activity.from_status or None,
        "toStatus": activity.to_status or None,
        "changedFields": activity.changed_fields,
        "createdAt": activity.created_at.isoformat(),
    }


def serialize_project(project: NewProductProject, *, include_activity: bool = False) -> dict[str, object]:
    stages = sorted(list(project.stages.all()), key=lambda item: [key for key, _label in STAGE_DEFINITIONS].index(item.stage_key) if item.stage_key in STAGE_KEYS else 999)
    targets = sorted(list(project.targets.all()), key=lambda item: (item.platform, item.shop_name))
    status = _project_status(project, stages)
    applicable = [stage for stage in stages if stage.status != "not_applicable"]
    completed = sum(1 for stage in applicable if stage.status == "completed")
    today = timezone.localdate()
    overdue_stages = [
        stage for stage in applicable
        if stage.status != "completed" and stage.planned_due_date and stage.planned_due_date < today
    ]
    current = next((stage for stage in stages if stage.status == "blocked"), None)
    if current is None:
        current = next((stage for stage in stages if stage.status == "in_progress"), None)
    if current is None:
        current = next((stage for stage in stages if stage.status == "not_started"), None)
    payload: dict[str, object] = {
        "id": str(project.id),
        "productName": project.product_name,
        "supplierName": project.supplier_name,
        "brand": project.brand,
        "category": project.category,
        "erpProductCode": project.erp_product_code,
        "skuCode": project.sku_code,
        "spuCode": project.spu_code,
        "productImageUrl": project.product_image_url,
        "proposedBy": project.proposed_by,
        "proposedDate": project.proposed_date.isoformat(),
        "owner": project.owner,
        "targetLaunchDate": project.target_launch_date.isoformat() if project.target_launch_date else None,
        "lifecycleStatus": project.lifecycle_status,
        "status": status,
        "priority": project.priority,
        "recommendedPriceCents": project.recommended_price_cents,
        "approvedPriceCents": project.approved_price_cents,
        "estimatedGrossMarginBps": project.estimated_gross_margin_bps,
        "source": project.source,
        "sourceRef": project.source_ref,
        "notes": project.notes,
        "shopPlan": project.shop_plan,
        "version": int(project.version),
        "progressPercent": round(completed / len(applicable) * 100) if applicable else 100,
        "currentStageKey": current.stage_key if current else None,
        "overdue": bool(overdue_stages),
        "overdueStageCount": len(overdue_stages),
        "targets": [_target_payload(target) for target in targets],
        "stages": [_stage_payload(stage) for stage in stages],
        "createdBy": project.created_by,
        "updatedBy": project.updated_by,
        "createdAt": project.created_at.isoformat(),
        "updatedAt": project.updated_at.isoformat(),
    }
    if include_activity:
        payload["activity"] = [
            _activity_payload(item)
            for item in project.activities.order_by("-created_at", "-id")[:50]
        ]
    return payload


def _project_queryset():
    return NewProductProject.objects.filter(deleted_at__isnull=True).prefetch_related(
        Prefetch("stages", queryset=NewProductStage.objects.order_by("stage_key")),
        Prefetch("targets", queryset=NewProductTarget.objects.order_by("platform", "shop_name")),
    )


def get_project(project_id: object, *, include_activity: bool = True) -> dict[str, object] | None:
    project = _project_queryset().filter(id=project_id).first()
    return serialize_project(project, include_activity=include_activity) if project else None


def list_projects(options: dict[str, object]) -> dict[str, object]:
    query = _project_queryset()
    needle = str(options.get("query") or "")
    if needle:
        query = query.filter(
            Q(product_name__icontains=needle)
            | Q(supplier_name__icontains=needle)
            | Q(brand__icontains=needle)
            | Q(category__icontains=needle)
            | Q(erp_product_code__icontains=needle)
            | Q(sku_code__icontains=needle)
            | Q(spu_code__icontains=needle)
            | Q(owner__icontains=needle)
            | Q(targets__shop_name__icontains=needle)
            | Q(shop_plan__icontains=needle)
            | Q(notes__icontains=needle)
        ).distinct()
    for key, field in (
        ("suppliers", "supplier_name__in"),
        ("owners", "owner__in"),
        ("categories", "category__in"),
        ("priorities", "priority__in"),
        ("sources", "source__in"),
        ("lifecycle_statuses", "lifecycle_status__in"),
    ):
        values = options.get(key) or []
        if values:
            query = query.filter(**{field: values})
    if options.get("platforms"):
        query = query.filter(targets__platform__in=options["platforms"]).distinct()
    if options.get("shop_names"):
        query = query.filter(targets__shop_name__in=options["shop_names"]).distinct()
    if options.get("proposed_from"):
        query = query.filter(proposed_date__gte=options["proposed_from"])
    if options.get("proposed_to"):
        query = query.filter(proposed_date__lt=options["proposed_to"])
    if options.get("due_from"):
        query = query.filter(target_launch_date__gte=options["due_from"])
    if options.get("due_to"):
        query = query.filter(target_launch_date__lt=options["due_to"])
    if query.count() > MAX_PROJECT_SCAN:
        raise _error("新品项目结果超过 10,000 项，请缩小筛选范围", code="payload_too_large", status=413)
    projects = list(query.order_by("-updated_at", "-id"))
    items = [serialize_project(project) for project in projects]
    statuses = options.get("statuses") or []
    stage_key = str(options.get("stage_key") or "")
    stage_statuses = set(options.get("stage_statuses") or [])
    if stage_key:
        items = [
            item for item in items
            if any(
                stage["stageKey"] == stage_key and (not stage_statuses or stage["status"] in stage_statuses)
                for stage in item["stages"]
            )
        ]
    elif stage_statuses:
        items = [item for item in items if any(stage["status"] in stage_statuses for stage in item["stages"])]

    quick_summary = {
        "total": len(items),
        "inProgress": sum(item["status"] == "in_progress" for item in items),
        "blocked": sum(item["status"] == "blocked" for item in items),
        "completed": sum(item["status"] == "completed" for item in items),
        "overdue": sum(bool(item["overdue"]) for item in items),
    }
    if statuses:
        items = [item for item in items if item["status"] in statuses]
    if options.get("overdue"):
        items = [item for item in items if item["overdue"]]

    status_counts = {value: 0 for value in DERIVED_STATUSES}
    overdue = 0
    stage_summary = {
        key: {"stageKey": key, "label": label, **{status: 0 for status in STAGE_STATUSES}}
        for key, label in STAGE_DEFINITIONS
    }
    for item in items:
        status_counts[str(item["status"])] += 1
        overdue += 1 if item["overdue"] else 0
        for stage in item["stages"]:
            stage_summary[stage["stageKey"]][stage["status"]] += 1

    total = len(items)
    page = int(options["page"])
    page_size = int(options["page_size"])
    offset = (page - 1) * page_size
    paged = items[offset : offset + page_size]
    platforms = sorted({str(target["platform"]) for item in items for target in item["targets"] if target["platform"]})
    shop_names = sorted({str(target["shopName"]) for item in items for target in item["targets"] if target["shopName"]})
    return {
        "items": paged,
        "quickSummary": quick_summary,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "returned": len(paged),
            "truncated": offset + len(paged) < total,
        },
        "summary": {
            "total": total,
            "notStarted": status_counts["not_started"],
            "inProgress": status_counts["in_progress"],
            "blocked": status_counts["blocked"],
            "completed": status_counts["completed"],
            "paused": status_counts["paused"],
            "cancelled": status_counts["cancelled"],
            "overdue": overdue,
            "stageSummary": list(stage_summary.values()),
        },
        "facets": {
            "suppliers": sorted({str(item["supplierName"]) for item in items if item["supplierName"]}),
            "owners": sorted({str(item["owner"]) for item in items if item["owner"]}),
            "categories": sorted({str(item["category"]) for item in items if item["category"]}),
            "platforms": platforms,
            "shopNames": shop_names,
            "sources": sorted(PROJECT_SOURCES),
        },
        "filtersApplied": {
            "query": needle,
            "statuses": statuses,
            "suppliers": options.get("suppliers") or [],
            "owners": options.get("owners") or [],
            "categories": options.get("categories") or [],
            "platforms": options.get("platforms") or [],
            "shopNames": options.get("shop_names") or [],
            "stageKey": stage_key or None,
            "stageStatuses": sorted(stage_statuses),
        },
    }


def search_projects(query_text: str, *, offset: int, limit: int) -> dict[str, object]:
    """Return the bounded projection used by global search and other readers.

    This deliberately exposes a fixed result shape instead of accepting SQL-like
    fields from callers.  The result is ordered independently of legacy D1
    workflow records; the edge search layer combines the two authorities using
    a stable source order during the gradual workflow migration.
    """
    rows = _project_queryset().filter(
        Q(product_name__icontains=query_text)
        | Q(supplier_name__icontains=query_text)
        | Q(brand__icontains=query_text)
        | Q(category__icontains=query_text)
        | Q(erp_product_code__icontains=query_text)
        | Q(sku_code__icontains=query_text)
        | Q(spu_code__icontains=query_text)
        | Q(proposed_by__icontains=query_text)
        | Q(owner__icontains=query_text)
        | Q(source_ref__icontains=query_text)
        | Q(notes__icontains=query_text)
        | Q(shop_plan__icontains=query_text)
        | Q(targets__platform__icontains=query_text)
        | Q(targets__shop_name__icontains=query_text)
        | Q(targets__listing_sku__icontains=query_text)
    ).distinct().order_by("-updated_at", "-id")
    total = rows.count()
    if total > MAX_PROJECT_SCAN:
        raise _error("新品项目搜索结果超过 10,000 项，请缩小关键词范围", code="payload_too_large", status=413)
    page = list(rows[offset : offset + limit])
    status_labels = {
        "not_started": "待开始", "in_progress": "进行中", "blocked": "阻塞",
        "completed": "已完成", "paused": "已暂停", "cancelled": "已取消",
    }
    items: list[dict[str, object]] = []
    for project in page:
        payload = serialize_project(project)
        subtitle_parts = [
            str(payload.get("supplierName") or ""),
            str(payload.get("category") or ""),
            status_labels.get(str(payload["status"]), str(payload["status"])),
        ]
        code = str(payload.get("skuCode") or payload.get("erpProductCode") or payload.get("spuCode") or "")
        shops = "、".join(
            f"{target['platform']}/{target['shopName']}"
            for target in payload["targets"][:3]
        )
        detail_parts = [code, str(payload.get("owner") or ""), payload.get("shopPlan") if payload.get("shopPlan") is not None else shops, str(payload.get("notes") or "")]
        amount = payload.get("approvedPriceCents")
        if amount is None:
            amount = payload.get("recommendedPriceCents")
        items.append({
            "id": str(payload["id"])[:160],
            "title": str(payload["productName"])[:200],
            "subtitle": " · ".join(value for value in subtitle_parts if value)[:240],
            "detail": " · ".join(value for value in detail_parts if value)[:400],
            "updatedAt": str(payload["updatedAt"])[:48],
            "amountCents": amount,
        })
    return {"items": items, "total": total, "truncated": offset + len(items) < total}


CREATE_FIELDS = {
    "productName", "supplierName", "brand", "category", "erpProductCode", "skuCode", "spuCode",
    "productImageUrl", "proposedBy", "proposedDate", "owner", "targetLaunchDate", "lifecycleStatus",
    "priority", "recommendedPriceCents", "approvedPriceCents", "estimatedGrossMarginBps", "source",
    "sourceRef", "notes", "shopPlan", "targets", "stages",
}
UPDATE_FIELDS = (CREATE_FIELDS - {"stages"}) | {"expectedVersion"}


def _normalized_project_fields(payload: dict[str, object], *, partial: bool) -> dict[str, Any]:
    output: dict[str, Any] = {}
    mappings = (
        ("productName", "product_name", "商品名称", 200, not partial),
        ("supplierName", "supplier_name", "供应商", 200, False),
        ("brand", "brand", "品牌", 120, False),
        ("category", "category", "品类", 120, False),
        ("erpProductCode", "erp_product_code", "ERP 货品编码", 160, False),
        ("skuCode", "sku_code", "SKU 编码", 160, False),
        ("spuCode", "spu_code", "SPU 编码", 160, False),
        ("proposedBy", "proposed_by", "提出人", 120, False),
        ("owner", "owner", "项目负责人", 120, False),
        ("sourceRef", "source_ref", "来源引用", 200, False),
        ("notes", "notes", "项目备注", 4_000, False),
    )
    for public, internal, label, maximum, required in mappings:
        if public in payload or not partial:
            output[internal] = _text(payload.get(public), label, maximum, required=required)
    if "shopPlan" in payload:
        output["shop_plan"] = _text(payload["shopPlan"], "店铺规划", 4_000)
    if "productImageUrl" in payload or not partial:
        output["product_image_url"] = _url(payload.get("productImageUrl"), "商品图片链接")
    if "proposedDate" in payload or not partial:
        output["proposed_date"] = _calendar_date(payload.get("proposedDate"), "提出日期", required=True)
    if "targetLaunchDate" in payload or not partial:
        output["target_launch_date"] = _calendar_date(payload.get("targetLaunchDate"), "目标上架日期")
    if "lifecycleStatus" in payload or not partial:
        output["lifecycle_status"] = _choice(payload.get("lifecycleStatus"), "项目生命周期状态", PROJECT_LIFECYCLE_STATUSES, "active")
    if "priority" in payload or not partial:
        output["priority"] = _choice(payload.get("priority"), "优先级", PROJECT_PRIORITIES, "normal")
    if "source" in payload or not partial:
        output["source"] = _choice(payload.get("source"), "来源", PROJECT_SOURCES, "manual")
    if "recommendedPriceCents" in payload or not partial:
        output["recommended_price_cents"] = _integer(payload.get("recommendedPriceCents"), "建议售价（分）", 10_000_000_000_000)
    if "approvedPriceCents" in payload or not partial:
        output["approved_price_cents"] = _integer(payload.get("approvedPriceCents"), "核准售价（分）", 10_000_000_000_000)
    if "estimatedGrossMarginBps" in payload or not partial:
        output["estimated_gross_margin_bps"] = _integer(payload.get("estimatedGrossMarginBps"), "预估毛利率（基点）", 10_000)
    target_date = output.get("target_launch_date")
    proposed_date = output.get("proposed_date")
    if target_date and proposed_date and target_date < proposed_date:
        raise _error("目标上架日期不能早于提出日期")
    return output


def create_project(payload: object, principal: Principal) -> dict[str, object]:
    data = _strict_object(payload, CREATE_FIELDS, "新品项目")
    fields = _normalized_project_fields(data, partial=False)
    targets = _normalize_targets(data.get("targets"), required="shopPlan" not in data)
    stages = _normalize_stage_seeds(
        data.get("stages"),
        owner=str(fields.get("owner") or ""),
        target_date=fields.get("target_launch_date"),
    )
    if fields["source"] != "manual" and principal.role != "admin":
        raise _error("只有管理员可以创建非手工来源的新品项目", code="access_denied", status=403)
    actor = principal.email.strip().lower()
    with transaction.atomic():
        lock_active_authority()
        project = NewProductProject.objects.create(**fields, created_by=actor, updated_by=actor)
        NewProductTarget.objects.bulk_create([NewProductTarget(project=project, **target) for target in targets])
        now = timezone.now()
        NewProductStage.objects.bulk_create([
            NewProductStage(
                project=project,
                **stage,
                completed_at=now if stage["status"] == "completed" else None,
                updated_by=actor,
            )
            for stage in stages
        ])
        NewProductActivity.objects.create(
            project=project,
            action="project.created",
            actor_email=actor,
            actor_role=principal.role,
            from_version=None,
            to_version=1,
            changed_fields=sorted(CREATE_FIELDS - {"notes"}),
        )
        bump_revision({"action": "project.created", "projectId": str(project.id), "version": 1})
    result = get_project(project.id)
    if result is None:
        raise RuntimeError("workflow_new_product_create_readback_failed")
    return result


def update_project(project_id: object, payload: object, principal: Principal) -> dict[str, object]:
    data = _strict_object(payload, UPDATE_FIELDS, "新品项目更新")
    expected = _version(data.get("expectedVersion"))
    changed_public = set(data) - {"expectedVersion"}
    if not changed_public:
        raise _error("缺少可更新的新品项目字段")
    fields = _normalized_project_fields(data, partial=True)
    target_update = "targets" in data
    targets = _normalize_targets(data.get("targets"), required=True) if target_update else []
    actor = principal.email.strip().lower()
    with transaction.atomic():
        lock_active_authority()
        project = NewProductProject.objects.select_for_update().filter(id=project_id, deleted_at__isnull=True).first()
        if project is None:
            raise _error("新品项目不存在或已删除", code="not_found", status=404)
        if int(project.version) != expected:
            raise _error("新品项目已被其他人更新，请刷新后重试", code="version_conflict", status=409)
        if fields.get("source", project.source) != "manual" and principal.role != "admin":
            raise _error("只有管理员可以设置非手工来源", code="access_denied", status=403)
        proposed_date = fields.get("proposed_date", project.proposed_date)
        target_date = fields.get("target_launch_date", project.target_launch_date)
        if target_date and proposed_date and target_date < proposed_date:
            raise _error("目标上架日期不能早于提出日期")
        actual_changes: list[str] = []
        public_by_internal = {
            "product_name": "productName", "supplier_name": "supplierName", "brand": "brand",
            "category": "category", "erp_product_code": "erpProductCode", "sku_code": "skuCode",
            "spu_code": "spuCode", "product_image_url": "productImageUrl", "proposed_by": "proposedBy",
            "proposed_date": "proposedDate", "owner": "owner", "target_launch_date": "targetLaunchDate",
            "lifecycle_status": "lifecycleStatus", "priority": "priority", "recommended_price_cents": "recommendedPriceCents",
            "approved_price_cents": "approvedPriceCents", "estimated_gross_margin_bps": "estimatedGrossMarginBps",
            "source": "source", "source_ref": "sourceRef", "notes": "notes", "shop_plan": "shopPlan",
        }
        for field, value in fields.items():
            if getattr(project, field) != value:
                setattr(project, field, value)
                actual_changes.append(public_by_internal[field])
        if target_update:
            current = sorted(
                (target.platform, target.shop_name, target.channel, target.listing_sku, target.listing_url, target.status)
                for target in project.targets.all()
            )
            replacement = sorted(
                (target["platform"], target["shop_name"], target["channel"], target["listing_sku"], target["listing_url"], target["status"])
                for target in targets
            )
            if current != replacement:
                project.targets.all().delete()
                NewProductTarget.objects.bulk_create([NewProductTarget(project=project, **target) for target in targets])
                actual_changes.append("targets")
        if not actual_changes:
            raise _error("新品项目没有发生变化")
        before = int(project.version)
        project.version = before + 1
        project.updated_by = actor
        project.save()
        NewProductActivity.objects.create(
            project=project,
            action="project.updated",
            actor_email=actor,
            actor_role=principal.role,
            from_version=before,
            to_version=project.version,
            changed_fields=sorted(actual_changes),
        )
        bump_revision({"action": "project.updated", "projectId": str(project.id), "version": int(project.version)})
    result = get_project(project.id)
    if result is None:
        raise RuntimeError("workflow_new_product_update_readback_failed")
    return result


def update_stage(project_id: object, stage_key: str, payload: object, principal: Principal) -> dict[str, object]:
    if stage_key not in STAGE_KEYS:
        raise _error("阶段标识无效")
    data = _strict_object(
        payload,
        {"status", "owner", "plannedDueDate", "blocker", "notes", "evidenceUrl", "evidenceLabel", "expectedVersion"},
        "新品阶段更新",
    )
    expected = _version(data.get("expectedVersion"))
    editable = set(data) - {"expectedVersion"}
    if not editable:
        raise _error("缺少可更新的阶段字段")
    normalized: dict[str, object] = {}
    if "status" in data:
        normalized["status"] = _choice(data.get("status"), "阶段状态", STAGE_STATUSES)
    if "owner" in data:
        normalized["owner"] = _text(data.get("owner"), "阶段负责人", 120)
    if "plannedDueDate" in data:
        normalized["planned_due_date"] = _calendar_date(data.get("plannedDueDate"), "阶段截止日期")
    if "blocker" in data:
        normalized["blocker"] = _text(data.get("blocker"), "阻塞原因", 500)
    if "notes" in data:
        normalized["notes"] = _text(data.get("notes"), "阶段备注", 2_000)
    if "evidenceUrl" in data:
        normalized["evidence_url"] = _url(data.get("evidenceUrl"), "阶段证据链接")
    if "evidenceLabel" in data:
        normalized["evidence_label"] = _text(data.get("evidenceLabel"), "阶段证据名称", 160)
    actor = principal.email.strip().lower()
    with transaction.atomic():
        lock_active_authority()
        project = NewProductProject.objects.select_for_update().filter(id=project_id, deleted_at__isnull=True).first()
        if project is None:
            raise _error("新品项目不存在或已删除", code="not_found", status=404)
        stage = NewProductStage.objects.select_for_update().filter(project=project, stage_key=stage_key).first()
        if stage is None:
            raise _error("新品阶段不存在", code="not_found", status=404)
        if int(stage.version) != expected:
            raise _error("新品阶段已被其他人更新，请刷新后重试", code="version_conflict", status=409)
        next_status = str(normalized.get("status", stage.status))
        if next_status != "blocked" and "blocker" not in normalized and stage.blocker:
            normalized["blocker"] = ""
        changed: list[str] = []
        public_by_internal = {
            "status": "status", "owner": "owner", "planned_due_date": "plannedDueDate",
            "blocker": "blocker", "notes": "notes", "evidence_url": "evidenceUrl", "evidence_label": "evidenceLabel",
        }
        previous_status = stage.status
        for field, value in normalized.items():
            if getattr(stage, field) != value:
                setattr(stage, field, value)
                changed.append(public_by_internal[field])
        if not changed:
            raise _error("新品阶段没有发生变化")
        if previous_status != stage.status:
            stage.completed_at = timezone.now() if stage.status == "completed" else None
        stage.version = int(stage.version) + 1
        stage.updated_by = actor
        stage.save()
        project_before = int(project.version)
        project.version = project_before + 1
        project.updated_by = actor
        project.save(update_fields=["version", "updated_by", "updated_at"])
        NewProductActivity.objects.create(
            project=project,
            action="stage.updated",
            actor_email=actor,
            actor_role=principal.role,
            from_version=project_before,
            to_version=project.version,
            stage_key=stage_key,
            from_status=previous_status,
            to_status=stage.status,
            changed_fields=sorted(changed),
        )
        bump_revision({"action": "stage.updated", "projectId": str(project.id), "stageKey": stage_key, "version": int(project.version)})
    result = get_project(project.id)
    if result is None:
        raise RuntimeError("workflow_new_product_stage_readback_failed")
    return result


def delete_project(project_id: object, expected_version: object, principal: Principal) -> dict[str, object]:
    expected = _version(expected_version)
    actor = principal.email.strip().lower()
    with transaction.atomic():
        lock_active_authority()
        project = NewProductProject.objects.select_for_update().filter(id=project_id, deleted_at__isnull=True).first()
        if project is None:
            raise _error("新品项目不存在或已删除", code="not_found", status=404)
        if int(project.version) != expected:
            raise _error("新品项目已被其他人更新，请刷新后重试", code="version_conflict", status=409)
        before = int(project.version)
        project.version = before + 1
        project.deleted_at = timezone.now()
        project.deleted_by = actor
        project.updated_by = actor
        project.save(update_fields=["version", "deleted_at", "deleted_by", "updated_by", "updated_at"])
        NewProductActivity.objects.create(
            project=project,
            action="project.deleted",
            actor_email=actor,
            actor_role=principal.role,
            from_version=before,
            to_version=project.version,
            changed_fields=["deletedAt"],
        )
        bump_revision({"action": "project.deleted", "projectId": str(project.id), "version": int(project.version)})
    return {"ok": True, "id": str(project.id), "version": int(project.version)}
