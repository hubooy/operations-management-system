"""Unregistered three-table market material from interval and exact-day roots.

Price-band summary/member rows describe the same selected TOP facts and are
never additive to one another. Rank entry/exit uses two explicitly selected
days from the same report's complete current and baseline market sources.
"""
from dataclasses import dataclass
import json

from business_analysis.promotion_views import _copy
from business_analysis.partitioned import Checkpoint
from business_analysis import market_dynamics, market_dynamics_v2
from . import business_market_dynamics as bands_owning
from . import business_market_observation as rank_owning
from . import business_market_export as previous
from .policy import AiError, canonical, digest


SCHEMA = "business-market-composite-materials-v2"
VIEWS = previous.VIEWS
_TOKEN = object()


def _need(ok, message="市场组合材料与同报告封存来源不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


@dataclass(frozen=True, slots=True, init=False)
class PreparedCompositeMarketExport:
    _manifest: str
    _chunks: tuple

    def __init__(self, token, manifest, chunks):
        if token is not _TOKEN:
            raise AiError("市场组合材料只能由完整封存准备过程构造")
        object.__setattr__(self, "_manifest", canonical(manifest))
        object.__setattr__(self, "_chunks", tuple(chunks))

    @property
    def manifest(self):
        return json.loads(self._manifest)

    def ndjson_pages(self, view):
        if view not in VIEWS:
            raise AiError("市场组合材料视图无效")
        return iter(tuple(raw for name, raw in self._chunks if name == view))


def prepare(report_id, current_key, baseline_key, current_day, baseline_day,
            principal, *, bands, checkpoint=None, limits=None):
    """Return no partial material if either owning context or final fence fails."""
    bounds = previous._bounds(limits)
    check = Checkpoint.wrap(checkpoint)
    fixed_bands = _copy(bands, 8192)
    chunks, specs = [], []
    counters = {"rows": 0, "bytes": 0, "pages": 0}
    with bands_owning.table(report_id, current_key, "price_band", principal,
            bands=fixed_bands) as (price, price_binding):
        for view in VIEWS[:2]:
            previous._add(view, price, price_binding, chunks, specs, counters, bounds, check)
    with rank_owning.table(report_id, current_key, baseline_key,
            current_day, baseline_day, principal) as (rank, rank_binding):
        previous._add(VIEWS[2], rank, rank_binding,
            chunks, specs, counters, bounds, check)
    fixed = price_binding["reportBinding"]
    source_descriptors = {"priceBand": price["sources"][0],
        "rankCurrent": rank["sources"][0], "rankBaseline": rank["sources"][1]}
    _need(rank_binding["reportBinding"] == fixed
        and source_descriptors["priceBand"] == source_descriptors["rankCurrent"]
        and price_binding["sourceKey"] == current_key
        and price_binding["baselineKey"] is None
        and price_binding["view"] == "price_band"
        and price_binding["algorithmVersion"] == market_dynamics.ALGORITHM_VERSION
        and price_binding["bandsDigest"] == digest(fixed_bands)
        and rank_binding["currentSourceKey"] == current_key
        and rank_binding["baselineSourceKey"] == baseline_key
        and rank_binding["currentObservationDate"] == current_day
        and rank_binding["baselineObservationDate"] == baseline_day
        and rank_binding["algorithmVersion"] == market_dynamics_v2.ALGORITHM_VERSION
        and fixed["reportId"] == report_id
        and specs[0]["sourceTableDigest"] == specs[1]["sourceTableDigest"]
        and specs[0]["binding"] == specs[1]["binding"])
    for spec in specs:
        spec["bindingDigest"] = digest(spec["binding"])
        spec["sourceDescriptorDigest"] = digest(
            [source_descriptors["priceBand"]] if spec["view"] != "rank_entry_exit"
            else [source_descriptors["rankCurrent"], source_descriptors["rankBaseline"]])
    manifest = {"schemaVersion": SCHEMA, "reportBinding": json.loads(canonical(fixed)),
        "priceBandSourceKey": current_key, "rankCurrentSourceKey": current_key,
        "rankBaselineKey": baseline_key,
        "sourceDescriptors": json.loads(canonical(source_descriptors)),
        "sourceDescriptorsDigest": digest(source_descriptors),
        "observationDates": {"current": current_day, "baseline": baseline_day},
        "rankObservationCoverage": json.loads(canonical(rank["observationCoverage"])),
        "bands": fixed_bands,
        "algorithms": {"priceBand": market_dynamics.ALGORITHM_VERSION,
            "rankEntryExit": market_dynamics_v2.ALGORITHM_VERSION},
        "tables": specs, "rowCount": counters["rows"],
        "pageCount": counters["pages"], "ndjsonBytes": counters["bytes"],
        "authority": {"selectedTopSampleReconciled": True,
            "wholeMarketCoverageVerified": False, "ownProductIdentityVerified": False,
            "priceSummaryAndMembersAdditive": False,
            "marketAndOwnSalesAdditive": False},
        "limitations": ["价格带汇总与成员明细是同一TOP样本，不可相加。",
            "进出榜只比较固定两日；日期缺失与未入TOP样本不同，均不等于零销量。",
            "市场SKU/SPU、样本成交区间不证明本店、ERP或B端销售归属。"],
        "authorityVerified": False, "registeredRenderer": False}
    manifest["manifestDigest"] = digest(manifest)
    if len(canonical(manifest).encode("utf-8")) + counters["bytes"] > bounds["maxBytes"]:
        raise AiError("市场组合清单及完整材料超过容量", "payload_too_large", 413)
    if check is not None:
        check({"stage": "market_composite_export", "phase": "complete"})
    bands_owning.report_binding._revalidate(fixed, principal)
    return PreparedCompositeMarketExport(_TOKEN, manifest, chunks)
