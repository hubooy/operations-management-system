"""Reader-only continuation of an exact source; no AI ledger authorization.

Expectations constrain the signed cursor; the AI owner must independently bind
them to its immutable last chunk and perform its original checkpoint CAS.
"""
import re

from django.core import signing
from django.http import QueryDict
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from business_analysis.contracts import MAX_SAFE_INTEGER
from . import analysis, analysis_cursor
from .errors import NetshopApiError

QUERY_FIELDS = {"platform", "shop", "dataset", "startDate", "endDate", "window"}
FIELDS = QUERY_FIELDS | {"limit", "cursor", "expectedSourceRef", "expectedRevision", "expectedLastId"}


def _invalid(message="续读原签名或检查点约束无效"):
    return NetshopApiError(message, code="invalid_cursor", status=409)


def validate_request(params):
    if set(params) != FIELDS or any(len(params.getlist(key)) != 1 for key in params):
        raise NetshopApiError("续读参数缺失、未知或重复")
    if params["limit"] != "100" or params["window"] not in {"current", "previous", "yearAgo"}:
        raise NetshopApiError("续读须保留明确窗口与固定100条页长")
    cursor, ref, revision, last = (params[key] for key in ("cursor", "expectedSourceRef", "expectedRevision", "expectedLastId"))
    if (not 1 <= len(cursor) <= 1600 or re.fullmatch(r"[a-f0-9]{64}", ref) is None
            or len(revision) > 128 or re.fullmatch(r"(0|[1-9][0-9]*):[a-f0-9]{12}", revision) is None
            or len(last) > 16 or re.fullmatch(r"[1-9][0-9]*", last) is None or int(last) > MAX_SAFE_INTEGER):
        raise NetshopApiError("续读检查点约束格式无效")
    query = {key: params[key] for key in QUERY_FIELDS}
    original = QueryDict(mutable=True)
    original.update(query)
    original["limit"], original["cursor"] = "100", cursor
    spec, limit, _ = analysis.validate_request(original)
    return query, spec, limit, cursor, ref, revision, int(last)


def read_page(principal, params):
    actor = analysis_cursor._actor(principal)
    query, spec, limit, cursor, ref, revision, last_id = validate_request(params)

    def expired():
        return analysis_cursor.read_expired_page(principal, query, cursor,
            expected_source_ref=ref, expected_revision=revision, expected_last_id=last_id, limit=limit)

    try:
        payload = signing.loads(cursor, salt=analysis.CURSOR_SALT, max_age=3600)
    except signing.SignatureExpired:
        page = expired()
    except (signing.BadSignature, ValueError, TypeError, RecursionError) as error:
        raise _invalid() from error
    else:
        if (type(payload) is not dict or set(payload) != {"binding", "lastId"}
                or payload["binding"] != ref or type(payload["lastId"]) is not int or payload["lastId"] != last_id):
            raise _invalid()
        if analysis.revision_value() != revision:
            raise NetshopApiError("来源版本已变化，不能续接旧证据", code="analysis_revision_changed", status=409)
        try:
            page = analysis.read_page(spec, limit, cursor)
        except NetshopApiError as error:
            # The original token may expire between classification and reading.
            # Only this exact underlying exception permits one expired read.
            if error.code != "invalid_cursor" or not isinstance(error.__cause__, signing.SignatureExpired):
                raise
            page = expired()
    if (page["sourceRef"] != ref or page["sourceRevision"] != revision
            or analysis.revision_value() != revision):
        raise NetshopApiError("续读期间来源版本变化", code="analysis_revision_changed", status=409)
    if actor != analysis_cursor._actor(principal):
        raise NetshopApiError("续读期间账号权限版本变化", code="access_denied", status=403)
    return page


@never_cache
@require_GET
def continuation(request):
    # Reuse only the established envelope/JSON/error boundary, not import paths.
    from .views import _principal, _json, _error
    try:
        principal = _principal(request, {"admin"})
        page = read_page(principal, request.GET)
        return _json(page, revision=page["sourceRevision"])
    except Exception as error:
        return _error(error, "经营分析续读失败")
