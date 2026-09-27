"""The action contract must reject unsupported scope, amounts and authority."""
from copy import deepcopy
from unittest import TestCase

from . import diagnostic_action_plan_v1 as plan
from .contracts import AnalysisContractError, digest
from .test_report_composition_v1 import call, fixtures


def report():
    return call(fixtures())


def market_report():
    preview = {"schemaVersion": "business-market-v2-fifth-read-preview-v1",
        "serverFullMarketMaterialVerified": True, "persistedRead": False,
        "registeredTool": False, "authorityVerified": False,
        "mode": "summary", "marketManifestDigest": "b" * 64,
        "sourceReportId": "separate-market-report"}
    preview["resultDigest"] = digest(preview)
    return call(fixtures(), market=preview)


def market_result():
    value = {"schemaVersion": plan.MARKET_RESULT_SCHEMA,
        "executionReportId": "separate-market-execution",
        "roles": [{"role": role} for role in plan.MARKET_ROLES],
        "candidateChecksPassed": True}
    for key in ("persistedSourceIndependentlyLoaded",
                "owningRowsIndependentlyReplayed", "proseNumbersVerified",
                "agentExecutionAuthorized", "numericCitationAllowed",
                "humanReviewApproved", "reportPublishAuthorized"):
        value[key] = False
    return value


def action(value, dimension, key, direction, basis, metric, *, index=1):
    source = next(row for row in value["tableManifest"]
                  if row["tableKey"] == key)
    return {"id": f"review-{index}", "dimension": dimension,
        "targetIdentityStatus": "dimension_only_no_row_citation",
        "phase": "D01-D07" if direction == "repair_source" else "D08-D14",
        "direction": direction, "ownerRole": "data_owner" if direction ==
            "repair_source" else "business_owner",
        "primaryEvidence": {key: source[key] for key in plan.REF_FIELDS},
        "contextEvidence": [], "factBasis": basis,
        "budget": {"principle": "no_new_spend", "capCents": 0,
                   "capSource": "no_new_spend_policy"},
        "kpi": {"metric": metric, "sourceTableKey": key,
                "baselineValue": None, "targetValue": None,
                "verificationStatus":
                    "definition_only_pending_owner_row_and_settlement"},
        "observation": {"days": 14,
            "startGate": "after_owner_source_recheck_and_human_approval"},
        "stopConditions": list(plan.STOP_CONDITIONS),
        "rollback": "manual_restore_last_approved_configuration",
        "executionAllowed": False, "automaticBidAllowed": False,
        "automaticPriceChangeAllowed": False}


def five_dimensions(value):
    return [action(value, "store", "store_comparison", "review_store_mix",
                   "table_level_candidate_only", "erp_net_sales_cents", index=1),
            action(value, "category", "category", "reconcile_identity",
                   "historical_identity_unverified", "source_coverage", index=2),
            action(value, "spu", "spu_native", "review_spu_mix",
                   "table_level_candidate_only", "native_sales_cents", index=3),
            action(value, "sku", "sku", "review_sku_mix",
                   "table_level_candidate_only", "native_sales_cents", index=4),
            action(value, "keyword", "keyword_sku", "repair_source",
                   "source_gap", "source_coverage", index=5)]


def prepare(value, actions, **kwargs):
    return plan.prepare_candidate(value, actions, enabled=True,
        verify_current_composition=lambda current:
            current["compositionDigest"] == value["compositionDigest"], **kwargs)


class DiagnosticActionPlanTests(TestCase):
    def test_five_dimension_review_plan_keeps_unverified_numbers_and_execution_off(self):
        value = report()
        result = prepare(value, five_dimensions(value))
        self.assertEqual(result["actionCount"], 5)
        self.assertEqual(result["reportRoot"]["compositionDigest"],
                         value["compositionDigest"])
        self.assertEqual([row["dimension"] for row in result["actions"]],
                         ["store", "category", "spu", "sku", "keyword"])
        for item in result["actions"]:
            self.assertIsNone(item["kpi"]["baselineValue"])
            self.assertIsNone(item["kpi"]["targetValue"])
            self.assertFalse(item["executionAllowed"])
        self.assertFalse(result["marketSampleEqualsOwnSales"])
        self.assertIsNone(result["b2bIncrementalSalesCents"])
        self.assertFalse(result["publicationAllowed"])
        self.assertEqual(result["candidateDigest"], digest({key: part
            for key, part in result.items() if key != "candidateDigest"}))
        original = deepcopy(result["actions"])
        source = five_dimensions(value)
        snapshot = prepare(value, source)
        source[0]["direction"] = "repair_source"
        self.assertEqual(snapshot["actions"], original)

    def test_default_off_and_owning_recheck_required(self):
        value = report(); rows = five_dimensions(value)
        with self.assertRaises(AnalysisContractError):
            plan.prepare_candidate(value, rows)
        with self.assertRaises(AnalysisContractError):
            plan.prepare_candidate(value, rows, enabled=True,
                                   verify_current_composition=lambda _: False)
        with self.assertRaises(AnalysisContractError):
            plan.prepare_candidate(value, rows, enabled=True,
                                   verify_current_composition=None)

    def test_tampered_report_root_or_table_digest_rejected(self):
        value = report(); rows = five_dimensions(value)
        bad = deepcopy(value); bad["reportId"] = "another-report"
        with self.assertRaises(AnalysisContractError): prepare(bad, rows)
        bad = deepcopy(rows); bad[0]["primaryEvidence"]["sourceDigest"] = "0" * 64
        with self.assertRaises(AnalysisContractError): prepare(value, bad)
        bad = deepcopy(value); bad["tableManifest"][1]["sourceDigest"] = "0" * 64
        bad["compositionDigest"] = digest({key: part for key, part in bad.items()
                                           if key != "compositionDigest"})
        with self.assertRaises(AnalysisContractError): prepare(bad, rows)

    def test_missing_keyword_source_only_allows_repair_and_no_number(self):
        value = report(); rows = five_dimensions(value)
        for change in (lambda item: item.update(direction="review_keyword_match"),
                       lambda item: item.update(factBasis="table_level_candidate_only"),
                       lambda item: item["kpi"].update(targetValue=12000),
                       lambda item: item.update(targetIdentityStatus=
                           "verified_specific_keyword")):
            bad = deepcopy(rows); change(bad[-1])
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                prepare(value, bad)

    def test_specific_sku_and_historical_growth_cannot_be_asserted(self):
        value = report(); rows = five_dimensions(value)
        bad = deepcopy(rows); bad[3]["entityId"] = "sku-123"
        with self.assertRaises(AnalysisContractError): prepare(value, bad)
        bad = deepcopy(rows); bad[1]["factBasis"] = "historical_growth_verified"
        with self.assertRaises(AnalysisContractError): prepare(value, bad)
        bad = deepcopy(rows); bad[1]["factBasis"] = "historical_identity_unverified"
        bad[1]["direction"] = "review_category_mix"
        with self.assertRaises(AnalysisContractError): prepare(value, bad)

    def test_market_sample_and_finance_b2b_cannot_be_primary_or_own_sales(self):
        value = report(); rows = five_dimensions(value)
        for key in ("market_sample", "finance_month", "b2b_daily"):
            bad = deepcopy(rows)
            source = next(item for item in value["tableManifest"]
                          if item["tableKey"] == key)
            bad[0]["primaryEvidence"] = {field: source[field]
                                          for field in plan.REF_FIELDS}
            with self.subTest(key=key), self.assertRaises(AnalysisContractError):
                prepare(value, bad)
        bad = deepcopy(rows)
        market = next(item for item in value["tableManifest"]
                      if item["tableKey"] == "market_sample")
        bad[0]["contextEvidence"] = [{field: market[field]
                                      for field in plan.REF_FIELDS}]
        with self.assertRaises(AnalysisContractError): prepare(value, bad)
        forged = deepcopy(value)
        forged["financeB2bProof"]["b2bIncrementalSalesCents"] = 500
        forged["financeB2bProof"]["candidateDigest"] = digest({key: part
            for key, part in forged["financeB2bProof"].items()
            if key != "candidateDigest"})
        forged["componentDigests"]["financeB2b"] = forged["financeB2bProof"]["candidateDigest"]
        for item in forged["tableManifest"]:
            if item["tableKey"] in {"finance_month", "b2b_daily"}:
                item["sourceDigest"] = forged["componentDigests"]["financeB2b"]
        forged["compositionDigest"] = digest({key: part for key, part in forged.items()
                                              if key != "compositionDigest"})
        with self.assertRaises(AnalysisContractError): prepare(forged, rows)

    def test_market_five_agent_candidate_is_external_context_only(self):
        value = market_report(); rows = five_dimensions(value)
        market = next(item for item in value["tableManifest"]
                      if item["tableKey"] == "market_sample")
        rows[0]["contextEvidence"] = [{field: market[field]
                                       for field in plan.REF_FIELDS}]
        result = market_result()
        with self.assertRaises(AnalysisContractError): prepare(value, rows)
        with self.assertRaises(AnalysisContractError):
            prepare(value, rows, market_result=result)
        checked = prepare(value, rows, market_result=result,
            verify_market_result=lambda supplied, report: supplied["executionReportId"]
                == "separate-market-execution" and
                report["componentDigests"]["market"] == market["sourceDigest"])
        self.assertEqual(checked["marketResultCandidateDigest"], digest(result))
        self.assertFalse(checked["marketResultSamePrimaryReportClaimed"])
        self.assertFalse(checked["numericClaimsVerified"])
        for change in (lambda item: item["roles"].pop(),
                       lambda item: item.update(numericCitationAllowed=True),
                       lambda item: item.update(reportPublishAuthorized=True)):
            bad = deepcopy(result); change(bad)
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                prepare(value, rows, market_result=bad,
                    verify_market_result=lambda *_: True)
        with self.assertRaises(AnalysisContractError):
            prepare(value, rows, market_result=result,
                verify_market_result=lambda *_: False)

    def test_budget_and_automation_cannot_be_added_by_agent(self):
        value = report(); rows = five_dimensions(value)
        for change in (lambda item: item["budget"].update(capCents=100000),
                       lambda item: item["budget"].update(capCents=False),
                       lambda item: item["budget"].update(capSource="model_estimate"),
                       lambda item: item.update(executionAllowed=True),
                       lambda item: item.update(automaticBidAllowed=True),
                       lambda item: item.update(automaticPriceChangeAllowed=True)):
            bad = deepcopy(rows); change(bad[0])
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                prepare(value, bad)

    def test_malformed_nested_agent_payload_fails_with_contract_error(self):
        value = report(); rows = five_dimensions(value)
        for change in (lambda item: item.update(dimension=[]),
                       lambda item: item.update(ownerRole=[]),
                       lambda item: item.update(contextEvidence=[{"tableKey": []}]),
                       lambda item: item.update(kpi=None),
                       lambda item: item["kpi"].update(metric=[])):
            bad = deepcopy(rows); change(bad[0])
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                prepare(value, bad)

    def test_kpi_observation_stop_and_rollback_are_mandatory(self):
        value = report(); rows = five_dimensions(value)
        for change in (lambda item: item["kpi"].update(baselineValue=0),
                       lambda item: item["kpi"].update(metric="b2b_incremental_sales"),
                       lambda item: item["observation"].update(days=0),
                       lambda item: item["observation"].update(
                           startGate="immediately"),
                       lambda item: item.update(stopConditions=["manual_stop"]),
                       lambda item: item.update(rollback="none")):
            bad = deepcopy(rows); change(bad[0])
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                prepare(value, bad)

    def test_duplicate_id_and_unbounded_actions_rejected(self):
        value = report(); rows = five_dimensions(value)
        bad = deepcopy(rows); bad[1]["id"] = bad[0]["id"]
        with self.assertRaises(AnalysisContractError): prepare(value, bad)
        with self.assertRaises(AnalysisContractError): prepare(value, rows * 7)
