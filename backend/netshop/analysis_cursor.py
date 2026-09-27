"""Internal expired-cursor read; not a checkpoint authorization endpoint.

Only an owning AI adapter may supply expectations from immutable chunks and a
fresh persisted checkpoint, then perform its original collection CAS. This
module neither accepts a renewal proposal as authority nor touches that ledger.
"""
import re

from django.core import signing
from django.http import QueryDict

from access_control.models import AppUser
from business_analysis.contracts import MAX_SAFE_INTEGER
from . import analysis
from .errors import NetshopApiError


def _actor(principal):
    if principal.role != "admin" or principal.scope is not None:
        raise NetshopApiError("续读仅向当前无范围限制管理员开放", code="access_denied", status=403)
    actor = AppUser.objects.filter(email=principal.email.lower()).values("email", "role", "status", "scope", "version").first()
    if not actor or actor["role"] != "admin" or actor["status"] != "active" or actor["scope"] is not None:
        raise NetshopApiError("当前账号或数据权限已变化", code="access_denied", status=403)
    return actor


def _invalid(message="过期续读参数或原签名无效"):
    return NetshopApiError(message, code="invalid_cursor", status=409)


def read_expired_page(principal, query, cursor, *, expected_source_ref, expected_revision, expected_last_id, limit):
    """Return the original analysis page shape, only after genuine expiration.

Expected values are extra consistency constraints, not proof of any checkpoint.
The signed original binding plus the unchanged owning reader enforce the actual
query, page size, revision and selected master batch. No cursor is persisted.
"""
    actor = _actor(principal)
    fields = {"platform", "shop", "dataset", "startDate", "endDate", "window"}
    if (type(query) is not dict or not fields-{"window"} <= set(query) <= fields
            or any(type(value) is not str for value in query.values())
            or type(limit) is not int or not 1 <= limit <= analysis.PAGE_LIMIT
            or type(cursor) is not str or not 1 <= len(cursor) <= 1600
            or type(expected_source_ref) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_source_ref) is None
            or type(expected_revision) is not str or not 1 <= len(expected_revision) <= 128
            or type(expected_last_id) is not int or not 1 <= expected_last_id <= MAX_SAFE_INTEGER):
        raise _invalid()
    params = QueryDict(mutable=True)
    params.update(query)
    params["limit"] = str(limit)
    spec, parsed_limit, _ = analysis.validate_request(params)
    try:
        signing.loads(cursor, salt=analysis.CURSOR_SALT, max_age=3600)
    except signing.SignatureExpired:
        try:
            # TimestampSigner already verified the signature before expiration;
            # decode again under the same salt, never interpret client JSON.
            payload = signing.loads(cursor, salt=analysis.CURSOR_SALT, max_age=None)
        except (signing.BadSignature, ValueError, TypeError, RecursionError) as error:
            raise _invalid() from error
    except (signing.BadSignature, ValueError, TypeError, RecursionError) as error:
        raise _invalid() from error
    else:
        raise _invalid("原游标尚未过期，请使用原正常读页流程")
    if (type(payload) is not dict or set(payload) != {"binding", "lastId"}
            or payload["binding"] != expected_source_ref or type(payload["lastId"]) is not int
            or payload["lastId"] != expected_last_id):
        raise _invalid("原签名载荷与固定来源检查点不一致")
    if analysis.revision_value() != expected_revision:
        raise NetshopApiError("网店版本已变化，不能续接旧证据", code="analysis_revision_changed", status=409)
    temporary = signing.dumps(payload, salt=analysis.CURSOR_SALT, compress=True)
    # Reuse actual binding calculation (including current masterBatch), keyset,
    # projections, byte capacity and the reader's before/after revision fence.
    page = analysis.read_page(spec, parsed_limit, temporary)
    if (page["sourceRef"] != expected_source_ref or page["sourceRevision"] != expected_revision
            or analysis.revision_value() != expected_revision):
        raise NetshopApiError("续读期间网店版本变化", code="analysis_revision_changed", status=409)
    if _actor(principal) != actor:
        raise NetshopApiError("续读期间账号权限版本变化", code="access_denied", status=403)
    return page
