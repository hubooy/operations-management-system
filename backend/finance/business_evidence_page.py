"""Internal bounded monthly finance page reader; no route or collector grant.

Each page rechecks the real actor, full finance revision, published month/batch
mapping and exact-scope row count. It never scans all line payloads per page.
The future collector must still seal the complete persisted row chain.
"""
from __future__ import annotations

from datetime import date

from django.db.models import QuerySet

from business_analysis import finance_source
from business_analysis.contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, comparison_periods, digest
from . import business_analysis_source as owner
from .errors import FinanceApiError
from .models import FinanceLine

SCHEMA = "business-finance-owned-page-v1"
MAX_PAGE_BYTES = 38_000
PAGE_ROWS = 100


def _fail(message, *, status=409, code="analysis_revision_changed"):
    raise FinanceApiError(message, status=status, code=code)


def _query(value):
    if type(value) is not dict or set(value) != {"months", "scope", "analysisPeriod"}:
        _fail("财报来源查询字段集合无效", status=422, code="invalid_finance_source")
    if type(value["analysisPeriod"]) is not dict:
        _fail("财报须明确绑定日分析区间", status=422, code="invalid_finance_source")
    try:
        source = finance_source._query({"months": value["months"], "scope": value["scope"]})
        alignment = finance_source._period(source, value["analysisPeriod"])
        comparison_periods(alignment["analysisPeriod"]["startDate"], alignment["analysisPeriod"]["endDate"])
    except AnalysisContractError as error:
        raise FinanceApiError("财报自然月、精确范围或分析日期无效", status=422,
                              code="invalid_finance_source") from error
    first = date.fromisoformat(alignment["analysisPeriod"]["startDate"])
    last = date.fromisoformat(alignment["analysisPeriod"]["endDate"])
    start_id, end_id = first.year * 12 + first.month - 1, last.year * 12 + last.month - 1
    selected = {finance_source._month(month) for month in source["months"]}
    if not set(range(start_id, end_id + 1)) <= selected:
        _fail("财报月份未包含日分析区间涉及的全部自然月", status=422, code="invalid_finance_source")
    return {**source, "analysisPeriod": alignment["analysisPeriod"]}, alignment


def _facts(query) -> QuerySet:
    return FinanceLine.objects.filter(month__in=query["months"], **query["scope"])


def _snapshot(principal, query):
    actor = owner._actor(principal)
    revision = owner._revision()
    try:
        months, batches = owner._metadata({key: query[key] for key in ("months", "scope")})
    except AnalysisContractError as error:
        raise FinanceApiError("财报月份与完成批次关系不可用", status=409,
                              code="analysis_revision_changed") from error
    total = _facts(query).count()
    if type(total) is not int or not 0 <= total <= finance_source.MAX_ROWS:
        _fail("财报精确范围超出完整来源行数上限", status=413, code="payload_too_large")
    publication = {"months": months, "batches": batches,
                   "missingMonths": [month for month in query["months"] if month not in {item["month"] for item in months}]}
    source_ref = digest({"schemaVersion": SCHEMA, "actor": actor, "query": query,
                         "revision": revision, "publication": publication, "rowCount": total})
    return {"actor": actor, "revision": revision, "publication": publication,
            "rowCount": total, "sourceRef": source_ref}


def read_page(principal, query, *, offset=0, after_id=0,
              expected_source_ref=None, expected_revision=None):
    """Return one complete prefix; offset+after_id must describe the same facts.

    Use the returned nextOffset/nextLastId with both expected bindings on every
    continuation. A successful page is an in-memory read, not persisted proof.
    """
    query, alignment = _query(query)
    try:
        finance_source._integer(offset, 0, finance_source.MAX_ROWS)
        finance_source._integer(after_id, 0, MAX_SAFE_INTEGER)
    except AnalysisContractError as error:
        raise FinanceApiError("财报分页边界无效", status=422, code="invalid_finance_source") from error
    if (offset == 0) != (after_id == 0):
        _fail("财报偏移与上一真实行身份不一致", status=422, code="invalid_finance_source")
    if offset > 0 and (type(expected_source_ref) is not str or type(expected_revision) is not str):
        _fail("财报续页须绑定原始来源与完整版本", status=422, code="invalid_finance_source")
    before = _snapshot(principal, query)
    revision_text = f'{before["revision"]["revision"]}:{before["revision"]["source_digest"]}'
    if (expected_source_ref is not None and expected_source_ref != before["sourceRef"]
            or expected_revision is not None and expected_revision != revision_text):
        _fail("财报来源版本或发布批次已变化")
    if offset > before["rowCount"]:
        _fail("财报分页偏移超过来源行数", status=422, code="invalid_finance_source")
    facts = _facts(query)
    if offset:
        if (not facts.filter(pk=after_id).exists() or facts.filter(id__lte=after_id).count() != offset):
            _fail("财报续页的原始行检查点不属于当前精确范围")
    rows = list(facts.filter(id__gt=after_id).order_by("id")
                .values(*sorted(finance_source.ROW_FIELDS))[:PAGE_ROWS + 1])
    available = {item["month"]: item for item in before["publication"]["months"]}
    records = []

    def assemble(items):
        next_offset = offset + len(items)
        value = {"schemaVersion": SCHEMA, "sourceRef": before["sourceRef"],
                 "sourceRevision": revision_text, "query": query,
                 "periodAlignment": alignment, "publication": before["publication"],
                 "rows": items, "pageEvidence": {"rowCount": len(items), "sha256": digest(items)},
                 "pagination": {"offset": offset, "returned": len(items),
                                "total": before["rowCount"],
                                "nextOffset": next_offset if next_offset < before["rowCount"] else None,
                                "nextLastId": items[-1]["id"] if next_offset < before["rowCount"] else None},
                 "sourceAuthorityVerified": False, "persistentEvidenceVerified": False}
        return {**value, "pageDigest": digest(value)}

    for raw in rows[:PAGE_ROWS]:
        try:
            row = finance_source._row(raw, {"months": query["months"], "scope": query["scope"]}, available)
        except AnalysisContractError as error:
            raise FinanceApiError("财报行与完成批次或精确范围不一致", status=409,
                                  code="analysis_revision_changed") from error
        record = {"rowId": digest({"revision": before["revision"], "row": row}), **row}
        trial = assemble([*records, record])
        if len(canonical(trial).encode("utf-8")) > MAX_PAGE_BYTES:
            if not records:
                _fail("单条财报事实超过完整工具页容量", status=413, code="payload_too_large")
            break
        records.append(record)
    page = assemble(records)
    if len(canonical(page).encode("utf-8")) > MAX_PAGE_BYTES:
        _fail("财报完整页超过工具容量", status=413, code="payload_too_large")
    # Catch a disappearing page or invented total before it escapes.
    if offset < before["rowCount"] and not records:
        _fail("财报来源行数与分页内容不一致")
    owner._revalidate(principal, before["actor"], before["revision"],
                      {key: query[key] for key in ("months", "scope")},
                      (before["publication"]["months"], before["publication"]["batches"]))
    after = _snapshot(principal, query)
    if after != before:
        _fail("财报账号、发布批次或精确行数在读取期间变化")
    return page
