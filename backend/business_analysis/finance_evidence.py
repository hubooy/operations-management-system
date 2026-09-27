"""Pure, versioned finance evidence projection for a future owning collector.

The caller must obtain ``source, binding`` from ``finance.business_analysis_source``
inside its context manager and publish only after normal context exit. Replaying
these bytes cannot grant authority, persistence, or proof that an Agent read them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import finance_source
from .contracts import AnalysisContractError, canonical, digest

SCHEMA = "business-finance-evidence-v1"
PAGE_SCHEMA = "business-finance-evidence-page-v1"
MAX_TOOL_BYTES = 38_000
MAX_PAGES = 2_000
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
ZERO_DIGEST = "0" * 64


def _require(condition, message):
    if not condition:
        raise AnalysisContractError(message)


def _fields(value, names):
    _require(type(value) is dict and set(value) == set(names), "财务证据字段集合无效")


def _source_rows(source, manifest):
    """Verify the whole original page chain before constructing any new page."""
    rows, originals, offset, last_id, byte_count = [], [], 0, 0, 0
    total = manifest["rowCount"]
    finance_source._integer(total, 0, finance_source.MAX_ROWS)
    while True:
        _require(len(originals) < MAX_PAGES, "财务证据超过完整页上限")
        page = source.page(offset)
        _fields(page, {"schemaVersion", "sourceDigest", "rows", "pagination", "pageDigest"})
        _require(page["schemaVersion"] == finance_source.PAGE_SCHEMA
                 and page["sourceDigest"] == manifest["sourceDigest"], "财报原页来源身份变化")
        expected_digest = digest({key: value for key, value in page.items() if key != "pageDigest"})
        _require(page["pageDigest"] == expected_digest, "财报原页摘要无效")
        pagination = page["pagination"]
        _fields(pagination, {"offset", "returned", "total", "nextOffset"})
        items = page["rows"]
        _require(type(items) is list and len(items) <= finance_source.PAGE_ROWS
                 and pagination["offset"] == offset and type(pagination["offset"]) is int
                 and pagination["returned"] == len(items) and type(pagination["returned"]) is int
                 and pagination["total"] == total and type(pagination["total"]) is int,
                 "财报原页分页不连续")
        _require(bool(items) or (total == 0 and offset == 0), "财报原页不得提前为空")
        next_offset = offset + len(items)
        _require(pagination["nextOffset"] == (next_offset if next_offset < total else None),
                 "财报原页后继偏移无效")
        encoded = canonical(page)
        byte_count += len(encoded.encode("utf-8"))
        _require(byte_count <= MAX_EVIDENCE_BYTES, "财报原页超出证据容量")
        for record in items:
            _fields(record, finance_source.ROW_FIELDS | {"rowId"})
            raw = {key: record[key] for key in finance_source.ROW_FIELDS}
            _require(type(raw["id"]) is int and raw["id"] > last_id,
                     "财报行未按真实持久 ID 递增")
            _require(record["rowId"] == digest({"revision": manifest["revision"], "row": raw}),
                     "财报行引用与完整原值不一致")
            rows.append(raw)
            last_id = raw["id"]
        originals.append(page)
        if next_offset == total:
            break
        offset = next_offset
    _require(len(rows) == total, "财报原页行数不完整")
    rebuilt = finance_source.build(rows, query=manifest["query"], revision=manifest["revision"],
        months=manifest["months"], batches=manifest["batches"],
        analysis_period=manifest["periodAlignment"]["analysisPeriod"])
    _require(canonical(rebuilt.manifest) == canonical(manifest), "财报原来源清单无法从完整行重建")
    offset = 0
    for original in originals:
        expected = rebuilt.page(offset)
        _require(canonical(original) == canonical(expected), "财报原页无法从完整行重建")
        offset = original["pagination"]["nextOffset"]
        if offset is None:
            break
    return [dict(record) for page in originals for record in page["rows"]]


def _binding(binding, manifest):
    _fields(binding, {"schemaVersion", "actor", "sourceDigest", "revision",
                      "publicationDigest", "persistentEvidenceVerified"})
    _require(binding["schemaVersion"] == "finance-owning-memory-binding-v1"
             and binding["sourceDigest"] == manifest["sourceDigest"]
             and binding["revision"] == manifest["revision"]
             and binding["publicationDigest"] == digest([manifest["months"], manifest["batches"]])
             and binding["persistentEvidenceVerified"] is False,
             "财务内存来源绑定与完整发布不一致")
    actor = binding["actor"]
    _fields(actor, {"email", "role", "status", "scope", "version"})
    _require(type(actor["email"]) is str and bool(actor["email"])
             and actor["role"] == "admin" and actor["status"] == "active"
             and actor["scope"] is None and type(actor["version"]) is int
             and actor["version"] >= 1, "财务来源账号绑定无效")
    return digest(binding)


@dataclass(frozen=True, slots=True)
class FinanceEvidence:
    """Canonical copies of a complete untrusted-for-authorization projection."""
    _manifest_json: str
    _pages: tuple[tuple[int, str], ...]

    @property
    def manifest(self):
        return json.loads(self._manifest_json)

    def page(self, offset=0):
        finance_source._integer(offset)
        for start, raw in self._pages:
            if start == offset:
                return json.loads(raw)
        raise AnalysisContractError("财务证据页偏移必须是实际返回的边界")


def prepare(source, binding):
    """Rebuild all owning-source bytes, then emit complete <=38 KiB pages.

    Every displayed month and amount remains a source scalar. No period money,
    profit, ratio, SKU attribution, or cross-source arithmetic is calculated.
    """
    _require(type(source) is finance_source.FinanceSource, "财务来源须为原有完整内存源")
    manifest = source.manifest
    _fields(manifest, {"schemaVersion", "query", "revision", "months", "batches", "periodAlignment",
                       "rowCount", "rowChainDigest", "coverage", "allRequestedMonthsPublished",
                       "allCoreMetricsPresent", "sourceAuthorityVerified", "businessCoverageVerified",
                       "limitations", "sourceDigest"})
    _require(manifest["schemaVersion"] == finance_source.SCHEMA
             and manifest["sourceAuthorityVerified"] is False
             and manifest["businessCoverageVerified"] is False,
             "财报原来源版本或验证标志无效")
    records = _source_rows(source, manifest)
    binding_digest = _binding(binding, manifest)
    query_digest = digest(manifest["query"])
    pages, offset, previous = [], 0, ZERO_DIGEST
    total = len(records)
    while offset < total or not pages:
        _require(len(pages) < MAX_PAGES, "财务证据超过完整页上限")
        selected = []

        def assemble(items):
            end = offset + len(items)
            value = {"schemaVersion": PAGE_SCHEMA, "sourceDigest": manifest["sourceDigest"],
                     "queryDigest": query_digest, "previousPageDigest": previous,
                     "rows": items, "pagination": {"offset": offset, "returned": len(items),
                     "total": total, "nextOffset": end if end < total else None}}
            return {**value, "pageDigest": digest(value)}

        for record in records[offset:offset + finance_source.PAGE_ROWS]:
            trial = assemble([*selected, record])
            if len(canonical(trial).encode("utf-8")) > MAX_TOOL_BYTES:
                _require(bool(selected), "单条财务事实超过完整工具页上限")
                break
            selected.append(record)
        page = assemble(selected)
        raw = canonical(page)
        _require(len(raw.encode("utf-8")) <= MAX_TOOL_BYTES, "财务证据页超过完整工具容量")
        pages.append((offset, raw))
        previous = page["pageDigest"]
        offset += len(selected)
        if offset == total:
            break
    header = {"schemaVersion": SCHEMA, "sourceManifest": manifest,
              "owningBindingDigest": binding_digest, "queryDigest": query_digest,
              "rowCount": total, "pageCount": len(pages), "lastPageDigest": previous,
              "sourceAuthorityVerified": False, "persistentEvidenceVerified": False,
              "businessCoverageVerified": False, "agentReadVerified": False,
              "numericPolicy": {"nullIsZero": False, "sumTotalsAndDetails": False,
                                "sumSourceRates": False, "inferSkuProfit": False,
                                "proratePartialMonths": False}}
    header["evidenceDigest"] = digest(header)
    raw_header = canonical(header)
    _require(len(raw_header.encode("utf-8")) <= MAX_TOOL_BYTES, "财务证据清单超过工具容量")
    _require(len(raw_header.encode("utf-8")) + sum(len(raw.encode("utf-8")) for _, raw in pages)
             <= MAX_EVIDENCE_BYTES, "财务证据完整交付超过容量")
    return FinanceEvidence(raw_header, tuple(pages))
