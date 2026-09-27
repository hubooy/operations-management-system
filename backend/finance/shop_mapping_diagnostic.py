"""Closed, read-only finance shop identity diagnostic without mapping authority.

Only publication status and bounded shop/group identity projections are read.
The normalized finance ledger may already have merged same-name shops from
different source groups; this diagnostic can flag that risk, not undo it.
"""
from __future__ import annotations

import json
import re

from django.db.models import Count, Max

from business_analysis import finance_source
from business_analysis.contracts import AnalysisContractError, canonical, digest
from . import business_analysis_source as owner
from .errors import FinanceApiError
from .models import FinanceImportBatch, FinanceLine, FinanceMonth


SCHEMA = "finance-shop-mapping-diagnostic-v1"
MAX_CANDIDATES = 1_000
MAX_SOURCE_LINES = 100_000
MAX_OUTPUT_BYTES = 128 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PLATFORMS = frozenset(("京东", "天猫"))


def _need(ok, message="财报店铺身份诊断输入、发布或容量无效"):
    if not ok:
        raise FinanceApiError(message, status=409,
            code="finance_shop_mapping_diagnostic_unavailable")


def _months(value):
    _need(type(value) is list and 1 <= len(value) <= finance_source.MAX_MONTHS)
    try:
        ordinals = [finance_source._month(item) for item in value]
    except AnalysisContractError as error:
        raise FinanceApiError("财报诊断月份格式无效", status=422,
            code="invalid_finance_months") from error
    _need(ordinals == list(range(ordinals[0], ordinals[0] + len(ordinals))),
        "财报诊断月份必须升序、连续且不重复")
    return list(value)


def _identity(value, maximum):
    _need(type(value) is str and 0 < len(value) <= maximum
        and value == value.strip())
    return value


def _publication(months, month_records, batch_records):
    _need(type(month_records) is list and type(batch_records) is list
        and len(month_records) <= len(months)
        and len(batch_records) <= len(months))
    found = {}
    for item in month_records:
        _need(type(item) is dict and set(item) == {"month", "batch_id", "status"}
            and item["month"] in months and item["month"] not in found)
        _identity(item["batch_id"], 64)
        _need(type(item["status"]) is str)
        found[item["month"]] = item
    batches = {}
    for item in batch_records:
        _need(type(item) is dict and set(item) == {"id", "status",
            "content_hash", "published_state_token"}
            and item["id"] not in batches)
        _identity(item["id"], 64)
        _need(type(item["status"]) is str)
        batches[item["id"]] = item
    status = []
    for month in months:
        record = found.get(month)
        batch = batches.get(record["batch_id"]) if record else None
        completed = bool(record and batch and record["status"] == "completed"
            and batch["status"] == "completed"
            and type(batch["content_hash"]) is str
            and _SHA.fullmatch(batch["content_hash"])
            and type(batch["published_state_token"]) is str
            and _SHA.fullmatch(batch["published_state_token"]))
        status.append({"month": month, "status": "completed_metadata"
            if completed else "missing_month" if record is None else
            "incomplete_publication"})
    return status


def project(months, month_records, batch_records, group_rows, revision):
    """Pure bounded projection; caller-supplied rows grant no read authority."""
    selected = _months(months)
    _need(type(revision) is dict and set(revision) ==
        {"revision", "source_digest"}
        and type(revision["revision"]) is int and revision["revision"] >= 0
        and type(revision["source_digest"]) is str
        and _SHA.fullmatch(revision["source_digest"]) is not None)
    publication = _publication(selected, month_records, batch_records)
    available = {item["month"] for item in publication
        if item["status"] == "completed_metadata"}
    _need(type(group_rows) is list and len(group_rows) <= MAX_CANDIDATES)
    seen = set()
    records = []
    total_source_lines = 0
    for item in group_rows:
        _need(type(item) is dict and set(item) == {"month", "scope_key",
            "scope_name", "group_name", "line_count",
            "max_source_row_count"}
            and item["month"] in available)
        scope = _identity(item["scope_key"], 500)
        name = _identity(item["scope_name"], 200)
        group = item["group_name"]
        _need(type(group) is str and len(group) <= 200)
        _need(type(item["line_count"]) is int and 1 <= item["line_count"] <= 100_000
            and type(item["max_source_row_count"]) is int
            and 1 <= item["max_source_row_count"] <= 100_000)
        total_source_lines += item["line_count"]
        _need(total_source_lines <= MAX_SOURCE_LINES,
            "财报店铺规范行总数超过诊断扫描容量")
        key = (item["month"], scope, name, group)
        _need(key not in seen)
        seen.add(key)
        platform = (group if group in _PLATFORMS else None)
        records.append({"month": item["month"], "financeScopeKey": scope,
            "financeScopeName": name, "financeGroupName": group,
            "platformCandidate": platform, "shopNameCandidate": name,
            "normalizedLineCount": item["line_count"],
            "maxSourceRowCount": item["max_source_row_count"],
            "netshopStableIdentity": None})
    by_name, by_scope = {}, {}
    for item in records:
        name_key = (item["month"], item["financeScopeName"])
        scope_key = (item["month"], item["financeScopeKey"])
        by_name.setdefault(name_key, set()).add((item["financeScopeKey"],
            item["financeGroupName"]))
        by_scope.setdefault(scope_key, set()).add((item["financeScopeName"],
            item["financeGroupName"]))
    candidates = []
    for item in sorted(records, key=lambda row: (row["month"],
            row["financeScopeKey"], row["financeGroupName"],
            row["financeScopeName"])):
        flags = []
        if len(by_name[item["month"], item["financeScopeName"]]) > 1:
            flags.append("same_name_multiple_finance_scopes_or_groups")
        if len(by_scope[item["month"], item["financeScopeKey"]]) > 1:
            flags.append("scope_key_multiple_names_or_groups")
        if item["maxSourceRowCount"] > 1:
            flags.append("possible_pre_normalization_merge")
        if item["platformCandidate"] is None:
            flags.append("platform_unknown")
        candidates.append({**item, "ambiguityFlags": flags,
            "status": "ambiguous_or_unresolved" if flags else
                "finance_identity_observed_only"})
    body = {"schemaVersion": SCHEMA, "months": publication,
        "financeRevisionObserved": dict(revision),
        "candidates": candidates, "candidateCount": len(candidates),
        "missingMonthCount": sum(item["status"] == "missing_month"
            for item in publication),
        "ambiguousCandidateCount": sum(bool(item["ambiguityFlags"])
            for item in candidates),
        "sourcePreMergeAmbiguityRecoverable": False,
        "netshopStableIdentityVerified": False,
        "financeShopMappingVerified": False,
        "mappingAuthorityVerified": False,
        "candidateOnly": True}
    _need(len(canonical(body).encode("utf-8")) <= MAX_OUTPUT_BYTES,
        "财报店铺身份诊断超过固定响应容量")
    return {**body, "diagnosticDigest": digest(body)}


def inspect(principal, months, *, enabled=False):
    """Live admin-fenced read; no netshop join, proof signature or public API."""
    _need(enabled is True, "财报店铺映射诊断默认关闭")
    selected = _months(months)
    actor = owner._actor(principal)
    revision = owner._revision()

    def read():
        month_rows = list(FinanceMonth.objects.filter(month__in=selected)
            .order_by("month").values("month", "batch_id", "status")
            [:finance_source.MAX_MONTHS + 1])
        batch_rows = list(FinanceImportBatch.objects.filter(
            id__in=[item["batch_id"] for item in month_rows]).order_by("id")
            .values("id", "status", "content_hash", "published_state_token")
            [:finance_source.MAX_MONTHS + 1])
        available = {item["month"] for item in _publication(selected,
            month_rows, batch_rows) if item["status"] == "completed_metadata"}
        facts = FinanceLine.objects.filter(month__in=sorted(available),
            scope_type="shop")
        _need(facts.count() <= MAX_SOURCE_LINES,
            "财报店铺原始规范行超过诊断扫描容量")
        groups = list(facts.values("month", "scope_key", "scope_name",
            "group_name").annotate(line_count=Count("id"),
            max_source_row_count=Max("source_row_count")).order_by("month",
            "scope_key", "scope_name", "group_name")[:MAX_CANDIDATES + 1])
        _need(len(groups) <= MAX_CANDIDATES,
            "财报店铺候选超过固定容量，不返回截断列表")
        return month_rows, batch_rows, groups

    before = read()
    _need(owner._actor(principal) == actor and owner._revision() == revision,
        "财报店铺诊断读取期间账号或版本变化")
    result = project(selected, *before, revision)
    _need(owner._actor(principal) == actor and owner._revision() == revision
        and read() == before and owner._revision() == revision
        and owner._actor(principal) == actor,
        "财报店铺诊断输出前身份、发布或范围变化")
    return json.loads(canonical(result))
