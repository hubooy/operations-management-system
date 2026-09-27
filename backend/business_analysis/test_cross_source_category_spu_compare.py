"""Pure category/SPU proof and no-historical-ownership comparison tests."""
from copy import deepcopy
import hashlib
import json
from unittest import TestCase

from . import (cross_source_category_spu_compare as candidate,
    cross_source_kpi_plan as planning, erp_fact_assignment, erp_fact_rollups)
from .contracts import AnalysisContractError, comparison_periods, digest
from .test_cross_source_daily_columns import (CONTEXT, fixture, native_page,
    product)
from .test_erp_fact_assignment import fixture as erp_fixture


def complete_fixture():
    plan, sources, infos, keys, manifest, three, native = fixture()
    sales, sales_proof, master, master_proof = erp_fixture()
    with erp_fact_assignment.assign_facts(sources, plan["mappingPlan"],
            plan["currentMappingPairKey"], sales, master,
            sales_proof, master_proof) as ledger:
        with erp_fact_rollups.prepare(ledger) as prepared:
            five = {kind: list(prepared.ndjson_pages(kind))
                for kind in erp_fact_rollups.KINDS}
            assert prepared.manifest["tables"] == manifest["tables"]
            assert prepared.manifest["manifestDigest"] == manifest["rollupManifestDigest"]
    for kind in three:
        assert three[kind] == five[kind]
    manifest["rowCount"] = sum(item["rowCount"] for item in manifest["tables"])
    manifest["ndjsonBytes"] = sum(item["ndjsonBytes"] for item in manifest["tables"])
    manifest["manifestDigest"] = digest({k: v for k, v in manifest.items()
        if k != "manifestDigest"})
    return plan, sources, infos, keys, manifest, five, native


def run_case(case):
    plan, sources, infos, keys, manifest, five, native = case
    return candidate.prepare_candidate(plan, sources, infos, CONTEXT, keys,
        {"current": (manifest, five), "previous": None, "yearAgo": None},
        {"current": native["netshopSpu"], "previous": None, "yearAgo": None})


class CategorySpuCandidateTests(TestCase):
    def test_current_master_erp_never_claims_historical_growth_and_native_is_separate(self):
        result = run_case(complete_fixture())
        self.assertEqual(result["schemaVersion"], candidate.SCHEMA)
        category = next(row for row in result["rows"] if
            row["dimension"] == "erpCategory" and row["metric"] == "netSalesCents")
        self.assertEqual(category["windows"]["current"]["value"], 100)
        self.assertEqual(category["windows"]["current"]["status"],
            "date_not_covered")
        self.assertEqual(category["comparisons"]["previous"]["status"],
            "historical_identity_unverified")
        self.assertIsNone(category["comparisons"]["previous"]["growthRateBps"])
        native = next(row for row in result["rows"] if
            row["dimension"] == "netshopSpuNative" and row["metric"] == "paymentCents")
        self.assertEqual(native["windows"]["current"]["value"], 70)
        self.assertEqual(native["windows"]["previous"]["status"], "missing_source")
        self.assertFalse(result["historicalErpOwnershipVerified"])
        self.assertFalse(result["crossDomainAmountsAdded"])
        self.assertEqual(result["comparisonDigest"], digest({k: v for k, v in
            result.items() if k != "comparisonDigest"}))

    def test_extra_stream_hash_and_rehashed_daily_conservation_reject(self):
        case = complete_fixture()
        bad = deepcopy(case)
        raw = bad[5]["category_day"]
        bad[5]["category_day"] = [raw[0] + b"\n"]
        with self.assertRaises(AnalysisContractError):
            run_case(bad)

        bad = deepcopy(case)
        row = json.loads(b"".join(bad[5]["category_day"]).splitlines()[0])
        row["metrics"]["netSalesCents"] += 1
        body = {key: value for key, value in row.items() if key != "id"}
        identity = {key: row[key] for key in ("period", "platform", "shopName",
            "date", "category", "spuId", "skuId", "status")}
        row["id"] = digest([bad[4]["assignmentSummaryDigest"], "category_day",
            json.dumps(identity, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")), body])
        blob = (json.dumps(row, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"))+"\n").encode("utf-8")
        bad[5]["category_day"] = [blob]
        spec = next(item for item in bad[4]["tables"] if item["kind"] == "category_day")
        spec.update(ndjsonBytes=len(blob), ndjsonSha256=hashlib.sha256(blob).hexdigest())
        bad[4]["ndjsonBytes"] = sum(item["ndjsonBytes"] for item in bad[4]["tables"])
        bad[4]["manifestDigest"] = digest({k: v for k, v in bad[4].items()
            if k != "manifestDigest"})
        with self.assertRaisesRegex(AnalysisContractError, "守恒"):
            run_case(bad)

    def test_rehashed_category_identity_shift_rejects_even_with_equal_totals(self):
        bad = deepcopy(complete_fixture())
        row = json.loads(b"".join(bad[5]["category_day"]).splitlines()[0])
        row["category"] = "伪造类目"
        identity = {key: row[key] for key in ("period", "platform", "shopName",
            "date", "category", "spuId", "skuId", "status")}
        body = {key: value for key, value in row.items() if key != "id"}
        row["id"] = digest([bad[4]["assignmentSummaryDigest"], "category_day",
            json.dumps(identity, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")), body])
        blob = (json.dumps(row, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"))+"\n").encode("utf-8")
        bad[5]["category_day"] = [blob]
        spec = next(item for item in bad[4]["tables"] if item["kind"] == "category_day")
        spec.update(ndjsonBytes=len(blob), ndjsonSha256=hashlib.sha256(blob).hexdigest())
        bad[4]["ndjsonBytes"] = sum(item["ndjsonBytes"] for item in bad[4]["tables"])
        bad[4]["manifestDigest"] = digest({k: v for k, v in bad[4].items()
            if k != "manifestDigest"})
        with self.assertRaisesRegex(AnalysisContractError, "守恒"):
            run_case(bad)

    def test_wrong_report_and_missing_source_material_reject(self):
        case = complete_fixture()
        wrong = deepcopy(case)
        wrong[4]["reportBinding"]["reportId"] = "other"
        wrong[4]["manifestDigest"] = digest({k: v for k, v in wrong[4].items()
            if k != "manifestDigest"})
        with self.assertRaises(AnalysisContractError):
            run_case(wrong)
        wrong = deepcopy(case)
        wrong[5]["spu_day"] = []
        with self.assertRaises(AnalysisContractError):
            run_case(wrong)

    def test_native_spu_exact_identity_complete_days_positive_baseline(self):
        case = list(complete_fixture())
        plan, sources, infos, keys, manifest, five, native = case
        current = next(source for source in sources if source["key"] == "netshopSpu")
        current_rows = [product(11, "2026-08-16", None, "P1", 70, 4),
            product(14, "2026-08-17", None, "P1", 30, 2)]
        current_rows[1]["metrics"]["productDayVisitors"] = None
        native["netshopSpu"], infos["netshopSpu"] = native_page(current, current_rows)
        previous = {"key": "native-prev", "domain": "netshop",
            "query": {**current["query"], "window": "previous"}}
        sources.append(previous)
        dates = comparison_periods("2026-08-16", "2026-08-17")["previous"]
        previous_rows = [product(21, dates["startDate"], None, "P1", 20, 2),
            product(22, dates["endDate"], None, "P1", 20, 2)]
        native["native-prev"], infos["native-prev"] = native_page(previous, previous_rows)
        keys["netshopSpu"]["previous"] = "native-prev"
        new_plan = planning.prepare_candidate(sources, infos, CONTEXT, keys)
        self.assertEqual(new_plan["mappingPlanDigest"], plan["mappingPlanDigest"])
        result = candidate.prepare_candidate(new_plan, sources, infos, CONTEXT,
            keys, {"current": (manifest, five), "previous": None, "yearAgo": None},
            {"current": native["netshopSpu"],
             "previous": native["native-prev"], "yearAgo": None})
        row = next(row for row in result["rows"] if
            row["dimension"] == "netshopSpuNative" and row["metric"] == "paymentCents")
        self.assertEqual(row["windows"]["current"]["value"], 100)
        self.assertEqual(row["windows"]["previous"]["value"], 40)
        self.assertEqual(row["comparisons"]["previous"],
            {"status": "comparable", "difference": 60, "growthRateBps": 15000})
        visitors = next(row for row in result["rows"] if
            row["dimension"] == "netshopSpuNative" and
            row["metric"] == "productDayVisitors")
        self.assertEqual(visitors["windows"]["current"]["status"],
            "partial_metric_coverage")
        self.assertIsNone(visitors["comparisons"]["previous"]["growthRateBps"])
        erp = next(row for row in result["rows"] if
            row["dimension"] == "erpSpu" and row["metric"] == "netSalesCents")
        self.assertIsNone(erp["comparisons"]["previous"]["growthRateBps"])
