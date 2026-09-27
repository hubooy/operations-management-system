"""Trusted reuse for exactly one integrated content validation.

The memo stores completed calculated JSON only. Every use still reloads the
report, current permissions, immutable parameters, catalog and sealed binding.
Each job's ledger is independently checked by the receipt verifier.
"""
import json

from business_analysis import budget_reference, identity_partitioned, mapped_results
from . import business_integrated as contract, business_budget_store as budgets
from .business_integrated_reuse import CompletedReuse
from .policy import AiError, canonical, digest


class ContentReuse:
    def __init__(self, report, principal):
        self._report, self._principal = report, principal
        self._memo = CompletedReuse(self._binding(report, principal),
            validate=lambda: self._binding(report, principal))

    @staticmethod
    def _binding(report, principal):
        actual, snapshot, reference, evidence, sources = contract.bound(report, principal)
        return {"schemaVersion":"business-integrated-content-reuse-v1",
            "principalEmail":principal.email.lower(), "principalRole":principal.role,
            "principalScope":principal.scope, "ownerEmail":actual.owner_email,
            "reportScope":json.loads(actual.scope_json), "reportId":actual.id,
            "workflowId":actual.workflow_id, "snapshotDigest":digest(actual.snapshot_json),
            "reference":reference, "sourcesDigest":digest(sources),
            "evidencePlanDigest":digest(evidence.plan_json), "evidenceStateDigest":digest(evidence.state_json),
            "mappingPlanDigest":snapshot["mappingPlanDigest"],
            "mappingAlgorithm":identity_partitioned.ALGORITHM_VERSION,
            "mappedTableAlgorithm":mapped_results.ALGORITHM_VERSION,
            "nativeTableSchema":"business-result-table-v1",
            "budgetCalculator":budget_reference.CALCULATOR_VERSION}

    def __enter__(self):
        self._memo.__enter__()
        return self

    def __exit__(self, kind, error, trace):
        return self._memo.__exit__(kind, error, trace)

    def budget(self, report, principal):
        # Rebuild this small object every time. Never memoize PreparedBudget,
        # permission decisions, parameters or caller-supplied proof results.
        binding = self._binding(report, principal)
        fixed = budgets.binding_for_report(report, principal)
        result = self._memo.resolve("budget", {"budgetRef":fixed.reference,
            "calculatorVersion":budget_reference.CALCULATOR_VERSION},
            lambda: budgets.load(report, principal).result, binding=binding)
        return budgets.PreparedBudget(fixed.id, fixed.plan_json, fixed.binding_json, canonical(result))

    def analysis(self, prepared, evidence, sources, arguments, principal, loader):
        binding = self._binding(self._report, principal)
        if (digest(prepared.snapshot_json) != binding["snapshotDigest"]
                or prepared.owner_email != binding["ownerEmail"]
                or prepared.scope_json != canonical(binding["reportScope"])
                or prepared.reference != binding["reference"]
                or digest(sources) != binding["sourcesDigest"]
                or digest(evidence.plan_json) != binding["evidencePlanDigest"]
                or digest(evidence.state_json) != binding["evidenceStateDigest"]
                or evidence.id != binding["reference"]["evidenceRunId"]
                or evidence.version != binding["reference"]["evidenceVersion"]):
            raise AiError("分析复用输入不属于当前固定报告", "derived_reuse_binding_changed", 409)
        # Exact arguments distinguish absent/default values as well as all
        # selectors; no key is derived from the untrusted tool response.
        return self._memo.resolve("analysis", {"arguments":arguments,
            "mappingAlgorithm":identity_partitioned.ALGORITHM_VERSION,
            "tableAlgorithm":mapped_results.ALGORITHM_VERSION,
            "nativeTableSchema":binding["nativeTableSchema"]}, loader, binding=binding)
