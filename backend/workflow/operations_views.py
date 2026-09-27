from __future__ import annotations

from datetime import date

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from sales.auth import Principal, PrincipalEnvelopeError, verify_principal

from .errors import WorkflowApiError
from .operations import (
    PRIORITIES,
    RECORD_SOURCES,
    RECORD_STATUSES,
    RECORD_TYPES,
    TASK_SOURCES,
    TASK_STATUSES,
    _datetime,
    attachment_metadata,
    cleanup_batch,
    cleanup_result,
    collaboration,
    create_attachment_metadata,
    create_comment,
    create_inventory_work_item,
    create_link,
    create_record,
    create_reminder,
    create_task,
    create_template,
    delete_attachment_metadata,
    delete_link,
    delete_record,
    delete_task,
    delete_template,
    dismiss_reminder,
    get_record,
    list_records,
    list_tasks,
    list_templates,
    record_activities,
    update_record,
    update_task,
    update_template,
)
from .revisions import revision_value
from .views import _body, _consistent_read, _error, _json, _one, _positive, _replay_write, _selections, _unknown


READ_ROLES = {"viewer", "analyst", "operator", "admin"}
WRITE_ROLES = {"operator", "admin"}


def _operations_principal(request: HttpRequest, roles: set[str], *, unrestricted: bool = False) -> Principal:
    principal = verify_principal(request)
    if principal.role not in roles:
        raise PrincipalEnvelopeError("当前角色无权访问", status=403, code="insufficient_role")
    if unrestricted and principal.scope is not None:
        raise PrincipalEnvelopeError("该运营事务接口仅支持未受限数据范围账号", status=403, code="access_denied")
    return principal


def _calendar_query(value: str | None, label: str) -> str | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise WorkflowApiError(f"{label}必须为真实的 YYYY-MM-DD 日期") from error
    if parsed.isoformat() != value:
        raise WorkflowApiError(f"{label}必须为真实的 YYYY-MM-DD 日期")
    return value


def _search(request: HttpRequest, names: tuple[str, ...], maximum: int = 80) -> str:
    values = [item for name in names for item in request.GET.getlist(name)]
    if len(values) > 1:
        raise WorkflowApiError("搜索词参数不能重复")
    value = values[0].strip() if values else ""
    if len(value) > maximum:
        raise WorkflowApiError(f"搜索词不能超过 {maximum} 个字符")
    return value


def _task_options(request: HttpRequest) -> dict[str, object]:
    _unknown(request, {"q", "query", "status", "priority", "owner", "shopName", "category", "source", "dueFrom", "dueTo", "page", "pageSize"}, "工作计划列表")
    due_from = _calendar_query(_one(request, "dueFrom"), "截止开始日期")
    due_to = _calendar_query(_one(request, "dueTo"), "截止结束日期")
    if due_from and due_to and due_from >= due_to:
        raise WorkflowApiError("截止日期范围必须满足开始日期早于结束日期")
    page = _positive(_one(request, "page"), 1, "page", 2_000)
    page_size = _positive(_one(request, "pageSize"), 50, "pageSize", 100)
    if (page - 1) * page_size > 100_000:
        raise WorkflowApiError("分页偏移不能超过 100000")
    return {
        "query": _search(request, ("q", "query")),
        "statuses": _selections(request, "status", 20, TASK_STATUSES),
        "priorities": _selections(request, "priority", 20, PRIORITIES),
        "owners": _selections(request, "owner", 20),
        "shop_names": _selections(request, "shopName", 20),
        "categories": _selections(request, "category", 20),
        "sources": _selections(request, "source", 20, TASK_SOURCES),
        "due_from": due_from, "due_to": due_to, "page": page, "page_size": page_size,
    }


def _record_options(request: HttpRequest) -> dict[str, object]:
    _unknown(request, {"type", "status", "priority", "shopName", "platform", "owner", "query", "from", "to", "page", "pageSize"}, "运营记录列表")
    from_time = _datetime(_one(request, "from"), "开始时间", nullable=True)
    to_time = _datetime(_one(request, "to"), "结束时间", nullable=True)
    if from_time and to_time and from_time >= to_time:
        raise WorkflowApiError("时间范围必须满足开始时间早于结束时间")
    page = _positive(_one(request, "page"), 1, "page", 100_000)
    page_size = _positive(_one(request, "pageSize"), 30, "pageSize", 100)
    if (page - 1) * page_size > 100_000:
        raise WorkflowApiError("分页偏移不能超过 100000")
    allowed_statuses = set().union(*RECORD_STATUSES.values())
    return {
        "types": _selections(request, "type", 20, RECORD_TYPES),
        "priorities": _selections(request, "priority", 3, PRIORITIES),
        "statuses": _selections(request, "status", 20, allowed_statuses),
        "shop_names": _selections(request, "shopName", 20),
        "platforms": _selections(request, "platform", 20),
        "owners": _selections(request, "owner", 20),
        "query": _search(request, ("query",)), "from_time": from_time, "to_time": to_time,
        "page": page, "page_size": page_size,
    }


def _write(request: HttpRequest, principal: Principal, callback):
    return _replay_write(request, principal, callback, authority_scope="operations")


@require_http_methods(["GET", "POST", "PATCH", "DELETE"])
def tasks(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _operations_principal(request, READ_ROLES, unrestricted=True)
            payload, revision = _consistent_read(lambda: list_tasks(_task_options(request)))
            return _json(payload, revision=revision)
        principal = _operations_principal(request, WRITE_ROLES, unrestricted=True)
        if request.method == "POST":
            payload = _body(request)
            return _write(request, principal, lambda: ({"item": create_task(payload, principal)}, 201))
        identifier = _one(request, "id")
        if request.method == "PATCH":
            _unknown(request, {"id"}, "工作事项更新")
            payload = _body(request)
            def update_callback():
                item = update_task(identifier, payload, principal)
                if item is None:
                    raise WorkflowApiError("工作项不存在或已删除", code="not_found", status=404)
                return {"item": item}, 200
            return _write(request, principal, update_callback)
        _unknown(request, {"id", "expectedVersion"}, "工作事项删除")
        expected_version = _one(request, "expectedVersion")
        def delete_callback():
            result = delete_task(identifier, expected_version, principal)
            if result is None:
                raise WorkflowApiError("工作项不存在或已删除", code="not_found", status=404)
            return {"ok": True, "cleanupObjectKeys": result["cleanupObjectKeys"]}, 200
        return _write(request, principal, delete_callback)
    except Exception as error:
        return _error(error, "工作计划处理失败")


@require_http_methods(["GET"])
def task_collaboration(request: HttpRequest, task_id: str) -> JsonResponse:
    try:
        _operations_principal(request, READ_ROLES, unrestricted=True)
        payload, revision = _consistent_read(lambda: collaboration(task_id))
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "工作事项协作信息读取失败")


@require_http_methods(["GET", "POST"])
def task_comments(request: HttpRequest, task_id: str) -> JsonResponse:
    try:
        if request.method == "GET":
            _operations_principal(request, READ_ROLES, unrestricted=True)
            payload, revision = _consistent_read(lambda: {"items": collaboration(task_id)["comments"]})
            return _json(payload, revision=revision)
        principal = _operations_principal(request, WRITE_ROLES, unrestricted=True); payload = _body(request)
        return _write(request, principal, lambda: ({"item": create_comment(task_id, payload, principal)}, 201))
    except Exception as error:
        return _error(error, "工作事项评论处理失败")


@require_http_methods(["GET"])
def task_activity(request: HttpRequest, task_id: str) -> JsonResponse:
    try:
        _operations_principal(request, READ_ROLES, unrestricted=True)
        payload, revision = _consistent_read(lambda: {"items": collaboration(task_id)["activity"]})
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "工作事项活动读取失败")


@require_http_methods(["GET", "POST", "DELETE"])
def task_reminders(request: HttpRequest, task_id: str) -> JsonResponse:
    try:
        if request.method == "GET":
            _operations_principal(request, READ_ROLES, unrestricted=True)
            payload, revision = _consistent_read(lambda: {"items": collaboration(task_id)["reminders"]})
            return _json(payload, revision=revision)
        principal = _operations_principal(request, WRITE_ROLES, unrestricted=True)
        if request.method == "POST":
            payload = _body(request)
            return _write(request, principal, lambda: ({"item": create_reminder(task_id, payload, principal)}, 201))
        _unknown(request, {"id"}, "提醒删除"); identifier = _one(request, "id")
        def callback():
            if not dismiss_reminder(task_id, identifier, principal):
                raise WorkflowApiError("待处理提醒不存在", code="not_found", status=404)
            return {"ok": True}, 200
        return _write(request, principal, callback)
    except Exception as error:
        return _error(error, "工作事项提醒处理失败")


@require_http_methods(["GET", "POST", "DELETE"])
def task_links(request: HttpRequest, task_id: str) -> JsonResponse:
    try:
        if request.method == "GET":
            _operations_principal(request, READ_ROLES, unrestricted=True)
            payload, revision = _consistent_read(lambda: {"items": collaboration(task_id)["links"]})
            return _json(payload, revision=revision)
        principal = _operations_principal(request, WRITE_ROLES, unrestricted=True)
        if request.method == "POST":
            payload = _body(request)
            return _write(request, principal, lambda: ({"item": create_link(task_id, payload, principal)}, 201))
        _unknown(request, {"id"}, "业务关联删除"); identifier = _one(request, "id")
        def callback():
            if not delete_link(task_id, identifier, principal):
                raise WorkflowApiError("业务关联不存在", code="not_found", status=404)
            return {"ok": True}, 200
        return _write(request, principal, callback)
    except Exception as error:
        return _error(error, "工作事项业务关联处理失败")


@require_http_methods(["GET", "POST"])
def task_attachments(request: HttpRequest, task_id: str) -> JsonResponse:
    try:
        if request.method == "GET":
            _operations_principal(request, READ_ROLES, unrestricted=True)
            payload, revision = _consistent_read(lambda: {"items": collaboration(task_id)["attachments"]})
            return _json(payload, revision=revision)
        principal = _operations_principal(request, WRITE_ROLES, unrestricted=True); payload = _body(request)
        return _write(request, principal, lambda: ({"item": create_attachment_metadata(task_id, payload, principal)}, 201))
    except Exception as error:
        return _error(error, "工作事项附件处理失败")


@require_http_methods(["GET", "DELETE"])
def task_attachment(request: HttpRequest, task_id: str, attachment_id: str) -> JsonResponse:
    try:
        if request.method == "GET":
            _operations_principal(request, READ_ROLES, unrestricted=True)
            payload, revision = _consistent_read(lambda: attachment_metadata(task_id, attachment_id))
            if payload is None:
                return _json({"error": "附件不存在", "code": "not_found"}, 404)
            return _json({"item": payload}, revision=revision)
        principal = _operations_principal(request, WRITE_ROLES, unrestricted=True)
        def callback():
            result = delete_attachment_metadata(task_id, attachment_id, principal)
            if result is None:
                raise WorkflowApiError("附件不存在", code="not_found", status=404)
            return result, 200
        return _write(request, principal, callback)
    except Exception as error:
        return _error(error, "工作事项附件处理失败")


@require_http_methods(["GET", "POST"])
def attachment_cleanup(request: HttpRequest) -> JsonResponse:
    try:
        principal = _operations_principal(request, WRITE_ROLES, unrestricted=True)
        if request.method == "GET":
            _unknown(request, {"limit"}, "附件清理读取")
            return _json(cleanup_batch(_positive(_one(request, "limit"), 50, "limit", 100)), revision=revision_value())
        payload = _body(request)
        return _write(request, principal, lambda: (cleanup_result(payload), 200))
    except Exception as error:
        return _error(error, "附件清理状态处理失败")


@require_http_methods(["GET", "POST", "PATCH", "DELETE"])
def templates(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            principal = _operations_principal(request, READ_ROLES, unrestricted=True)
            _unknown(request, {"includeInactive"}, "工作模板列表")
            raw = _one(request, "includeInactive")
            if raw not in {None, "true", "false"}:
                raise WorkflowApiError("includeInactive 参数无效")
            include = raw == "true" and principal.role in WRITE_ROLES
            payload, revision = _consistent_read(lambda: {"items": list_templates(include)})
            return _json(payload, revision=revision)
        principal = _operations_principal(request, WRITE_ROLES, unrestricted=True)
        if request.method == "POST":
            payload = _body(request)
            return _write(request, principal, lambda: ({"item": create_template(payload, principal)}, 201))
        identifier = _one(request, "id")
        if request.method == "PATCH":
            _unknown(request, {"id"}, "工作模板更新"); payload = _body(request)
            def callback():
                item = update_template(identifier, payload, principal)
                if item is None: raise WorkflowApiError("模板不存在", code="not_found", status=404)
                return {"item": item}, 200
            return _write(request, principal, callback)
        _unknown(request, {"id", "expectedVersion"}, "工作模板删除"); expected = _one(request, "expectedVersion")
        def callback():
            if not delete_template(identifier, expected): raise WorkflowApiError("模板不存在", code="not_found", status=404)
            return {"ok": True}, 200
        return _write(request, principal, callback)
    except Exception as error:
        return _error(error, "工作模板处理失败")


@require_http_methods(["GET", "POST"])
def operation_records(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            principal = _operations_principal(request, READ_ROLES)
            payload, revision = _consistent_read(lambda: list_records(_record_options(request), principal))
            return _json(payload, revision=revision)
        principal = _operations_principal(request, WRITE_ROLES); payload = _body(request)
        if payload.get("type") == "launch":
            raise WorkflowApiError("新品上新已切换到结构化项目，请使用新品项目接口。", code="conflict", status=409)
        return _write(request, principal, lambda: ({"item": create_record(payload, principal)}, 201))
    except Exception as error:
        return _error(error, "运营记录处理失败")


@require_http_methods(["GET", "PATCH", "DELETE"])
def operation_record(request: HttpRequest, record_id: str) -> JsonResponse:
    try:
        if request.method == "GET":
            principal = _operations_principal(request, READ_ROLES)
            payload, revision = _consistent_read(lambda: get_record(record_id, principal))
            if payload is None: return _json({"error": "运营记录不存在或不可访问", "code": "not_found"}, 404)
            return _json({"item": payload}, revision=revision)
        principal = _operations_principal(request, WRITE_ROLES)
        if request.method == "PATCH":
            payload = _body(request)
            def callback():
                item = update_record(record_id, payload, principal)
                if item is None: raise WorkflowApiError("运营记录不存在或不可访问", code="not_found", status=404)
                return {"item": item}, 200
            return _write(request, principal, callback)
        _unknown(request, {"expectedVersion"}, "运营记录删除"); expected = _one(request, "expectedVersion")
        def callback():
            result = delete_record(record_id, expected, principal)
            if result is None: raise WorkflowApiError("运营记录不存在或不可访问", code="not_found", status=404)
            return result, 200
        return _write(request, principal, callback)
    except Exception as error:
        return _error(error, "运营记录处理失败")


@require_http_methods(["GET"])
def operation_record_activity(request: HttpRequest, record_id: str) -> JsonResponse:
    try:
        principal = _operations_principal(request, READ_ROLES)
        _unknown(request, {"page", "pageSize"}, "运营记录活动")
        page = _positive(_one(request, "page"), 1, "page", 100_000); page_size = _positive(_one(request, "pageSize"), 30, "pageSize", 100)
        if (page - 1) * page_size > 100_000: raise WorkflowApiError("分页偏移不能超过 100000")
        payload, revision = _consistent_read(lambda: record_activities(record_id, page, page_size, principal))
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "运营记录活动读取失败")


@require_http_methods(["POST"])
def inventory_work_item(request: HttpRequest) -> JsonResponse:
    try:
        principal = _operations_principal(request, WRITE_ROLES, unrestricted=True); payload = _body(request)
        return _write(request, principal, lambda: (create_inventory_work_item(payload, principal), 201))
    except Exception as error:
        return _error(error, "库存执行事项处理失败")
