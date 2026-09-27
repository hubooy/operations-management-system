"""Unregistered three-table market material from one sealed promotion-v2 report.

The selected owning sealed v2 page chains are fully replayed. A caller
must consume the temporary typed tables inside prepare(); no Agent or file
publication authority is conferred.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json

from business_analysis import market_report_tables_v2
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint

from . import business_market_composite_export as composite
from . import business_promotion_market_admission as admission
from .policy import AiError, canonical, digest, identifier


SCHEMA = "business-market-report-owning-material-candidate-v1"
MAX_MANIFEST_BYTES = 128 * 1024
_TOKEN = object()


def _need(ok, message="市场材料与同一报告封存来源不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


@dataclass(frozen=True, slots=True, init=False)
class PreparedMarketReportMaterial:
    _manifest_json: str
    _summary_json: str
    _tables: tuple
    _active: list

    def __init__(self, token, manifest, summary, tables, active):
        if token is not _TOKEN:
            raise AiError("市场正式材料只能由完整封存准备过程构造", "conflict", 409)
        object.__setattr__(self, "_manifest_json", canonical(manifest))
        object.__setattr__(self, "_summary_json", canonical(summary))
        object.__setattr__(self, "_tables", tuple(tables))
        object.__setattr__(self, "_active", active)

    @property
    def manifest(self):
        _need(self._active[0], "市场临时材料已结束")
        return json.loads(self._manifest_json)

    @property
    def summary(self):
        _need(self._active[0], "市场临时材料已结束")
        return json.loads(self._summary_json)

    @property
    def tables(self):
        _need(self._active[0], "市场临时材料已结束")
        return self._tables


def _matched(manifest, fixed, selector):
    candidate = fixed["candidate"]
    descriptors = manifest["sourceDescriptors"]
    specs = manifest["tables"]
    _need(manifest["schemaVersion"] == composite.SCHEMA
        and manifest["manifestDigest"] == digest({key: value for key, value in
            manifest.items() if key != "manifestDigest"})
        and digest(manifest["reportBinding"]) ==
            fixed["binding"]["reportBindingDigest"]
        and manifest["priceBandSourceKey"] == selector["priceBandSourceKey"]
        and manifest["rankCurrentSourceKey"] == selector["rankCurrentSourceKey"]
        and manifest["rankBaselineKey"] == selector["rankBaselineKey"]
        and manifest["bands"] == selector["bands"]
        and manifest["observationDates"] == {
            "current": selector["currentObservationDate"],
            "baseline": selector["baselineObservationDate"]}
        and manifest["rankObservationCoverage"] ==
            candidate["observationCoverage"]
        and manifest["authority"] == {
            "selectedTopSampleReconciled": True,
            "wholeMarketCoverageVerified": False,
            "ownProductIdentityVerified": False,
            "priceSummaryAndMembersAdditive": False,
            "marketAndOwnSalesAdditive": False}
        and manifest["authorityVerified"] is False
        and [item["view"] for item in specs] == list(composite.VIEWS)
        and specs[0]["sourceTableDigest"] == specs[1]["sourceTableDigest"],
        "市场三表清单与本报告准入身份不一致")
    for side, item in (("current", descriptors["rankCurrent"]),
                       ("baseline", descriptors["rankBaseline"])):
        source = candidate["sources"][side]
        _need(all(item[key] == source[key] for key in
            ("key", "domain", "query", "queryDigest")),
            "市场本期或基期来源不是已准入目录")
    _need(descriptors["priceBand"] == descriptors["rankCurrent"]
        and manifest["sourceDescriptorsDigest"] == digest(descriptors)
        and all(spec["bindingDigest"] == digest(spec["binding"])
            for spec in specs), "市场价格带与进出榜不属于同一封存目录")


@contextmanager
def prepare(report_id, selector, principal, *, checkpoint=None, limits=None):
    """Yield fully checked typed market tables inside a bounded lifetime."""
    report_id = identifier(report_id, "reportId")
    check = Checkpoint.wrap(checkpoint)
    if check is not None:
        check({"stage": "market_report_material", "phase": "before"})
    fixed = admission.require_observed(report_id, selector, principal,
        checkpoint=check)
    selected = fixed["candidate"]["selector"]
    try:
        material = composite.prepare(report_id,
            selected["rankCurrentSourceKey"], selected["rankBaselineKey"],
            selected["currentObservationDate"],
            selected["baselineObservationDate"], principal,
            bands=selected["bands"], checkpoint=check, limits=limits)
        manifest = material.manifest
        _matched(manifest, fixed, selected)
        _need(len(canonical(manifest).encode("utf-8")) <= MAX_MANIFEST_BYTES)
        with market_report_tables_v2.tables(manifest,
                {view: material.ndjson_pages(view) for view in composite.VIEWS}) as (summary, tables):
            _need(len(tables) == 3 and summary["authorityVerified"] is False
                and summary["sourceMaterials"] == manifest)
            result = {"schemaVersion": SCHEMA,
                "reportId": report_id,
                "admissionDigest": fixed["bindingDigest"],
                "reportBinding": manifest["reportBinding"],
                "marketManifestDigest": manifest["manifestDigest"],
                "tableViews": list(composite.VIEWS),
                "rowCount": manifest["rowCount"],
                "observationCoverage": manifest["rankObservationCoverage"],
                "selectedSealedSourcesFullyReplayed": True,
                "typedMarketRowsVerified": True,
                "wholeMarketCoverageVerified": False,
                "ownProductIdentityVerified": False,
                "marketAndOwnSalesAdditive": False,
                "priceSummaryAndMembersAdditive": False,
                "authorityVerified": False,
                "registeredAgentTool": False,
                "registeredRenderer": False}
            result["summaryDigest"] = digest(result)
            active = [True]
            prepared = PreparedMarketReportMaterial(_TOKEN, manifest, result,
                tables, active)
            try:
                yield prepared
            finally:
                active[0] = False
                if check is not None:
                    check({"stage": "market_report_material", "phase": "complete"})
                _need(admission.require_observed(report_id, selected,
                    principal)["bindingDigest"] == fixed["bindingDigest"],
                    "市场材料返回前报告、账号或封存来源改变")
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError, RecursionError) as error:
        raise AiError("市场材料未通过同报告封存页链或类型表核验",
            "conflict", 409) from error
