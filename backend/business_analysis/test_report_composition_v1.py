"""Synthetic report manifest reconciliation and closed provenance gates."""
from copy import deepcopy
from unittest import TestCase

from . import (cross_source_category_spu_compare as category_spu,
               cross_source_daily_columns as daily,
               cross_source_sku_window_compare as sku,
               cross_source_window_compare as shop,
               finance_b2b_source_proof as finance_b2b,
               report_composition_v1 as composition)
from .contracts import AnalysisContractError, digest
from .test_cross_source_category_spu_compare import complete_fixture
from .test_cross_source_daily_columns import CONTEXT
from .test_finance_b2b_source_proof import (finance as finance_fixture,
                                             b2b as b2b_fixture)


def fixtures():
    plan, sources, infos, keys, manifest, five, native = complete_fixture()
    materials = {"current": daily.prepare_candidate(plan, sources, infos, CONTEXT,
        keys, "current", manifest,
        {kind: five[kind] for kind in daily._ERP_KINDS}, native)}
    for window in ("previous", "yearAgo"):
        materials[window] = daily.prepare_candidate(plan, sources, infos,
            CONTEXT, keys, window, None, None, {})
    category = category_spu.prepare_candidate(plan, sources, infos, CONTEXT,
        keys, {"current": (manifest, five), "previous": None,
               "yearAgo": None},
        {"current": native["netshopSpu"], "previous": None,
         "yearAgo": None})
    return plan, sources, infos, keys, materials, category


def call(case, *, finance=None, b2b=None, keyword=None, market=None,
         category=True, tamper_proof=None, verifier=None):
    plan, sources, infos, keys, materials, category_result = case
    category_result = category_result if category else None
    store = shop.prepare_candidate(plan, sources, infos, CONTEXT, keys, materials)
    sku_result = sku.prepare_candidate(plan, sources, infos, CONTEXT, keys, materials)
    finance_proof = finance_b2b.build_candidate(finance=finance, b2b=b2b)
    components = {"store": store["comparisonDigest"],
                  "sku": sku_result["comparisonDigest"],
                  "categorySpu": category_result["comparisonDigest"]
                      if category_result else None,
                  "keyword": digest(keyword) if keyword else None,
                  "financeB2b": finance_proof["candidateDigest"],
                  "market": market["resultDigest"] if market else None}
    binding = {"schemaVersion": composition.PROOF_SCHEMA,
               "reportId": plan["reportId"], "planDigest": plan["planDigest"],
               "evidenceRunId": CONTEXT["evidenceRunId"],
               "sealedDigest": CONTEXT["sealedDigest"],
               "materialDigests": {window: materials[window]["materialDigest"]
                   for window in composition.WINDOWS},
               "componentDigests": components,
               "externalContextSameReportClaimed": False}
    proof = {**binding, "bindingDigest": digest(binding)}
    if tamper_proof:
        tamper_proof(proof)
    return composition.compose_candidate(plan, sources, infos, CONTEXT,
        keys, materials, category_spu_result=category_result,
        keyword_headers=keyword, finance=finance, b2b=b2b,
        market_preview=market, owning_proof=proof,
        verify_owning_proof=verifier if verifier is not None else
            lambda supplied, expected: supplied["bindingDigest"] == digest(expected))


class ReportCompositionTests(TestCase):
    def test_manifest_keeps_all_dimensions_coverage_and_unassigned_separate(self):
        result = call(fixtures())
        self.assertEqual(result["schemaVersion"], composition.SCHEMA)
        self.assertEqual([row["tableKey"] for row in result["tableManifest"]],
                         list(composition.TABLES))
        self.assertEqual(result["tableManifest"][2]["tableKey"], "erp_unassigned")
        self.assertEqual(result["tableManifest"][7]["status"], "not_supplied")
        self.assertEqual(len(result["coverageRows"]), 12)
        self.assertTrue(any(row["coverage"] is None for row in
                            result["coverageRows"]))
        self.assertEqual(result["financeB2bProof"]["rows"][0]["sourceStatus"],
                         "not_supplied")
        self.assertFalse(result["historicalErpIdentityVerified"])
        self.assertFalse(result["crossDomainAmountsAdded"])
        self.assertFalse(result["authorityVerified"])
        self.assertFalse(result["agentReadPersisted"])
        self.assertEqual([item["window"] for item in result["actions30Days"]],
                         ["D01-D07", "D01-D07", "D08-D14", "D08-D14", "D15-D30"])
        self.assertTrue(all(item["executionAllowed"] is False
                            for item in result["actions30Days"]))
        self.assertEqual(result["compositionDigest"], digest({key: value
            for key, value in result.items() if key != "compositionDigest"}))

    def test_proof_absent_wrong_report_and_rejected_owning_recheck_close(self):
        case = fixtures()
        for change in (lambda proof: proof.update(reportId="other-report"),
                       lambda proof: proof.update(bindingDigest="0"*64),
                       lambda proof: proof["componentDigests"].update(
                           store="0"*64)):
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                call(case, tamper_proof=change)
        with self.assertRaises(AnalysisContractError):
            call(case, verifier=lambda *_: False)
        with self.assertRaises(AnalysisContractError):
            composition.compose_candidate(case[0], case[1], case[2],
                CONTEXT, case[3], case[4],
                category_spu_result=case[5])

    def test_tampered_day_and_category_proof_reject_even_if_local_digest_rehashed(self):
        case = fixtures()
        changed = deepcopy(case)
        changed[4]["current"]["shopDayRows"][0]["erpSales"]["netSalesCents"]["value"] += 1
        changed[4]["current"]["materialDigest"] = digest({key: value
            for key, value in changed[4]["current"].items()
            if key != "materialDigest"})
        with self.assertRaises(AnalysisContractError):
            call(changed)
        changed = deepcopy(case)
        erp = next(row for row in changed[5]["rows"] if
                   row["dimension"] == "erpCategory")
        erp["comparisons"]["previous"] = {"status": "comparable",
            "difference": 2, "growthRateBps": 100}
        changed[5]["comparisonDigest"] = digest({key: value
            for key, value in changed[5].items() if key != "comparisonDigest"})
        with self.assertRaises(AnalysisContractError):
            call(changed)
        changed = deepcopy(case)
        changed[5]["sourceMaterialProofs"]["current"]["netshopSpuEvidenceDigest"] = "0"*64
        changed[5]["comparisonDigest"] = digest({key: value
            for key, value in changed[5].items() if key != "comparisonDigest"})
        with self.assertRaises(AnalysisContractError):
            call(changed)
        changed = deepcopy(case)
        changed[5]["sourceMaterialProofs"]["current"]["erpRollupManifestDigest"] = "0"*64
        changed[5]["comparisonDigest"] = digest({key: value
            for key, value in changed[5].items() if key != "comparisonDigest"})
        with self.assertRaises(AnalysisContractError):
            call(changed)

    def test_natural_month_finance_is_context_and_does_not_become_daily_profit(self):
        result = call(fixtures(), finance=finance_fixture())
        finance_row = next(item for item in result["tableManifest"] if
                           item["tableKey"] == "finance_month")
        self.assertEqual(finance_row["status"], "context_only")
        self.assertEqual([row["periodRole"] for row in
            result["financeB2bProof"]["rows"] if row["domain"] == "finance"],
            ["2026-08", "2026-09"])
        self.assertFalse(result["financeB2bProof"]["financeDailyProrationAllowed"])
        self.assertFalse(result["externalContextSameReportClaimed"])
        self.assertIsNone(result["financeB2bProof"]["b2bIncrementalSalesCents"])

    def test_b2b_from_other_shop_or_period_is_rejected(self):
        case = fixtures()
        with self.assertRaises(AnalysisContractError):
            call(case, b2b=b2b_fixture())

    def test_keyword_header_and_market_sample_never_grant_execution_or_sales(self):
        case = fixtures()
        plan = case[0]
        keyword = {window: None for window in composition.WINDOWS}
        keyword["current"] = {"schemaVersion":
            "business-promotion-keyword-sku-table-v1", "authorityVerified": False,
            "sourceWindow": "current",
            "source": {"key": plan["sourceKeys"]["promotion"]["current"]},
            "periods": plan["periods"], "total": 2,
            "tableBindingDigest": "a"*64}
        preview = {"schemaVersion": "business-market-v2-fifth-read-preview-v1",
                   "serverFullMarketMaterialVerified": True,
                   "persistedRead": False, "registeredTool": False,
                   "authorityVerified": False, "mode": "summary",
                   "marketManifestDigest": "b"*64,
                   "sourceReportId": "external-market-report"}
        preview["resultDigest"] = digest(preview)
        result = call(case, keyword=keyword, market=preview)
        self.assertEqual(next(row for row in result["tableManifest"] if
            row["tableKey"] == "keyword_sku")["rowCount"], 2)
        self.assertEqual(next(row for row in result["tableManifest"] if
            row["tableKey"] == "market_sample")["status"], "sample_only")
        self.assertFalse(result["externalContextSameReportClaimed"])
        self.assertFalse(result["agentReadPersisted"])
        bad = deepcopy(keyword); bad["current"]["source"]["key"] = "other"
        with self.assertRaises(AnalysisContractError): call(case, keyword=bad)
        bad_market = deepcopy(preview); bad_market["persistedRead"] = True
        bad_market["resultDigest"] = digest({key: value for key, value
            in bad_market.items() if key != "resultDigest"})
        with self.assertRaises(AnalysisContractError): call(case, market=bad_market)
