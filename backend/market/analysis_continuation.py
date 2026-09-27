"""Independent reader continuation; caller expectations never authorize an AI ledger write."""
import re
from django.core import signing
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from access_control.models import AppUser
from business_analysis.contracts import MAX_SAFE_INTEGER
from . import analysis
from .models import MarketDataRevision
from .errors import MarketApiError

QUERY_FIELDS = {"platform", "category", "scope", "rankingDimension", "priceBandFilter", "startDate", "endDate", "window"}
FIELDS = QUERY_FIELDS | {"limit", "cursor", "expectedSourceRef", "expectedRevision", "expectedLastId"}


def _invalid(message="原签名或固定来源约束无效"):
    return MarketApiError(message, code="invalid_cursor", status=409)


def _actor(principal):
    if principal.role != "admin" or principal.scope is not None:
        raise MarketApiError("续读仅允许当前无范围管理员", code="access_denied", status=403)
    row = AppUser.objects.filter(email=principal.email.lower()).values("email", "role", "status", "scope", "version").first()
    if not row or row["role"] != "admin" or row["status"] != "active" or row["scope"] is not None:
        raise MarketApiError("实际账号或权限已变化", code="access_denied", status=403)
    return row


def revision_snapshot():
    row = MarketDataRevision.objects.filter(domain="market").values("revision", "source_digest").first()
    if (not row or type(row["revision"]) is not int or not 0 <= row["revision"] <= MAX_SAFE_INTEGER
            or type(row["source_digest"]) is not str or re.fullmatch(r"[a-f0-9]{64}", row["source_digest"]) is None):
        raise MarketApiError("市场来源版本未就绪", code="service_unavailable", status=503)
    return f'{row["revision"]}:{row["source_digest"][:12]}'


def validate_request(params):
    if set(params) != FIELDS or any(len(params.getlist(k)) != 1 for k in params):
        raise MarketApiError("续读参数缺失、未知或重复")
    if params["limit"] != "100" or params["window"] not in {"current", "previous", "yearAgo"}:
        raise MarketApiError("续读须保留明确窗口和固定100条页长")
    cursor, ref, revision, last = (params[k] for k in ("cursor", "expectedSourceRef", "expectedRevision", "expectedLastId"))
    if (not 1 <= len(cursor) <= 1600 or re.fullmatch(r"[a-f0-9]{64}", ref) is None
            or len(revision) > 128 or re.fullmatch(r"(0|[1-9][0-9]*):[a-f0-9]{12}", revision) is None
            or len(last) > 16 or re.fullmatch(r"[1-9][0-9]*", last) is None or int(last) > MAX_SAFE_INTEGER):
        raise MarketApiError("续读检查点约束格式无效")
    query = analysis.validate({**{k: params[k] for k in QUERY_FIELDS}, "operation": "analysis_records", "limit": 100, "cursor": cursor})
    return query, cursor, ref, revision, int(last)


def read_page(principal, params):
    actor = _actor(principal)
    query, cursor, ref, revision, last = validate_request(params)

    def checked_payload(expired=False):
        try:
            if expired:
                try:
                    signing.loads(cursor, salt=analysis.SALT, max_age=3600)
                except signing.SignatureExpired:
                    pass
                else:
                    raise _invalid("原游标未过期，不能重新签名")
            payload = signing.loads(cursor, salt=analysis.SALT, max_age=None if expired else 3600)
        except signing.SignatureExpired:
            raise
        except (signing.BadSignature, ValueError, TypeError, RecursionError) as error:
            raise _invalid() from error
        if (type(payload) is not dict or set(payload) != {"binding", "lastId"} or payload["binding"] != ref
                or type(payload["lastId"]) is not int or payload["lastId"] != last):
            raise _invalid()
        if revision_snapshot() != revision:
            raise MarketApiError("来源版本已变化，不能续接旧证据", code="analysis_revision_changed", status=409)
        return payload

    def expired_read():
        payload = checked_payload(expired=True)
        temporary = signing.dumps(payload, salt=analysis.SALT)
        return analysis.read_page(principal, {**query, "cursor": temporary})

    try:
        checked_payload()
    except signing.SignatureExpired:
        page = expired_read()
    else:
        try:
            page = analysis.read_page(principal, query)
        except MarketApiError as error:
            if not isinstance(error.__cause__, signing.SignatureExpired) or error.code != "invalid_cursor":
                raise
            page = expired_read()
    if page["sourceRef"] != ref or page["sourceRevision"] != revision or revision_snapshot() != revision:
        raise MarketApiError("续读期间来源版本变化", code="analysis_revision_changed", status=409)
    if actor != _actor(principal):
        raise MarketApiError("续读期间账号权限版本变化", code="access_denied", status=403)
    return page


@never_cache
@require_GET
def continuation(request):
    from .views import _principal, _json, _error
    try:
        page = read_page(_principal(request, {"admin"}), request.GET)
        return _json(page, revision=page["sourceRevision"])
    except Exception as error:
        return _error(error, "经营分析续读失败")
