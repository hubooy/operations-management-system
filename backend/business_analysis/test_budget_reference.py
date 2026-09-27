from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from . import budget, budget_reference as ref
from .contracts import AnalysisContractError, canonical, digest
from .test_budget import fixture


def binding_for(plan, **changes):
    values = dict(report_id="report-fixed", owner_email="fixed@example.invalid", scope=None,
        evidence_run_id="evidence-fixed", evidence_version=3, evidence_plan_digest="a"*64,
        catalog_digest="b"*64, sealed_digest="c"*64, analysis_request=None)
    return ref.make_binding(plan, **{**values, **changes})


def result_for(plan, bases):
    return {**budget.calculate(plan, bases), "evidenceRunId": "evidence-fixed", "evidenceVersion": 3, "evidencePlanDigest": "a"*64}


class BudgetReferenceTests(TestCase):
    def test_binding_is_exact_and_missing_analysis_is_null(self):
        plan, _ = fixture()
        binding = binding_for(plan)
        self.assertEqual(len(binding), 13)
        self.assertEqual(set(binding), ref.BINDING_FIELDS)
        self.assertIsNone(binding["analysisRequestDigest"])
        self.assertEqual(binding["scopeDigest"], digest(None))
        reference = ref.make_reference("budget-one", binding)
        result = ref.validate_record(plan, binding, reference, expected_binding=binding)
        self.assertEqual(result["plan"], plan)
        result["plan"]["targets"].clear()
        self.assertEqual(len(plan["targets"]), 2)
        metadata = {"schemaVersion": "business-analysis-request-v1", "question": "固定预算", "requestedDimensions": ["sku"], "requestedWindows": ["current"]}
        self.assertEqual(binding_for(plan, analysis_request=metadata)["analysisRequestDigest"], digest(metadata))

    def test_each_binding_change_with_recomputed_digest_still_rejected(self):
        plan, _ = fixture(); trusted = binding_for(plan)
        for field, value in {"reportId": "other-report", "ownerEmail": "other@example.invalid", "evidenceRunId": "other-evidence",
                "evidenceVersion": 4, "evidencePlanDigest": "d"*64, "catalogDigest": "d"*64,
                "sealedDigest": "d"*64, "analysisRequestDigest": "d"*64, "planDigest": "d"*64}.items():
            with self.subTest(field=field):
                altered = {**trusted, field: value}
                with self.assertRaises(AnalysisContractError):
                    ref.validate_record(plan, altered, ref.make_reference("budget-one", altered), expected_binding=trusted)
        for field, value in (("calculatorVersion", "unknown"), ("capacityProfile", "unknown"), ("schemaVersion", "unknown"),
                             ("evidenceVersion", True), ("evidenceVersion", 3.0), ("scopeDigest", "0"*64)):
            with self.assertRaises(AnalysisContractError): ref.validate_binding({**trusted, field: value})

    def test_parameters_and_reference_are_not_self_certifying(self):
        plan, _ = fixture(); binding = binding_for(plan); reference = ref.make_reference("budget-one", binding)
        changed = deepcopy(plan); changed["targets"][0]["weight"] = 3
        with self.assertRaises(AnalysisContractError): ref.validate_record(changed, binding, reference, expected_binding=binding)
        for value in ({**reference, "bindingDigest": "0"*64}, {**reference, "schemaVersion": "unknown"}, {**reference, "extra": True}):
            with self.assertRaises(AnalysisContractError): ref.validate_record(plan, binding, value, expected_binding=binding)

    def test_plan_larger_than_snapshot_with_exact_48000_boundary(self):
        plan, _ = fixture(); original = plan["targets"][0]
        plan["targets"] = [{**original, "rowIndex": i, "rowId": f"{i+1:064x}", "sourceKey": "s"*160, "ownerRole": "x"} for i in range(100)]
        # Fill valid ownerRole scalar bytes up to the exact contract limit.
        for target in plan["targets"]:
            missing = ref.MAX_PLAN_BYTES-len(canonical(plan).encode())
            if missing <= 0: break
            target["ownerRole"] += "x"*min(missing, 79)
        for target in plan["targets"]:
            missing = ref.MAX_PLAN_BYTES-len(canonical(plan).encode())
            if missing <= 0: break
            extra = min(missing, 2*len(target["ownerRole"]))
            count, odd = divmod(extra, 2)
            target["ownerRole"] = "中"*count + ("é" if odd else "") + target["ownerRole"][count+odd:]
        self.assertEqual(len(canonical(plan).encode()), 48000)
        self.assertEqual(ref.normalize_plan(plan), plan)
        binding = binding_for(plan)
        self.assertLess(len(canonical(ref.make_reference("budget-one", binding)).encode()), 1024)
        target = next(t for t in plan["targets"] if "x" in t["ownerRole"])
        target["ownerRole"] = target["ownerRole"].replace("x", "é", 1)
        with self.assertRaises(AnalysisContractError): ref.normalize_plan(plan)

    def test_nested_cyclic_types_and_binding_byte_boundary_rejected(self):
        plan, _ = fixture()
        for bad in (True, 0.0, [], {"nested": []}):
            value = deepcopy(plan); value["targets"][0]["rowIndex"] = bad
            with self.assertRaises(AnalysisContractError): ref.normalize_plan(value)
        cyclic = {}; cyclic["self"] = cyclic
        with self.assertRaises(AnalysisContractError): ref.normalize_plan(cyclic)
        with self.assertRaises(AnalysisContractError): binding_for(plan, scope={"channels": []})
        binding = binding_for(plan); size = len(canonical(binding).encode())
        with patch.object(ref, "MAX_BINDING_BYTES", size): self.assertEqual(ref.validate_binding(binding), binding)
        with patch.object(ref, "MAX_BINDING_BYTES", size-1), self.assertRaises(AnalysisContractError): ref.validate_binding(binding)

    def test_pages_are_complete_prefixes_and_independently_reproducible(self):
        plan, bases = fixture(); result = result_for(plan, bases); binding = binding_for(plan)
        reference = ref.make_reference("budget-one", binding)
        first = ref.page(result, binding, budget_ref=reference, report_id="report-fixed", limit=1)
        self.assertEqual(first["pagination"]["nextOffset"], 1)
        tail = ref.page(result, binding, budget_ref=reference, report_id="report-fixed", offset=1)
        self.assertIsNone(tail["pagination"]["nextOffset"])
        self.assertEqual([first["rows"][0]["rowId"], tail["rows"][0]["rowId"]], [b["rowId"] for b in bases])
        self.assertEqual(first["pageDigest"], digest({k: v for k, v in first.items() if k != "pageDigest"}))
        first["rows"][0]["ownerRole"] = "mutated"
        self.assertNotEqual(first, ref.page(result, binding, budget_ref=reference, report_id="report-fixed", limit=1))

    def test_byte_shrink_never_truncates_a_row_and_single_row_rejects(self):
        plan, bases = fixture()
        for b in bases: b["entity"]["title"] = "中"*4000
        result = result_for(plan, bases); binding = binding_for(plan); reference = ref.make_reference("budget-one", binding)
        page = ref.page(result, binding, budget_ref=reference, report_id="report-fixed", limit=20)
        self.assertEqual(len(page["rows"]), 1)
        self.assertEqual(page["rows"][0]["entity"]["title"], "中"*4000)
        self.assertEqual(page["pagination"]["nextOffset"], 1)
        self.assertLessEqual(len(canonical(page).encode()), 38000)
        bases[0]["entity"]["title"] = "中"*20000
        with self.assertRaises(AnalysisContractError): ref.page(result_for(plan,bases), binding, budget_ref=reference, report_id="report-fixed")

    def test_page_invalid_offsets_and_mutated_arithmetic_rejected(self):
        plan, bases = fixture(); result = result_for(plan,bases); binding = binding_for(plan); reference = ref.make_reference("budget-one",binding)
        for offset in (True, 0.0, -1, 2):
            with self.assertRaises(AnalysisContractError): ref.page(result,binding,budget_ref=reference,report_id="report-fixed",offset=offset)
        for field in ("allocatedCents", "targetCount"):
            changed = deepcopy(result); changed["allocation"][field] += 1
            with self.assertRaises(AnalysisContractError): ref.page(changed,binding,budget_ref=reference,report_id="report-fixed")
        with self.assertRaises(AnalysisContractError): ref.page(result,binding,budget_ref=reference,report_id="other")

    def test_hundred_targets_five_scenarios_wide_result_full_six_pages(self):
        plan, bases = fixture()
        target, baseline = plan["targets"][0], bases[0]
        plan["targets"] = [{**target,"rowIndex":i,"rowId":f"{i:064x}","weight":1,"ownerRole":"中"*40} for i in range(100)]
        plan["scenarios"] = [{**plan["scenarios"][0],"name":f"情景{i}"} for i in range(5)]
        bases = [{**deepcopy(baseline),"rowId":target["rowId"],
            "entity":{"platform":"京东","shopName":"合成店","skuId":f"S{i}"},
            "source":"jd_promotion","metricSemantics":{"spendCents":"广告消耗"}}
            for i,target in enumerate(plan["targets"])]
        self.assertEqual(len(canonical(plan).encode()),34587)
        self.assertGreater(len(canonical(plan).encode()),32768)
        result = result_for(plan,bases)
        self.assertEqual(len(canonical(result).encode()),662550)
        binding = binding_for(plan); reference = ref.make_reference("budget-wide",binding)
        offset, ids, sizes = 0, [], []
        while offset is not None:
            page = ref.page(result,binding,budget_ref=reference,report_id="report-fixed",offset=offset,limit=20)
            sizes.append(len(canonical(page).encode()))
            self.assertLessEqual(sizes[-1],38000)
            self.assertEqual([row["rowIndex"] for row in page["rows"]],list(range(offset,offset+len(page["rows"]))))
            self.assertTrue(all(len(row["outcomes"]) == 5 for row in page["rows"]))
            ids.extend(row["rowId"] for row in page["rows"])
            offset = page["pagination"]["nextOffset"]
        self.assertEqual(ids,[target["rowId"] for target in plan["targets"]])
        self.assertEqual(sizes,[36601,36632,36632,36632,36632,32788])
