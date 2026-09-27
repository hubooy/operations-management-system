from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import guangdong as service
from .errors import InventoryApiError
from .models import GuangdongSupplierCycle
from .views import _principal, _json, _error, _body, _one, _selections, _unknown, _positive, _replay_write

READ_ROLES = {"viewer", "analyst", "operator", "admin"}
WRITE_ROLES = {"operator", "admin"}


def _read(loader):
    before = service.version()
    result = loader()
    if service.version() != before:
        service._conflict()
    result["version"] = before
    # Keep the existing inventory header contract; the composite export fence is in the body.
    return _json(result, revision=before.split("/", 1)[0])


def _options(request, export=False):
    _unknown(request, {"q", "brand", "category", "supplier", "risk", "page", "pageSize"} | ({"version", "kind"} if export else set()), "广东入仓监控")
    query = _one(request, "q") or ""
    risk = _one(request, "risk") or ""
    if len(query) > 100 or risk and risk not in service.RISK_LABELS:
        raise InventoryApiError("搜索或风险条件无效")
    return {"query": query, "risk": risk, "brands": _selections(request, "brand", 20), "categories": _selections(request, "category", 20), "suppliers": _selections(request, "supplier", 20), "page": _positive(_one(request, "page"), 1, "page", 10000), "pageSize": _positive(_one(request, "pageSize"), 50, "pageSize", 100), "version": _one(request, "version") if export else None}


@require_GET
def monitor(request):
    try:
        principal = _principal(request, READ_ROLES)
        return _read(lambda: service.monitor(principal, _options(request)))
    except Exception as error:
        return _error(error, "广东入仓监控读取失败")


@require_GET
def watchlist(request):
    try:
        _principal(request, READ_ROLES)
        _unknown(request, set(), "监控清单")
        return _read(service.list_items)
    except Exception as error:
        return _error(error, "监控清单读取失败")


@require_GET
def products(request):
    try:
        _principal(request, READ_ROLES)
        _unknown(request, {"q"}, "货品搜索")
        return _read(lambda: service.search_products(_one(request, "q")))
    except Exception as error:
        return _error(error, "货品搜索失败")


@require_POST
def preview(request):
    try:
        _principal(request, WRITE_ROLES)
        _unknown(request, set(), "监控清单预览")
        payload = _body(request)
        if set(payload) != {"rows"}:
            raise InventoryApiError("预览只接受rows字段")
        return _read(lambda: service.preview(payload["rows"]))
    except Exception as error:
        return _error(error, "清单预览失败")


@require_POST
def imports(request):
    try:
        principal = _principal(request, WRITE_ROLES)
        _unknown(request, set(), "监控清单导入")
        payload = _body(request)
        if payload.get("action") not in {"import", "reject"}:
            raise InventoryApiError("导入操作无效")
        return _replay_write(request, principal, lambda: (service.mutate(payload, principal.email), 200))
    except Exception as error:
        return _error(error, "清单导入失败")


@require_http_methods(["GET", "PATCH"])
def suppliers(request):
    try:
        principal = _principal(request, READ_ROLES if request.method == "GET" else WRITE_ROLES)
        _unknown(request, set(), "供应商周期")
        if request.method == "GET":
            def loader():
                names = sorted({row["supplier"] for row in service.list_items()["items"] if row["supplier"] != "未映射供应商"})
                cycles = {row.supplier: row for row in GuangdongSupplierCycle.objects.filter(supplier__in=names)}
                return {"items": [{"supplier": name, "leadDays": cycles[name].lead_days if name in cycles else None, "bufferDays": cycles[name].buffer_days if name in cycles else 7} for name in names]}
            return _read(loader)
        payload = _body(request)
        if payload.get("action") != "supplier":
            raise InventoryApiError("供应商操作无效")
        return _replay_write(request, principal, lambda: (service.mutate(payload, principal.email), 200))
    except Exception as error:
        return _error(error, "供应商周期操作失败")


@require_http_methods(["PATCH"])
def items(request):
    try:
        principal = _principal(request, WRITE_ROLES)
        _unknown(request, set(), "型号设置")
        payload = _body(request)
        if payload.get("action") != "item":
            raise InventoryApiError("型号设置操作无效")
        return _replay_write(request, principal, lambda: (service.mutate(payload, principal.email), 200))
    except Exception as error:
        return _error(error, "型号设置保存失败")


@require_GET
def export(request):
    try:
        principal = _principal(request, READ_ROLES)
        options = _options(request, export=True)
        kind = _one(request, "kind")
        if kind not in {"watchlist", "monitor"}:
            raise InventoryApiError("导出类型无效")
        if options["version"] != service.version(): service._conflict()
        return _read(service.list_items if kind == "watchlist" else lambda: service.monitor(principal, options, export=True))
    except Exception as error:
        return _error(error, "导出失败，请刷新后重试")
