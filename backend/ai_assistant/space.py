from __future__ import annotations
import base64
import hashlib
import re
import struct
import zlib
from datetime import timedelta
from django.db.models import Q, Sum
from django.utils import timezone
from sales.auth import Principal
from . import models as m, transport
from .chat import _day
from .configuration import endpoint, profile_record
from .policy import (
    AiError,
    authorize_owner,
    boolean,
    canonical,
    cas,
    choice,
    current_principal,
    digest,
    fields,
    identifier,
    integer,
    mutation,
    owned,
    page,
    record,
    text,
    uid,
)
from .secrets import decrypt
from .workflows import background

SCENES = [
    {
        "id": "product_main",
        "label": "商品主图",
        "description": "纯净商品主体与电商摄影构图",
        "defaultSize": "1024x1024",
    },
    {
        "id": "product_detail",
        "label": "卖点详情",
        "description": "突出可核验卖点与产品细节",
        "defaultSize": "1024x1536",
    },
    {
        "id": "promotion",
        "label": "活动视觉",
        "description": "保留商品主体与活动文案安全留白",
        "defaultSize": "1536x1024",
    },
]
SIZES = ["1024x1024", "1024x1536", "1536x1024"]
LIMITS = {
    "minimumImages": 1,
    "maximumImages": 4,
    "maximumActiveJobsPerOwner": 5,
    "maximumActiveJobsGlobal": 20,
    "maximumDailyImagesPerOwner": 40,
    "maximumJobsPageSize": 50,
    "maximumAssetsPageSize": 60,
    "maximumPromptCharacters": 4000,
    "maximumImageBytes": 6 * 1024 * 1024,
    "maximumProviderResponseBytes": 9 * 1024 * 1024,
    "maximumImagePixels": 1024 * 1536,
    "maximumDailyDispatchesGlobal": 200,
    "maximumDailyDispatchesPerProfile": 100,
    "leaseSeconds": 360,
}
SAFETY = "仅生成商品视觉草稿。不得补充未提供的参数、价格、折扣、销量、认证或功效，不生成真人背书或平台标识；所有输出均为 AI 草稿，发布前必须人工复核。"


def template_record(row):
    return record(
        row,
        "id scene name prompt_template size model_profile_id version is_enabled is_default updated_by created_at updated_at",
        bool_fields={"is_enabled", "is_default"},
    )


def admin_audit(principal, action, entity_type, entity_id, before, after):
    m.AiSpaceAdminAudits.objects.create(
        id=uid("ai-space-audit"),
        actor_email=principal.email.lower(),
        actor_role=principal.role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=canonical(before or {}),
        after_json=canonical(after or {}),
    )


def meta(principal):
    return {
        "scenes": SCENES,
        "templates": [
            {
                k: v
                for k, v in template_record(t).items()
                if k
                in {
                    "id",
                    "scene",
                    "name",
                    "size",
                    "version",
                    "isDefault",
                    "modelProfileId",
                }
            }
            for t in m.AiSpaceTemplates.objects.filter(is_enabled=1).order_by(
                "scene", "-is_default", "id"
            )[:100]
        ],
        "profiles": [
            {
                k: v
                for k, v in profile_record(p).items()
                if k in {"id", "name", "modelName", "lastSuccessAt"}
            }
            for p in m.AiSpaceModelProfiles.objects.filter(status="enabled").order_by(
                "name"
            )[:100]
        ],
        "limits": LIMITS,
        "permissions": {
            "canGenerate": principal.role != "viewer",
            "canManage": principal.role == "admin" and principal.scope is None,
        },
        "safetyNotice": SAFETY,
    }


def save_template(body, principal):
    current_principal(principal, write=True, admin=True)
    fields(
        body,
        {
            "id",
            "expectedVersion",
            "scene",
            "name",
            "promptTemplate",
            "size",
            "modelProfileId",
            "isEnabled",
            "isDefault",
        },
        {"scene", "name", "promptTemplate", "size"},
    )
    row = (
        m.AiSpaceTemplates.objects.filter(id=identifier(body["id"])).first()
        if "id" in body
        else None
    )
    if "id" in body and not row:
        raise AiError("模板不存在", "not_found", 404)
    if row:
        cas(row, body.get("expectedVersion"))
    before = template_record(row) if row else None
    prompt = text(body["promptTemplate"], "promptTemplate", 3000)
    if "{product_name}" not in prompt or set(re.findall(r"\{([^{}]+)\}", prompt)) - {
        "product_name",
        "brand",
        "sku",
        "selling_points",
        "scene",
    }:
        raise AiError("模板变量无效或缺少 {product_name}")
    profile = body.get("modelProfileId") or None
    if (
        profile
        and not m.AiSpaceModelProfiles.objects.filter(id=identifier(profile)).exists()
    ):
        raise AiError("图片模型不存在")
    values = {
        "scene": choice(body["scene"], [s["id"] for s in SCENES], "scene"),
        "name": text(body["name"], "name", 100),
        "prompt_template": prompt,
        "size": choice(body["size"], SIZES, "size"),
        "model_profile_id": profile,
        "is_enabled": boolean(body.get("isEnabled", True), "isEnabled"),
        "is_default": boolean(body.get("isDefault", False), "isDefault"),
        "updated_by": principal.email,
        "updated_at": timezone.now(),
        "version": row.version + 1 if row else 1,
    }
    if values["is_default"]:
        if not values["is_enabled"]:
            raise AiError("默认模板必须启用")
        m.AiSpaceTemplates.objects.filter(scene=values["scene"], is_default=1).update(
            is_default=0
        )
    if row:
        for k, v in values.items():
            setattr(row, k, v)
        row.save()
    else:
        row = m.AiSpaceTemplates.objects.create(id=uid("ai-space-template"), **values)
    after = template_record(row)
    admin_audit(principal, "upsert_template", "template", row.id, before, after)
    return {"item": after}


def asset_record(row, principal, *, jobs=None, favorites=None):
    result = record(
        row, "id job_id item_id scene mime_type byte_size width height created_at"
    )
    job = (
        jobs[row.job_id]
        if jobs is not None
        else m.AiSpaceJobs.objects.get(id=row.job_id)
    )
    result.update(
        productName=job.product_name,
        brand=job.brand,
        sku=job.sku,
        favorite=row.id in favorites
        if favorites is not None
        else m.AiSpaceAssetFavorites.objects.filter(
            asset_id=row.id, actor_email=principal.email.lower()
        ).exists(),
        generatedByAi=True,
        reviewRequired=True,
        contentUrl="/api/ai/space/assets/" + row.id + "/content",
    )
    return result


def job_record(row, principal, *, assets=None, items=None, favorites=None):
    result = record(
        row,
        "id client_request_id scene template_id template_name template_version model_profile_id model_profile_name model_profile_version model_name product_name brand sku selling_points final_prompt size requested_count succeeded_count failed_count cancelled_count status cancel_requested error_code error_message started_at completed_at created_at updated_at",
        bool_fields={"cancel_requested"},
    )
    assets = (
        assets
        if assets is not None
        else {a.id: a for a in m.AiSpaceAssets.objects.filter(job_id=row.id)}
    )
    items = (
        items
        if items is not None
        else m.AiSpaceJobItems.objects.filter(job_id=row.id).order_by("ordinal")[:4]
    )
    if favorites is None:
        favorites = set(
            m.AiSpaceAssetFavorites.objects.filter(
                asset_id__in=assets, actor_email=principal.email.lower()
            ).values_list("asset_id", flat=True)
        )
    result["items"] = []
    for item in items:
        value = record(item, "id ordinal status error_code error_message duration_ms")
        value["asset"] = (
            asset_record(
                assets[item.asset_id],
                principal,
                jobs={row.id: row},
                favorites=favorites,
            )
            if item.asset_id in assets
            else None
        )
        result["items"].append(value)
    return result


def get_job(job_id, principal):
    row = m.AiSpaceJobs.objects.filter(id=identifier(job_id)).first()
    if not row:
        raise AiError("图片任务不存在", "not_found", 404)
    return authorize_owner(row, principal)


def get_asset(asset_id, principal):
    row = m.AiSpaceAssets.objects.filter(id=identifier(asset_id)).first()
    if not row:
        raise AiError("图片资产不存在", "not_found", 404)
    return authorize_owner(row, principal)


def listing(params, principal, *, assets=False):
    fields(
        params,
        {"page", "pageSize", "scene", "favorites", "q"}
        if assets
        else {"page", "pageSize"},
    )
    query = owned(
        m.AiSpaceAssets.objects.all() if assets else m.AiSpaceJobs.objects.all(),
        principal,
    )
    if assets:
        if params.get("scene"):
            query = query.filter(
                scene=choice(params["scene"], [s["id"] for s in SCENES], "scene")
            )
        if params.get("favorites") not in {None, "0", "1"}:
            raise AiError("favorites 必须为 0 或 1")
        if params.get("favorites") == "1":
            query = query.filter(
                id__in=m.AiSpaceAssetFavorites.objects.filter(
                    actor_email=principal.email.lower()
                ).values("asset_id")
            )
        if params.get("q"):
            q = text(params["q"], "q", 100)
            query = query.filter(
                job_id__in=m.AiSpaceJobs.objects.filter(
                    Q(product_name__icontains=q) | Q(sku__icontains=q)
                ).values("id")
            )

    def batch(rows):
        if not rows:
            return []
        jobs = (
            {
                r.id: r
                for r in m.AiSpaceJobs.objects.filter(id__in={r.job_id for r in rows})
            }
            if assets
            else {r.id: r for r in rows}
        )
        asset_rows = (
            {r.id: r for r in rows}
            if assets
            else {r.id: r for r in m.AiSpaceAssets.objects.filter(job_id__in=jobs)}
        )
        favorites = set(
            m.AiSpaceAssetFavorites.objects.filter(
                asset_id__in=asset_rows, actor_email=principal.email.lower()
            ).values_list("asset_id", flat=True)
        )
        if assets:
            return [
                asset_record(r, principal, jobs=jobs, favorites=favorites) for r in rows
            ]
        items = {}
        for item in m.AiSpaceJobItems.objects.filter(job_id__in=jobs).order_by(
            "ordinal"
        ):
            items.setdefault(item.job_id, []).append(item)
        return [
            job_record(
                r,
                principal,
                assets=asset_rows,
                items=items.get(r.id, []),
                favorites=favorites,
            )
            for r in rows
        ]

    return page(
        query.order_by("-created_at", "id"),
        params,
        maximum=60 if assets else 50,
        default=24 if assets else 20,
        batch=batch,
    )


def create(body, principal):
    fields(
        body,
        {
            "clientRequestId",
            "scene",
            "templateId",
            "modelProfileId",
            "productName",
            "brand",
            "sku",
            "sellingPoints",
            "additionalInstructions",
            "count",
        },
        {"clientRequestId", "scene", "templateId", "productName", "count"},
    )
    client = identifier(body["clientRequestId"])
    count = integer(body["count"], "count", 1, 4)
    scene = choice(body["scene"], [s["id"] for s in SCENES], "scene")
    values = {
        k: text(body.get(k, ""), k, limit, empty=k != "productName")
        for k, limit in [
            ("productName", 200),
            ("brand", 100),
            ("sku", 120),
            ("sellingPoints", 800),
            ("additionalInstructions", 800),
        ]
    }
    all_text = "\n".join(values.values())
    if re.search(
        r"忽略.{0,20}(?:规则|要求|指令|安全)|system\s*prompt|developer\s*message|以上.{0,12}(?:无效|作废)|买家秀|真人.{0,8}(?:代言|出镜|模特)|(?:伪造|虚构).{0,12}(?:认证|参数|功效|销量)|(?:添加|写上|标注|展示|突出|生成).{0,16}(?:价格|折扣|销量|认证|功效|平台标识)",
        all_text,
        re.I,
    ):
        raise AiError("图片请求包含禁止或绕过安全约束的内容")
    with mutation(principal):
        existing = m.AiSpaceJobs.objects.filter(
            owner_email=principal.email.lower(), client_request_id=client
        ).first()
        if existing:
            authorize_owner(existing, principal)
            if existing.request_digest != digest(body):
                raise AiError("相同请求标识已用于不同内容", "conflict", 409)
            return {"item": job_record(existing, principal), "replayed": True}
        template = m.AiSpaceTemplates.objects.filter(
            id=identifier(body["templateId"]), scene=scene, is_enabled=1
        ).first()
        if not template:
            raise AiError("模板不可用")
        profile = m.AiSpaceModelProfiles.objects.filter(
            id=body.get("modelProfileId") or template.model_profile_id, status="enabled"
        ).first()
        if not profile or not profile.api_key_encrypted:
            raise AiError("图片模型不可用")
        active = m.AiSpaceJobs.objects.filter(status__in=["queued", "running"])
        daily = (
            m.AiSpaceJobs.objects.filter(
                owner_email=principal.email.lower(), created_at__gte=_day()
            ).aggregate(n=Sum("requested_count"))["n"]
            or 0
        )
        if (
            active.count() >= 20
            or active.filter(owner_email=principal.email.lower()).count() >= 5
            or daily + count > 40
        ):
            raise AiError("图片任务达到配额上限", "rate_limited", 429)
        substitutions = {
            "product_name": values["productName"],
            "brand": values["brand"],
            "sku": values["sku"] or "未提供",
            "selling_points": values["sellingPoints"] or "不补充未提供参数",
            "scene": scene,
        }
        prompt = (
            re.sub(
                r"\{([^{}]+)\}", lambda v: substitutions[v[1]], template.prompt_template
            )
            + "\n"
            + values["additionalInstructions"]
            + "\n"
            + SAFETY
        )
        text(prompt, "finalPrompt", 4000)
        row = m.AiSpaceJobs.objects.create(
            id=uid("ai-space-job"),
            client_request_id=client,
            request_digest=digest(body),
            owner_email=principal.email.lower(),
            scope_json=canonical(principal.scope),
            scene=scene,
            template_id=template.id,
            template_name=template.name,
            template_version=template.version,
            model_profile_id=profile.id,
            model_profile_name=profile.name,
            model_profile_version=profile.version,
            model_name=profile.model_name,
            product_name=values["productName"],
            brand=values["brand"],
            sku=values["sku"],
            selling_points=values["sellingPoints"],
            final_prompt=prompt,
            prompt_digest=digest(prompt),
            size=template.size,
            requested_count=count,
        )
        for ordinal in range(1, count + 1):
            m.AiSpaceJobItems.objects.create(
                id=uid("ai-space-item"), job_id=row.id, ordinal=ordinal
            )
        return {"item": job_record(row, principal), "replayed": False}


def refresh(job):
    statuses = list(
        m.AiSpaceJobItems.objects.filter(job_id=job.id).values_list("status", flat=True)
    )
    job.succeeded_count = statuses.count("succeeded")
    job.failed_count = statuses.count("failed")
    job.cancelled_count = statuses.count("cancelled")
    job.status = (
        "running"
        if "running" in statuses
        else "queued"
        if "queued" in statuses
        else "succeeded"
        if job.succeeded_count == job.requested_count
        else "partial"
        if job.succeeded_count
        else "cancelled"
        if job.cancelled_count == job.requested_count
        else "failed"
    )
    job.updated_at = timezone.now()
    job.completed_at = None if job.status in ["queued", "running"] else timezone.now()
    job.save()


def cancel(job_id, principal):
    row = get_job(job_id, principal)
    row.cancel_requested = 1
    row.save()
    m.AiSpaceJobItems.objects.filter(job_id=row.id, status="queued").update(
        status="cancelled", completed_at=timezone.now()
    )
    refresh(row)
    return {"item": job_record(row, principal)}


def png(bytes_value):
    if len(bytes_value) > 6 * 1024 * 1024 or not bytes_value.startswith(
        b"\x89PNG\r\n\x1a\n"
    ):
        raise AiError("图片必须为有界 PNG")
    offset = 8
    compressed = []
    header = None
    ended = False
    while offset < len(bytes_value):
        if offset + 12 > len(bytes_value):
            raise AiError("PNG 数据不完整")
        length = struct.unpack(">I", bytes_value[offset : offset + 4])[0]
        kind = bytes_value[offset + 4 : offset + 8]
        end = offset + 8 + length
        if (
            end + 4 > len(bytes_value)
            or zlib.crc32(bytes_value[offset + 4 : end]) & 0xFFFFFFFF
            != struct.unpack(">I", bytes_value[end : end + 4])[0]
        ):
            raise AiError("PNG CRC 无效")
        chunk = bytes_value[offset + 8 : end]
        if kind == b"IHDR":
            if header or offset != 8 or length != 13:
                raise AiError("PNG 头无效")
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            compressed.append(chunk)
        elif kind == b"IEND":
            if length or end + 4 != len(bytes_value):
                raise AiError("PNG 结束块无效")
            ended = True
        elif kind[0] & 32 == 0 and kind != b"PLTE":
            raise AiError("不支持的 PNG 关键块")
        offset = end + 4
    if not header or not ended or not compressed:
        raise AiError("PNG 数据不完整")
    width, height, depth, color, compression, filter_method, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if (
        not channels
        or depth != 8
        or compression
        or filter_method
        or interlace
        or not 0 < width * height <= 1024 * 1536
    ):
        raise AiError("PNG 格式或像素数不支持")
    row_bytes = width * channels + 1
    expected = row_bytes * height
    dec = zlib.decompressobj()
    raw = dec.decompress(b"".join(compressed), expected + 1)
    if (
        len(raw) != expected
        or not dec.eof
        or dec.unused_data
        or dec.unconsumed_tail
        or any(raw[i] > 4 for i in range(0, len(raw), row_bytes))
    ):
        raise AiError("PNG 扫描线无效")
    return width, height


def download(asset_id, principal):
    asset = get_asset(asset_id, principal)
    payload = m.AiSpaceAssetPayload.objects.filter(asset_id=asset.id).first()
    if payload is None:
        raise AiError("图片字节缺失", "service_unavailable", 503)
    raw = bytes(payload.content)
    if (
        len(raw) != asset.byte_size
        or hashlib.sha256(raw).hexdigest() != asset.content_sha256
        or png(raw) != (asset.width, asset.height)
    ):
        raise AiError("图片回查失败", "service_unavailable", 503)
    return {
        "base64": base64.b64encode(raw).decode(),
        "mimeType": asset.mime_type,
        "fileName": asset.id + ".png",
    }


def cleanup():
    # Published metadata and payload commit together. No external object can be
    # stranded by cancellation or a lost lease; retain only legacy queue handling.
    with mutation():
        now = timezone.now()
        entry = m.AiSpaceAssetCleanupQueue.objects.order_by("created_at").first()
        if not entry:
            return
        if not re.fullmatch(
            r"ai-space/v1/[A-Za-z0-9_-]{1,160}/[A-Za-z0-9_-]{1,200}\.png",
            entry.object_key,
        ):
            raise AiError("图片清理键不在受控命名空间", "service_unavailable", 503)
        if (
            m.AiSpaceJobItems.objects.filter(pending_object_key=entry.object_key)
            .filter(Q(status__in=["queued", "running"]) | Q(lease_expires_at__gt=now))
            .exists()
        ):
            return
        # Assets and their immutable bytes are never deleted by queue cleanup.
        entry.delete()


def tick():
    cleanup()
    with mutation():
        now = timezone.now()
        # Expired paid dispatches are terminal; only pre-dispatch leases can be reclaimed.
        expired = m.AiSpaceJobItems.objects.filter(
            status="running", lease_expires_at__lte=now
        ).order_by("created_at")[:20]
        for item in expired:
            item.status = "failed" if item.dispatch_started_at else "queued"
            item.error_code = "dispatch_unknown" if item.dispatch_started_at else ""
            item.lease_token = ""
            item.lease_expires_at = None
            item.save()
            if item.pending_object_key:
                m.AiSpaceAssetCleanupQueue.objects.get_or_create(
                    object_key=item.pending_object_key
                )
            refresh(m.AiSpaceJobs.objects.get(id=item.job_id))
    busy = m.AiSpaceJobItems.objects.filter(status="running").values("job_id")
    eligible = m.AiSpaceJobItems.objects.filter(status="queued").exclude(
        job_id__in=busy
    )
    candidate = eligible.order_by("created_at", "ordinal").first()
    if not candidate:
        return {"status": "idle"}
    pre_error = None
    try:
        principal = background(m.AiSpaceJobs.objects.get(id=candidate.job_id))
    except AiError as error:
        pre_error = error
    with mutation():
        now = timezone.now()
        item = eligible.filter(pk=candidate.pk).first()
        if not item:
            return {"status": "idle"}
        job = m.AiSpaceJobs.objects.get(id=item.job_id)
        try:
            if pre_error:
                raise pre_error
            current_principal(principal, write=True)
            if job.cancel_requested:
                raise AiError("任务已取消", "cancelled", 409)
            profile = m.AiSpaceModelProfiles.objects.get(id=job.model_profile_id)
            if (
                profile.status != "enabled"
                or profile.version != job.model_profile_version
            ):
                raise AiError("图片配置已变化", "profile_version_changed", 409)
            endpoint(profile.base_url)
            daily = m.AiSpaceDispatchReceipts.objects.filter(dispatched_at__gte=_day())
            if (
                daily.count() >= 200
                or daily.filter(owner_email=job.owner_email).count() >= 40
                or daily.filter(model_profile_id=profile.id).count() >= 100
            ):
                raise AiError("今日图片实际派发达到上限", "rate_limited", 429)
        except AiError as e:
            item.status = "failed"
            item.error_code = e.code
            item.error_message = str(e)
            item.completed_at = now
            item.save()
            refresh(job)
            return {"status": "failed", "errorCode": e.code}
        item.status = "running"
        item.lease_token = uid("lease")
        item.lease_epoch += 1
        item.lease_expires_at = now + timedelta(seconds=360)
        item.dispatch_started_at = now
        item.started_at = now
        item.attempt_count += 1
        item.save()
        refresh(job)
        dispatch = m.AiSpaceDispatchReceipts.objects.create(
            id=uid("ai-image-dispatch"),
            item_id=item.id,
            job_id=job.id,
            owner_email=job.owner_email,
            actor_role=principal.role,
            model_profile_id=profile.id,
            model_profile_version=profile.version,
            model_name=profile.model_name,
            scene=job.scene,
            size=job.size,
            prompt_digest=job.prompt_digest,
        )
        lease = (item.id, item.lease_token, item.lease_epoch)
    object_key = None
    try:
        body = {
            "model": job.model_name,
            "prompt": job.final_prompt,
            "n": 1,
            "size": job.size,
        }
        if re.fullmatch(r"dall-e-[23]", job.model_name, re.I):
            body["response_format"] = "b64_json"
        result = transport.bounded_json(
            endpoint(profile.base_url) + "/images/generations",
            body,
            {"Authorization": "Bearer " + decrypt(profile.api_key_encrypted)},
            timeout=profile.timeout_ms / 1000,
            maximum=9 * 1024 * 1024,
        )
        if not isinstance(result.get("data"), list) or len(result["data"]) != 1:
            raise AiError("图片生成结果无效")
        raw = base64.b64decode(result["data"][0]["b64_json"], validate=True)
        width, height = png(raw)
        if f"{width}x{height}" != job.size:
            raise AiError("生成尺寸与任务不一致")
        sha = hashlib.sha256(raw).hexdigest()
        object_key = (
            f"ai-space/v1/{job.id}/{item.ordinal}-{item.lease_epoch}-{sha[:16]}.png"
        )
        with mutation(principal, background=True):
            item = _leased(lease, publication=True)
            asset = m.AiSpaceAssets.objects.create(
                id=uid("ai-space-asset"),
                job_id=job.id,
                item_id=item.id,
                owner_email=job.owner_email,
                scope_json=job.scope_json,
                scene=job.scene,
                object_key=object_key,
                content_sha256=sha,
                mime_type="image/png",
                byte_size=len(raw),
                width=width,
                height=height,
            )
            m.AiSpaceAssetPayload.objects.create(asset=asset, content=raw)
            m.AiSpaceDispatchResults.objects.create(
                dispatch_id=dispatch.id,
                status="succeeded",
                usage_json=canonical(result.get("usage", {})),
            )
            item.asset_id = asset.id
            item.status = "succeeded"
            item.completed_at = timezone.now()
            item.duration_ms = int(
                (item.completed_at - item.started_at).total_seconds() * 1000
            )
            item.lease_token = ""
            item.lease_expires_at = None
            item.save()
            refresh(job)
            profile.last_success_result = "真实图片生成成功"
            profile.last_success_at = timezone.now()
            profile.save(update_fields=["last_success_result", "last_success_at"])
        return {"status": "succeeded", "jobId": job.id}
    except Exception as error:
        with mutation():
            if object_key:
                m.AiSpaceAssetCleanupQueue.objects.get_or_create(object_key=object_key)
            try:
                item = _leased(lease)
            except AiError:
                return {"status": "lease_lost"}
            item.status = (
                "cancelled"
                if isinstance(error, AiError) and error.code == "cancelled"
                else "failed"
            )
            item.error_code = (
                error.code if isinstance(error, AiError) else "dispatch_unknown"
            )
            item.error_message = "图片生成或存储回查未获确认，已禁止自动重复付费"
            item.completed_at = timezone.now()
            item.lease_token = ""
            item.lease_expires_at = None
            item.save()
            refresh(job)
            m.AiSpaceDispatchResults.objects.get_or_create(
                dispatch_id=dispatch.id,
                defaults={"status": "failed", "error_code": item.error_code},
            )
        return {"status": "failed", "jobId": job.id}


def _leased(lease, *, publication=False):
    item = m.AiSpaceJobItems.objects.get(id=lease[0])
    if (
        item.status != "running"
        or item.lease_token != lease[1]
        or item.lease_epoch != lease[2]
        or item.lease_expires_at <= timezone.now()
    ):
        raise AiError("图片租约失效", "lease_lost", 409)
    if publication and m.AiSpaceJobs.objects.get(id=item.job_id).cancel_requested:
        raise AiError("图片任务已取消", "cancelled", 409)
    return item
