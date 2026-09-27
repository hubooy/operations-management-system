from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from . import evidence_v2, planning, planning_v2
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest
from .test_planning import fixture


def complete_request():
    body = fixture()
    body["schemaVersion"] = planning_v2.REQUEST_SCHEMA
    body["shops"][0].update(datasets=["promotion", "sku", "spu", "b2b", "master"], salesChannels=["京东渠道"])
    body["markets"] = [{"platform": "京东", "category": "测试类目", "scope": "POP", "rankingDimension": "SKU", "priceBandFilter": "全部"}]
    return body


def sources_request(count):
    body = complete_request()
    body["markets"] = []
    body["shops"] = [{"platform": "京东", "shop": "合成店"+str(i), "datasets": ["promotion", "sku", "spu", "b2b"], "salesChannels": []} for i in range(4)]
    if count == 49:
        body["shops"][0]["datasets"].append("master")
    return body


class PlanningV2Tests(TestCase):
    def test_legacy_default_whole_response_golden_is_unchanged(self):
        self.assertEqual(digest(planning.preview(fixture())), "c3d191cec425733ae89a8c4f04275d32e0b5361d9f787723fbeb521e801698ea")
        body = complete_request(); body.pop("schemaVersion")
        legacy = planning.preview(body)
        self.assertFalse(legacy["canCollect"])
        self.assertEqual(legacy["capacity"]["maxSources"], 12)
        self.assertNotIn("plan", legacy)
        self.assertNotIn("schemaVersion", legacy["evidenceRequest"])

    def test_complete_nineteen_sources_have_real_small_plan_and_light_reference(self):
        body = complete_request(); original = deepcopy(body)
        result = planning_v2.preview(body)
        self.assertEqual(body, original)
        self.assertTrue(result["canCollect"])
        self.assertEqual(result["schemaVersion"], planning_v2.PREVIEW_SCHEMA)
        self.assertEqual(result["request"]["schemaVersion"], planning_v2.REQUEST_SCHEMA)
        self.assertEqual(len(result["sources"]), 19)
        self.assertEqual(len(result["coverage"]), 19)
        expected = evidence_v2.build_catalog(result["sources"], analysis_request=result["evidenceRequest"]["analysisRequest"])
        self.assertEqual(result["plan"], expected["header"])
        self.assertEqual(result["planDigest"], expected["planDigest"])
        self.assertEqual(result["catalogDigest"], expected["header"]["catalogDigest"])
        self.assertNotIn("sources", result["plan"])
        self.assertNotIn("sources", result["workflowInputEstimate"])
        self.assertEqual(result["capacity"]["planBytes"], len(canonical(expected["header"]).encode()))
        self.assertEqual(result["capacity"]["workflowBytes"], len(canonical(result["workflowInputEstimate"]).encode()))
        sizes = [len(canonical(source["query"]).encode()) for source in result["sources"]]
        self.assertEqual(result["capacity"]["queryBytes"], max(sizes))
        self.assertEqual(result["capacity"]["directoryQueryBytes"], sum(sizes))
        self.assertEqual(result["capacity"]["factBytes"], 64*1024*1024)
        self.assertEqual(result["capacity"]["factPages"], 2000)
        self.assertEqual(result["workflowEstimateAssumptions"]["evidenceVersion"], MAX_SAFE_INTEGER)
        self.assertFalse(result["workflowEstimateAssumptions"]["modelAdmissionChecked"])
        self.assertTrue(all(row["availability"] == "not_collected" and row["status"] == "planned" for row in result["coverage"]))
        self.assertNotIn(planning_v2.LEGACY_ESTIMATE_NOTE, result["limitations"])
        self.assertEqual(result["evidenceRequest"]["schemaVersion"], "business-evidence-v2")
        self.assertTrue(result["evidenceRequest"]["autoCollect"])

    def test_forty_eight_supported_forty_nine_preserved_and_refused(self):
        accepted = planning_v2.preview(sources_request(48))
        self.assertTrue(accepted["canCollect"])
        self.assertEqual(len(accepted["sources"]), 48)
        rejected = planning_v2.preview(sources_request(49))
        self.assertFalse(rejected["canCollect"])
        self.assertEqual(len(rejected["sources"]), 49)
        self.assertEqual(len(rejected["coverage"]), 49)
        self.assertEqual(len(rejected["evidenceRequest"]["sources"]), 49)
        self.assertTrue(any("来源数量49超过上限48" in note for note in rejected["limitations"]))
        for key in ("plan", "planDigest", "catalogDigest", "workflowInputEstimate"):
            self.assertIsNone(rejected[key])
        self.assertIsNone(rejected["capacity"]["planBytes"])
        self.assertIsNone(rejected["capacity"]["workflowBytes"])

    def test_maximum_form_scope_kept_without_internal_proposal_truncation(self):
        body = sources_request(48)
        for shop in body["shops"]:
            shop["datasets"].append("master")
            shop["salesChannels"] = ["渠道"+str(i) for i in range(10)]
        body["markets"] = [{**complete_request()["markets"][0], "category": "类目"+str(i)} for i in range(7)]
        result = planning_v2.preview(body)
        self.assertEqual(len(result["sources"]), planning_v2.MAX_PROPOSAL_SOURCES)
        self.assertEqual(len(result["coverage"]), 193)
        self.assertFalse(result["canCollect"])
        self.assertTrue(any("来源数量193超过上限48" in note for note in result["limitations"]))

    def test_unsupported_scope_does_not_receive_a_plan_for_remaining_subset(self):
        body = complete_request(); body["shops"][0]["platform"] = "天猫"
        result = planning_v2.preview(body)
        self.assertFalse(result["canCollect"])
        self.assertEqual(len(result["coverage"]), 19)
        self.assertEqual(sum(row["status"] == "unsupported" for row in result["coverage"]), 6)
        self.assertIsNone(result["plan"])
        # Owning-reader rejection survives even if the pure catalog supports it.
        result = planning_v2.preview(complete_request(), market_validator=lambda query: "实际reader不支持该榜单")
        self.assertEqual(sum(row["reason"] == "实际reader不支持该榜单" for row in result["coverage"]), 3)
        self.assertFalse(result["canCollect"])
        self.assertIsNone(result["plan"])
        result = planning_v2.preview(complete_request(), netshop_sources={})
        self.assertFalse(result["canCollect"])
        self.assertEqual(len(result["coverage"]), 19)

    def test_no_sources_unknown_schema_and_extra_fields_rejected_or_blocked(self):
        body = complete_request(); body["shops"] = []; body["markets"] = []
        result = planning_v2.preview(body)
        self.assertFalse(result["canCollect"])
        self.assertIsNone(result["planDigest"])
        self.assertEqual(result["capacity"]["queryBytes"], 0)
        for schema in (None, "business-plan-request-v1", "business-evidence-v2", True, 2):
            with self.assertRaises(AnalysisContractError):
                planning_v2.preview({**complete_request(), "schemaVersion": schema})
        for extra in ({"modelId": "x"}, {"expectedPrincipalKey": "x"}, {"maxSources": 100}, {"budgetPlan": {}}):
            with self.assertRaises(AnalysisContractError):
                planning_v2.preview({**complete_request(), **extra})

    def test_long_chinese_question_exact_identities_and_requested_windows(self):
        body = complete_request()
        body["question"] = "中" * 1000
        body["shops"][0]["shop"] = "精确店" * 33
        body["shops"][0]["salesChannels"] = ["sku"]
        body["markets"][0].update(category="中"*200, scope="中"*200, priceBandFilter="中"*200)
        result = planning_v2.preview(body)
        self.assertTrue(result["canCollect"])
        self.assertLess(result["capacity"]["planBytes"], 16000)
        self.assertLess(result["capacity"]["workflowBytes"], 8000)
        self.assertTrue(any(s["query"].get("channel") == "sku" for s in result["sources"]))
        self.assertEqual(result["evidenceRequest"]["analysisRequest"]["question"], body["question"])
        for s in result["sources"]:
            self.assertEqual(s["query"]["startDate"], body["startDate"])
            self.assertEqual(s["query"]["endDate"], body["endDate"])
            if s["query"].get("dataset") == "master":
                self.assertEqual(s["query"]["window"], "current")
        smaller = evidence_v2.workflow_reference(result["sources"], run_id=planning.EVIDENCE_ID_PLACEHOLDER, evidence_version=20,
            sealed_digest="a"*64, question=body["question"], analysis_request=result["evidenceRequest"]["analysisRequest"])
        self.assertLessEqual(len(canonical(smaller).encode()), result["capacity"]["workflowBytes"])

    def test_failed_catalog_or_reference_capacity_is_null_not_zero_or_partial(self):
        for constant in ("MAX_HEADER_BYTES", "MAX_WORKFLOW_BYTES", "MAX_QUERY_BYTES", "MAX_DIRECTORY_QUERY_BYTES"):
            with self.subTest(constant=constant), patch.object(evidence_v2, constant, 1):
                result = planning_v2.preview(complete_request())
                self.assertFalse(result["canCollect"])
                self.assertEqual(len(result["sources"]), 19)
                self.assertIsNone(result["plan"])
                self.assertIsNone(result["capacity"]["planBytes"])
                self.assertIsNone(result["capacity"]["workflowBytes"])

    def test_reply_containers_do_not_alias_evidence_submission(self):
        result = planning_v2.preview(complete_request())
        key = result["sources"][0]["key"]
        result["sources"][0]["key"] = "changed"
        self.assertEqual(result["evidenceRequest"]["sources"][0]["key"], key)
        result["request"]["windows"].clear()
        self.assertEqual(result["evidenceRequest"]["analysisRequest"]["requestedWindows"], ["current", "previous", "yearAgo"])
