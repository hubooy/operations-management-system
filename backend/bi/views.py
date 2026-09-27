from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from sales.auth import PrincipalEnvelopeError, verify_principal

from .errors import BiApiError
from .query import get_bi_overview, parse_overview_options


logger = logging.getLogger(__name__)


def _json(payload: object, status: int = 200, *, revision: str | None = None) -> JsonResponse:
    response = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    if revision is not None and 200 <= status < 300:
        response["X-Bi-Data-Revision"] = revision
    return response


@require_GET
def overview(request: HttpRequest) -> JsonResponse:
    try:
        if settings.DJANGO_PROCESS_ROLE not in {"bi_reader", "development"}:
            raise BiApiError("BI reader 进程不可用", code="service_unavailable", status=503)
        principal = verify_principal(request)
        if principal.role not in {"viewer", "analyst", "operator", "admin"}:
            raise PrincipalEnvelopeError("当前角色无权访问", status=403, code="insufficient_role")
        payload, revision = get_bi_overview(principal, parse_overview_options(request.GET))
        return _json(payload, revision=revision)
    except PrincipalEnvelopeError as error:
        return _json({"error": str(error), "code": error.code}, error.status)
    except BiApiError as error:
        return _json({"error": str(error), "code": error.code}, error.status)
    except Exception:
        logger.exception("Unhandled BI API error")
        return _json({"error": "读取 BI 经营看板失败", "code": "internal_error"}, 500)
