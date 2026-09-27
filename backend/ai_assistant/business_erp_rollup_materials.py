"""Unregistered ERP-only materials from one exact sealed integrated report.

The owning Reader fully replays the two selected v2 sources. Temporary fact
assignment and five-grain rollups remain alive only inside prepare().
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json

from business_analysis import erp_fact_assignment, erp_fact_rollups
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint
from . import business_evidence_store, business_integrated, models as m
from .business_sealed import Reader
from .policy import AiError, canonical, digest, identifier


SCHEMA = "business-erp-report-rollup-materials-candidate-v1"
MAX_MANIFEST_BYTES = 128 * 1024
_TOKEN = object()


def _need(ok, message="ERP逐日材料与固定报告或封存来源不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _bound(report_id, principal):
    report = m.AiReportRun.objects.filter(pk=identifier(report_id)).select_related("workflow").first()
    if report is None:
        raise AiError("ERP逐日材料固定报告不存在", "not_found", 404)
    actual, snapshot, _, evidence, sources = business_integrated.bound(report, principal)
    _need(evidence.status == "sealed" and business_evidence_store.is_v2(evidence),
        "ERP逐日材料只接受已封存v2报告")
    binding = {"schemaVersion": "business-erp-report-rollup-binding-v1",
        "reportId": actual.id, "workflowId": actual.workflow_id,
        "reportSnapshotDigest": digest(actual.snapshot_json),
        "workflowInputDigest": digest(actual.workflow.input_json),
        "principalKey": digest([principal.email.lower(), canonical(principal.scope)]),
        "evidenceRunId": evidence.id, "evidenceVersion": evidence.version,
        "evidencePlanDigest": snapshot["evidencePlanDigest"],
        "catalogDigest": snapshot["catalogDigest"],
        "sealedDigest": snapshot["sealedDigest"],
        "mappingPlanDigest": snapshot["mappingPlanDigest"]}
    return actual, snapshot, evidence, sources, binding


@dataclass(frozen=True, slots=True, init=False)
class PreparedErpRollups:
    _manifest_json: str
    _rollups: erp_fact_rollups.PreparedRollups

    def __init__(self, token, manifest, rollups):
        if token is not _TOKEN:
            raise AiError("ERP逐日材料只能由完整封存准备过程构造", "conflict", 409)
        object.__setattr__(self, "_manifest_json", canonical(manifest))
        object.__setattr__(self, "_rollups", rollups)

    @property
    def manifest(self):
        self._rollups.manifest  # Enforce the temporary material lifetime.
        return json.loads(self._manifest_json)

    def ndjson_pages(self, kind):
        self._rollups.manifest
        return self._rollups.ndjson_pages(kind)

    def tables(self):
        self._rollups.manifest
        return self._rollups.tables()


@contextmanager
def prepare(report_id, pair_key, sales_key, master_key, principal, *,
            checkpoint=None, assignment_scratch_bytes=None, rollup_scratch_bytes=None):
    """Yield five complete ERP-only tables after full two-source replay.

    Callers must consume tables/NDJSON inside the context; no authority is
    conferred and the final report/account fence runs on context exit.
    """
    pair_key, sales_key, master_key = (identifier(value) for value in
        (pair_key, sales_key, master_key))
    check = Checkpoint.wrap(checkpoint)
    if check is not None:
        check({"stage": "erp_report_rollups", "phase": "before"})
    actual, snapshot, evidence, sources, fixed = _bound(report_id, principal)
    pair = next((item for item in snapshot["mappingPlan"]["pairs"]
        if item["pairKey"] == pair_key), None)
    _need(pair is not None and pair["salesKey"] == sales_key
        and pair["masterKey"] == master_key,
        "ERP来源键与报告固定mapping pair不一致")
    reader = Reader(evidence, principal)
    indexed = {source["key"]: source for source in sources}
    _need(sales_key in indexed and master_key in indexed
        and indexed[sales_key]["domain"] == "sales"
        and indexed[master_key]["domain"] == "netshop"
        and indexed[master_key]["query"].get("dataset") == "master")
    sales_info, master_info = reader.info(sales_key), reader.info(master_key)
    kwargs = {"checkpoint": check}
    if assignment_scratch_bytes is not None:
        kwargs["max_scratch_bytes"] = assignment_scratch_bytes
    try:
        with erp_fact_assignment.assign_facts(sources, snapshot["mappingPlan"],
                pair_key, reader.pages(sales_key, checkpoint=check),
                reader.pages(master_key, checkpoint=check),
                sales_info["expected"], master_info["expected"], **kwargs) as ledger:
            source = ledger.summary()
            _need(source["mappingPlanDigest"] == snapshot["mappingPlanDigest"]
                and source["pairKey"] == pair_key
                and source["sourceProofs"]["sales"] == sales_info["expected"]
                and source["sourceProofs"]["master"] == master_info["expected"])
            rollup_kwargs = ({"max_scratch_bytes": rollup_scratch_bytes}
                if rollup_scratch_bytes is not None else {})
            with erp_fact_rollups.prepare(ledger, **rollup_kwargs) as rollups:
                material = rollups.manifest
                manifest = {"schemaVersion": SCHEMA,
                    "reportBinding": fixed, "pairKey": pair_key,
                    "salesKey": sales_key, "masterKey": master_key,
                    "salesQueryDigest": digest(indexed[sales_key]["query"]),
                    "masterQueryDigest": digest(indexed[master_key]["query"]),
                    "sourceProofs": source["sourceProofs"],
                    "assignmentSummaryDigest": source["resultDigest"],
                    "rollupManifestDigest": material["manifestDigest"],
                    "tables": material["tables"],
                    "rowCount": material["outputRows"],
                    "ndjsonBytes": material["ndjsonBytes"],
                    "sourceRowCount": material["sourceRowCount"],
                    "sourceTotals": material["sourceTotals"],
                    "matchedTotals": material["matchedTotals"],
                    "unassignedTotals": material["unassignedTotals"],
                    "authorityVerified": False,
                    "registeredRenderer": False,
                    "netshopAdFinanceCombined": False,
                    "historicalOwnershipVerified": False,
                    "limitations": material["limitations"]}
                manifest["manifestDigest"] = digest(manifest)
                _need(len(canonical(manifest).encode("utf-8")) <= MAX_MANIFEST_BYTES,
                    "ERP逐日完整材料清单超过容量")
                if check is not None:
                    check({"stage": "erp_report_rollups", "phase": "complete"})
                _need(_bound(report_id, principal)[4] == fixed,
                    "ERP逐日材料准备期间报告或账号变化")
                prepared = PreparedErpRollups(_TOKEN, manifest, rollups)
                yield prepared
                _need(_bound(report_id, principal)[4] == fixed,
                    "ERP逐日材料读取期间报告或账号变化")
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError) as error:
        raise AiError("ERP逐日材料未通过完整来源、归属及守恒核验", "conflict", 409) from error
