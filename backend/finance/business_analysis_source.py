"""Internal complete in-memory finance source; no public or persistence protocol.

Callers must leave open_source normally before publishing anything derived from
it. Neither the returned pure source nor its binding is a reusable capability.
"""
from contextlib import contextmanager
import copy

from access_control.models import AppUser
from business_analysis import finance_source
from business_analysis.contracts import AnalysisContractError, digest
from .errors import FinanceApiError
from .models import FinanceDataRevision, FinanceImportBatch, FinanceLine, FinanceMonth

READ_ROWS = 100


def _actor(principal):
    if principal.role != "admin" or principal.scope is not None:
        raise FinanceApiError("财报证据仅向无范围限制管理员开放", status=403, code="access_denied")
    row = AppUser.objects.filter(email=principal.email.lower()).values("email", "role", "status", "scope", "version").first()
    if not row or row["role"] != "admin" or row["status"] != "active" or row["scope"] is not None:
        raise FinanceApiError("当前账号权限不可用", status=403, code="access_denied")
    return row


def _revision():
    row = FinanceDataRevision.objects.filter(domain="finance").values("revision", "source_digest").first()
    if row is None:
        raise FinanceApiError("财务版本不可用", status=503, code="service_unavailable")
    finance_source._integer(row["revision"])
    finance_source._sha(row["source_digest"])
    return row


def _metadata(query):
    months = list(FinanceMonth.objects.filter(month__in=query["months"]).order_by("month")
                  .values(*sorted(finance_source.MONTH_FIELDS))[:finance_source.MAX_MONTHS + 1])
    batches = list(FinanceImportBatch.objects.filter(id__in=[m["batch_id"] for m in months]).order_by("id")
                   .values(*sorted(finance_source.BATCH_FIELDS))[:finance_source.MAX_MONTHS + 1])
    # Validate missing/incomplete/foreign batch relations before fetching facts.
    finance_source.build([], query=query, revision={"revision": 0, "source_digest": "0" * 64},
                         months=months, batches=batches)
    return months, batches


def _revalidate(principal, actor, revision, query, metadata):
    if _actor(principal) != actor:
        raise FinanceApiError("采集期间账号权限变化", status=403, code="access_denied")
    try:
        changed = _revision() != revision or _metadata(query) != metadata or _revision() != revision
    except AnalysisContractError as error:
        raise FinanceApiError("采集期间财务发布元数据失效", status=409, code="analysis_revision_changed") from error
    if changed:
        raise FinanceApiError("采集期间财务版本或发布批次变化", status=409, code="analysis_revision_changed")
    if _actor(principal) != actor:
        raise FinanceApiError("采集期间账号权限变化", status=403, code="access_denied")


@contextmanager
def open_source(principal, query, *, analysis_period=None):
    """Read all exact-scope facts in bounded keyset pages with live fences.

Missing months remain explicit gaps. No retry or partial completed source is
returned after any late error. Production grants are a separate adoption gate.
"""
    try:
        query = finance_source._query(query)
        actor = _actor(principal)
        revision = _revision()
        metadata = _metadata(query)

        def rows():
            last_id = 0
            while True:
                _revalidate(principal, actor, revision, query, metadata)
                page = list(FinanceLine.objects.filter(month__in=query["months"],
                    **query["scope"], id__gt=last_id).order_by("id")
                    .values(*sorted(finance_source.ROW_FIELDS))[:READ_ROWS])
                _revalidate(principal, actor, revision, query, metadata)
                yield from page
                if len(page) < READ_ROWS:
                    break
                last_id = page[-1]["id"]

        source = finance_source.build(rows(), query=query, revision=revision,
            months=metadata[0], batches=metadata[1], analysis_period=analysis_period)
        _revalidate(principal, actor, revision, query, metadata)
        binding = {"schemaVersion": "finance-owning-memory-binding-v1", "actor": actor,
            "sourceDigest": source.manifest["sourceDigest"], "revision": revision,
            "publicationDigest": digest(metadata), "persistentEvidenceVerified": False}
    except AnalysisContractError as error:
        raise FinanceApiError("财报来源未通过完整字段、容量或发布核验", status=422, code="invalid_finance_source") from error
    yield source, copy.deepcopy(binding)
    # Deliberately outside the preparation error mapping; caller exceptions and
    # failures must propagate without turning partial work into a successful seal.
    _revalidate(principal, actor, revision, query, metadata)
