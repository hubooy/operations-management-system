"""Internal owning material for JD B2B within one sealed integrated v2 report.

No public route, Agent tool, file renderer or new collection is registered.
Only this report's exact sealed directory can select a B2B source; an absent
selection never proves absence from the platform or database.
"""
from __future__ import annotations

import json
import unicodedata

from business_analysis import b2b_daily_candidate as candidate
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint

from . import business_erp_rollup_materials as report_owner
from .business_sealed import Reader
from .policy import AiError, canonical, digest, identifier


SCHEMA = "business-b2b-report-owning-material-candidate-v1"
MAX_SELECTED_PAGES = 2_000
MAX_SELECTED_ROWS = 200_000
MAX_SELECTED_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 640 * 1024
WINDOWS = candidate.WINDOWS


def _need(ok, message="B端报告来源、封存版本或容量不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _shop(value):
    _need(type(value) is str and 0 < len(value) <= 100
        and value == value.strip()
        and not any(unicodedata.category(char) in {"Cc", "Cs"} for char in value),
        "须选择报告中的精确京东店铺")
    return value


def prepare(report_id, shop, principal, *, checkpoint=None):
    """Return B2B daily/window material only after full sealed-page replay."""
    report_id, shop = identifier(report_id), _shop(shop)
    check = Checkpoint.wrap(checkpoint)
    if check is not None:
        check({"stage": "b2b_report", "phase": "before"})
    _, snapshot, evidence, catalog, fixed = report_owner._bound(report_id, principal)
    reader = Reader(evidence, principal)
    sources = [{key: source[key] for key in ("key", "domain", "query")}
        for source in reader.sources]
    _need(sources == [{key: source[key] for key in ("key", "domain", "query")}
        for source in catalog], "报告目录与封存Reader目录不同")
    _need(any(source["domain"] in {"sales", "netshop"}
        and source["query"].get("platform") == "京东"
        and source["query"].get("shop") == shop for source in sources),
        "所选京东店铺不属于此报告")
    first = sources[0]["query"]
    start, end = first["startDate"], first["endDate"]
    selections = {}
    for window in WINDOWS:
        matches = [source["key"] for source in sources
            if source["domain"] == "netshop"
            and source["query"].get("dataset") == "b2b"
            and source["query"].get("platform") == "京东"
            and source["query"].get("shop") == shop
            and source["query"].get("window", "current") == window]
        _need(len(matches) <= 1, "同店同窗口B端目录身份重复")
        selections[window] = matches[0] if matches else None
    selected = {key for key in selections.values() if key is not None}
    infos = {key: reader.info(key) for key in sorted(selected)}
    _need(sum(info["pageCount"] for info in infos.values()) <= MAX_SELECTED_PAGES
        and sum(info["expected"]["rowCount"] for info in infos.values()) <= MAX_SELECTED_ROWS,
        "B端完整来源超过此内部候选容量")
    pages, total_bytes, total_pages = {}, 0, 0
    try:
        for key in sorted(selected):
            complete = []
            for page in reader.pages(key, checkpoint=check):
                total_pages += 1
                total_bytes += len(canonical(page).encode("utf-8"))
                _need(total_pages <= MAX_SELECTED_PAGES and
                    total_bytes <= MAX_SELECTED_BYTES,
                    "B端完整封存页超过此内部候选容量")
                complete.append(page)
            pages[key] = complete  # Exhaustion certifies the Reader chain.
        material = candidate.prepare_candidate(sources, infos, selections, pages,
            catalog_digest=snapshot["catalogDigest"], platform="京东", shop=shop,
            start_date=start, end_date=end)
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError, RecursionError) as error:
        raise AiError("B端材料未通过完整封存页与精确期间核验",
            "conflict", 409) from error
    if check is not None:
        check({"stage": "b2b_report", "phase": "complete"})
    _need(report_owner._bound(report_id, principal)[4] == fixed,
        "B端材料返回前账号、报告或封存版本变化")
    summary = {window: {"sourceKey": key,
        "status": ("catalogue_only_missing_source" if key is None else
            "selected_sealed_source"),
        "sourceRef": material["windows"][window]["sourceRef"],
        "evidenceDigest": material["windows"][window]["evidenceDigest"],
        "rowCount": infos[key]["expected"]["rowCount"] if key else None,
        "pageCount": infos[key]["pageCount"] if key else None,
        "coverage": material["windows"][window]["coverage"]}
        for window, key in selections.items()}
    result = {"schemaVersion": SCHEMA, "reportBinding": fixed,
        "platform": "京东", "shop": shop, "catalogDigest": snapshot["catalogDigest"],
        "sourceSummary": summary,
        "catalogueMissingWindows": [window for window in WINDOWS
            if selections[window] is None],
        "material": material,
        "sealedReportDirectoryVerified": True,
        "sealedSelectedSourcesFullyReplayed": True,
        "databaseGlobalB2BAbsenceVerified": False,
        "b2bIncludedInErpSales": "unknown",
        "b2bIncludedInPlatformSkuSales": "unknown",
        "b2bShareOfErpSales": None,
        "b2bIncrementalSalesCents": None,
        "crossDomainAmountsAdded": False,
        "authorityVerified": False, "registeredAgentTool": False,
        "registeredRenderer": False}
    _need(len(canonical(result).encode("utf-8")) <= MAX_RESPONSE_BYTES,
        "B端逐日和期间材料超过内部响应容量")
    return {**result, "resultDigest": digest(result)}
