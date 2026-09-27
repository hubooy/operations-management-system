from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import date

from django.http import HttpRequest, JsonResponse
from django.db import transaction
from django.views.decorators.http import require_POST, require_http_methods

from sales.auth import Principal, PrincipalEnvelopeError, verify_principal

from .errors import WorkflowApiError
from .consumers import execute_consumer_query, validate_consumer_request
from .followup import (
    create_product_line,
    get_product_line_image,
    get_report_config,
    learn_product_line_codes,
    preview_product_line_codes,
    list_product_lines,
    update_product_line,
    update_report_config,
    weekly_followup,
)
from .new_products import (
    DERIVED_STATUSES,
    PROJECT_LIFECYCLE_STATUSES,
    PROJECT_PRIORITIES,
    PROJECT_SOURCES,
    STAGE_KEYS,
    STAGE_STATUSES,
    create_project,
    delete_project,
    get_project,
    list_projects,
    update_project,
    update_stage,
)
from .revisions import revision_value
from .write_requests import claim_write_request, complete_write_request, fail_write_request


logger = logging.getLogger(__name__)
JSON_CONTENT_TYPE_RE = re.compile(r"^(?:application/json|application/[a-z0-9.+-]+\+json)(?:\s*;|$)", re.I)


def _json(payload: object, status: int = 200, *, revision: str | None = None, replayed: bool = False) -> JsonResponse:
    response = JsonResponse(payload, status=status, safe=not isinstance(payload, list), json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    if revision is not None and 200 <= status < 300:
        response["X-Workflow-Data-Revision"] = revision
    if replayed:
        response["X-Teruisi-Write-Replay"] = "1"
    return response


def _error(error: Exception, fallback: str) -> JsonResponse:
    if isinstance(error, PrincipalEnvelopeError):
        return _json({"error": str(error), "code": error.code}, error.status)
    if isinstance(error, WorkflowApiError):
        return _json({"error": str(error), "code": error.code}, error.status)
    logger.exception("Unhandled workflow API error")
    return _json({"error": fallback, "code": "internal_error"}, 500)


def _principal(
    request: HttpRequest,
    roles: set[str],
    *,
    unrestricted: bool = True,
) -> Principal:
    principal = verify_principal(request)
    if principal.role not in roles:
        raise PrincipalEnvelopeError("当前角色无权访问", status=403, code="insufficient_role")
    if unrestricted and principal.scope is not None:
        raise PrincipalEnvelopeError("运营事务接口仅支持未受限数据范围账号", status=403, code="access_denied")
    return principal


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowApiError("请求 JSON 包含重复字段")
        result[key] = value
    return result


def _body(request: HttpRequest) -> dict[str, object]:
    if not JSON_CONTENT_TYPE_RE.match(request.headers.get("Content-Type", "")):
        raise WorkflowApiError("Django 运营事务接口只接受 application/json", status=415)
    try:
        payload = json.loads(
            request.body.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(WorkflowApiError("请求 JSON 包含非有限数字")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowApiError("请求内容不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise WorkflowApiError("请求内容必须是 JSON 对象")
    return payload


def _consistent_read(loader: Callable[[], dict[str, object] | None]) -> tuple[dict[str, object] | None, str]:
    for _attempt in range(2):
        before = revision_value()
        payload = loader()
        after = revision_value()
        if before == after:
            return payload, after
    raise WorkflowApiError("运营事务数据版本持续变化，请稍后重试", code="service_unavailable", status=503)


def _replay_write(
    request: HttpRequest,
    principal: Principal,
    callback: Callable[[], tuple[dict[str, object], int]],
    *,
    authority_scope: str = "launch",
) -> JsonResponse:
    claim = claim_write_request(
        request_id=request.headers.get("X-Teruisi-Request-Id", "").strip(),
        actor_email=principal.email.strip().lower(),
        method=request.method,
        path=request.path,
        body_sha256=request.headers.get("X-Teruisi-Content-SHA256", "").strip().lower(),
        query_sha256=hashlib.sha256(request.META.get("QUERY_STRING", "").encode()).hexdigest(),
        authority_scope=authority_scope,
    )
    if claim.replay_payload is not None and claim.replay_status is not None:
        return _json(claim.replay_payload, claim.replay_status, revision=revision_value(), replayed=True)
    try:
        # Keep the domain mutation and its completed replay receipt in one
        # transaction. A crash can therefore leave a retryable claim, but
        # never committed business data paired with an incomplete receipt.
        with transaction.atomic():
            payload, status = callback()
            complete_write_request(claim, response_status=status, response_payload=payload)
        return _json(payload, status, revision=revision_value())
    except Exception:
        fail_write_request(claim)
        raise


def _unknown(request: HttpRequest, allowed: set[str], label: str) -> None:
    if any(key not in allowed for key in request.GET):
        raise WorkflowApiError(f"{label}包含未知查询参数")


def _one(request: HttpRequest, key: str) -> str | None:
    values = request.GET.getlist(key)
    if len(values) > 1:
        raise WorkflowApiError(f"{key} 参数不能重复")
    return values[0] if values else None


def _positive(value: str | None, fallback: int, label: str, maximum: int) -> int:
    if value is None:
        return fallback
    if not re.fullmatch(r"[1-9]\d*", value) or int(value) > maximum:
        raise WorkflowApiError(f"{label} 超出允许范围")
    return int(value)


def _date(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise WorkflowApiError(f"{label}必须为真实的 YYYY-MM-DD 日期") from error
    if parsed.isoformat() != value:
        raise WorkflowApiError(f"{label}必须为真实的 YYYY-MM-DD 日期")
    return parsed


def _selections(request: HttpRequest, key: str, maximum: int, allowed: set[str] | None = None) -> list[str]:
    values = list(dict.fromkeys(value.strip() for value in request.GET.getlist(key) if value.strip()))
    if len(values) > maximum or any(len(value) > 200 for value in values) or (allowed and any(value not in allowed for value in values)):
        raise WorkflowApiError(f"{key} 筛选无效")
    return values


def _list_options(request: HttpRequest) -> dict[str, object]:
    _unknown(
        request,
        {
            "q", "query", "status", "supplier", "owner", "category", "platform", "shopName",
            "priority", "source", "lifecycleStatus", "stage", "stageStatus", "proposedFrom",
            "proposedTo", "dueFrom", "dueTo", "page", "pageSize", "overdue",
        },
        "新品项目列表",
    )
    query_values = [value for key in ("q", "query") for value in request.GET.getlist(key)]
    if len(query_values) > 1:
        raise WorkflowApiError("搜索词参数不能重复")
    query = query_values[0].strip() if query_values else ""
    if len(query) > 100:
        raise WorkflowApiError("搜索词不能超过 100 个字符")
    stage = _one(request, "stage") or ""
    if stage and stage not in STAGE_KEYS:
        raise WorkflowApiError("stage 筛选无效")
    proposed_from = _date(_one(request, "proposedFrom"), "提出开始日期")
    proposed_to = _date(_one(request, "proposedTo"), "提出结束日期")
    due_from = _date(_one(request, "dueFrom"), "上架开始日期")
    due_to = _date(_one(request, "dueTo"), "上架结束日期")
    if proposed_from and proposed_to and proposed_from >= proposed_to:
        raise WorkflowApiError("提出日期范围必须满足开始日期早于结束日期")
    if due_from and due_to and due_from >= due_to:
        raise WorkflowApiError("上架日期范围必须满足开始日期早于结束日期")
    overdue = _one(request, "overdue")
    if overdue not in (None, "true", "false"):
        raise WorkflowApiError("overdue 必须为 true 或 false")
    return {
        "overdue": overdue == "true",
        "query": query,
        "statuses": _selections(request, "status", 10, DERIVED_STATUSES),
        "suppliers": _selections(request, "supplier", 20),
        "owners": _selections(request, "owner", 20),
        "categories": _selections(request, "category", 20),
        "platforms": _selections(request, "platform", 20),
        "shop_names": _selections(request, "shopName", 20),
        "priorities": _selections(request, "priority", 3, PROJECT_PRIORITIES),
        "sources": _selections(request, "source", 4, PROJECT_SOURCES),
        "lifecycle_statuses": _selections(request, "lifecycleStatus", 3, PROJECT_LIFECYCLE_STATUSES),
        "stage_key": stage,
        "stage_statuses": _selections(request, "stageStatus", 5, STAGE_STATUSES),
        "proposed_from": proposed_from,
        "proposed_to": proposed_to,
        "due_from": due_from,
        "due_to": due_to,
        "page": _positive(_one(request, "page"), 1, "page", 10_000),
        "page_size": _positive(_one(request, "pageSize"), 50, "pageSize", 100),
    }


@require_http_methods(["GET", "POST"])
def launch_projects(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            payload, revision = _consistent_read(lambda: list_projects(_list_options(request)))
            return _json(payload, revision=revision)
        principal = _principal(request, {"operator", "admin"})
        payload = _body(request)
        return _replay_write(request, principal, lambda: ({"item": create_project(payload, principal)}, 201))
    except Exception as error:
        return _error(error, "新品项目处理失败")


@require_http_methods(["GET", "PATCH", "DELETE"])
def launch_project(request: HttpRequest, project_id: object) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            _unknown(request, set(), "新品项目详情")
            payload, revision = _consistent_read(lambda: get_project(project_id))
            if payload is None:
                raise WorkflowApiError("新品项目不存在或已删除", code="not_found", status=404)
            return _json({"item": payload}, revision=revision)
        principal = _principal(request, {"operator", "admin"})
        if request.method == "PATCH":
            payload = _body(request)
            return _replay_write(request, principal, lambda: ({"item": update_project(project_id, payload, principal)}, 200))
        _unknown(request, {"expectedVersion"}, "删除新品项目")
        expected = _positive(_one(request, "expectedVersion"), 0, "expectedVersion", 9_007_199_254_740_991)
        return _replay_write(request, principal, lambda: (delete_project(project_id, expected, principal), 200))
    except Exception as error:
        return _error(error, "新品项目处理失败")


@require_http_methods(["PATCH"])
def launch_project_stage(request: HttpRequest, project_id: object, stage_key: str) -> JsonResponse:
    try:
        principal = _principal(request, {"operator", "admin"})
        payload = _body(request)
        return _replay_write(
            request,
            principal,
            lambda: ({"item": update_stage(project_id, stage_key, payload, principal)}, 200),
        )
    except Exception as error:
        return _error(error, "新品阶段更新失败")


@require_http_methods(["GET", "POST"])
def new_product_lines(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            _unknown(request, set(), "新品产品线列表")
            payload, revision = _consistent_read(list_product_lines)
            return _json(payload, revision=revision)
        principal = _principal(request, {"operator", "admin"})
        payload = _body(request)
        return _replay_write(request, principal, lambda: ({"item": create_product_line(payload, principal)}, 201))
    except Exception as error:
        return _error(error, "新品产品线处理失败")


@require_http_methods(["PATCH"])
def new_product_line(request: HttpRequest, line_id: object) -> JsonResponse:
    try:
        principal = _principal(request, {"operator", "admin"})
        payload = _body(request)
        return _replay_write(
            request,
            principal,
            lambda: ({"item": update_product_line(line_id, payload, principal)}, 200),
        )
    except Exception as error:
        return _error(error, "新品产品线更新失败")


@require_http_methods(["GET"])
def new_product_line_image(request: HttpRequest, line_id: object) -> JsonResponse:
    try:
        _principal(request, {"viewer", "analyst", "operator", "admin"})
        _unknown(request, set(), "新品产品线图片")
        image, revision = _consistent_read(lambda: get_product_line_image(line_id))
        if image is None:
            raise WorkflowApiError("新品产品线图片不存在", code="not_found", status=404)
        return _json({"image": image}, revision=revision)
    except Exception as error:
        return _error(error, "新品产品线图片读取失败")


@require_http_methods(["GET", "POST"])
def new_product_line_learning(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"operator", "admin"})
        if request.method == "GET":
            _unknown(request, {"lineId", "name", "term"}, "新品代码预览")
            line_id = _one(request, "lineId") or ""
            if line_id and not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", line_id):
                raise WorkflowApiError("产品线 ID 无效")
            payload, revision = _consistent_read(lambda: preview_product_line_codes(
                line_id, _one(request, "name") or "", request.GET.getlist("term"),
            ))
            return _json(payload, revision=revision)
        payload = _body(request)
        if not isinstance(payload, dict) or not set(payload).issubset({"expectedSourceBatchId"}):
            raise WorkflowApiError("新品代码学习参数无效")
        expected_batch = payload.get("expectedSourceBatchId", "")
        if not isinstance(expected_batch, str) or len(expected_batch) > 200 or any(ord(character) < 32 for character in expected_batch):
            raise WorkflowApiError("吉客云货品主数据批次无效")
        return _replay_write(
            request,
            principal,
            lambda: ({"result": learn_product_line_codes(principal, expected_source_batch_id=expected_batch.strip())}, 200),
        )
    except Exception as error:
        return _error(error, "新品代码学习失败")


@require_http_methods(["GET"])
def new_product_weekly_followup(request: HttpRequest) -> JsonResponse:
    try:
        _principal(request, {"viewer", "analyst", "operator", "admin"})
        _unknown(request, {"weekStart"}, "新品销售周报")
        week_start = _date(_one(request, "weekStart"), "周起始日期")
        payload, revision = _consistent_read(lambda: weekly_followup(week_start=week_start))
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "新品销售周报读取失败")


@require_http_methods(["GET", "PATCH"])
def new_product_weekly_report_config(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            _unknown(request, set(), "新品周报配置")
            payload, revision = _consistent_read(get_report_config)
            return _json({"config": payload}, revision=revision)
        principal = _principal(request, {"operator", "admin"})
        payload = _body(request)
        return _replay_write(
            request,
            principal,
            lambda: ({"config": update_report_config(payload, principal)}, 200),
        )
    except Exception as error:
        return _error(error, "新品周报配置处理失败")


@require_POST
def consumer_query(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(
            request,
            {"viewer", "analyst", "operator", "admin"},
            unrestricted=False,
        )
        if len(request.body) > 64 * 1024:
            raise WorkflowApiError("运营事务消费查询请求超出安全上限", code="payload_too_large", status=413)
        consumer = validate_consumer_request(_body(request))
        payload, revision = _consistent_read(
            lambda: {
                "operation": consumer["operation"],
                "data": execute_consumer_query(principal, consumer),
            }
        )
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "运营事务消费查询失败")
