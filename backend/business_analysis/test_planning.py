from copy import deepcopy
from unittest import TestCase

from .contracts import AnalysisContractError, canonical, comparison_periods, digest
from .planning import EVIDENCE_ID_PLACEHOLDER, preview, validate_analysis_request, workflow_input_bytes


def fixture():
    return {"question": "诊断推广并给出调整计划", "startDate": "2024-02-01", "endDate": "2024-02-29",
            "shops": [{"platform": "京东", "shop": "测试店", "datasets": ["promotion", "master"], "salesChannels": []}],
            "windows": ["current", "previous", "yearAgo"], "markets": []}


class PlanningTests(TestCase):
    def test_complete_jd_request_is_not_silently_reduced_to_twelve(self):
        body = fixture()
        body["shops"][0].update(datasets=["promotion", "sku", "spu", "b2b", "master"], salesChannels=["京东渠道"])
        body["markets"] = [{"platform": "京东", "category": "测试类目", "scope": "POP", "rankingDimension": "SKU", "priceBandFilter": "全部"}]
        result = preview(body)
        self.assertEqual(result["capacity"]["sourceCount"], 19)
        self.assertFalse(result["canCollect"])
        self.assertEqual(len(result["sources"]), 19)
        self.assertTrue(any("来源数量19超过上限12" in s for s in result["limitations"]))
        self.assertTrue(preview(body, max_sources=30, max_plan_bytes=30000, max_workflow_bytes=30000)["canCollect"])

    def test_master_only_current_dates_are_never_shifted_twice(self):
        result = preview(fixture())
        self.assertTrue(result["canCollect"])
        self.assertEqual(len(result["sources"]), 4)
        masters = [s for s in result["sources"] if s["query"].get("dataset") == "master"]
        self.assertEqual([s["query"]["window"] for s in masters], ["current"])
        for source in result["sources"]:
            self.assertEqual(source["query"]["startDate"], "2024-02-01")
            self.assertEqual(source["query"]["endDate"], "2024-02-29")
        self.assertEqual(comparison_periods("2024-02-01", "2024-02-29")["yearAgo"]["endDate"], "2023-02-28")
        self.assertTrue(all(c["availability"] == "not_collected" for c in result["coverage"]))
        self.assertTrue(any("不证明历史商品映射" in s for s in result["limitations"]))

    def test_order_is_stable_exact_identity_is_separate_and_input_untouched(self):
        body = fixture()
        body["shops"].append({"platform": "天猫", "shop": "测试店", "datasets": ["spu", "promotion"], "salesChannels": ["B", "A"]})
        original = deepcopy(body)
        expected = preview(body)
        body["shops"].reverse()
        body["windows"].reverse()
        for shop in body["shops"]:
            shop["datasets"].reverse()
            shop["salesChannels"].reverse()
        self.assertEqual(expected, preview(body))
        self.assertEqual(original["shops"][0]["datasets"], ["promotion", "master"])
        self.assertEqual(len({s["key"] for s in expected["sources"]}), len(expected["sources"]))
        for source in expected["sources"]:
            self.assertEqual(source["key"], "source-" + digest({"domain": source["domain"], "query": source["query"]}))

    def test_unsupported_tmall_is_explicit_and_blocks_partial_collection(self):
        body = fixture()
        body["shops"][0].update(platform="天猫", datasets=["promotion", "sku", "b2b"])
        result = preview(body)
        self.assertFalse(result["canCollect"])
        self.assertEqual(len(result["sources"]), 3)
        rejected = [c for c in result["coverage"] if c["status"] == "unsupported"]
        self.assertEqual(len(rejected), 6)
        self.assertTrue(all("sourceKey" not in c for c in rejected))
        self.assertEqual(result["request"]["shops"][0]["datasets"], ["b2b", "promotion", "sku"])

    def test_empty_sources_and_unsupported_market_are_not_success(self):
        body = fixture()
        body["shops"] = []
        self.assertFalse(preview(body)["canCollect"])
        body["markets"] = [{"platform": "天猫", "category": "类目", "scope": "全部", "rankingDimension": "SKU", "priceBandFilter": "全部"}]
        result = preview(body)
        self.assertEqual(len(result["coverage"]), 3)
        self.assertFalse(result["canCollect"])
        self.assertTrue(all(c["status"] == "unsupported" for c in result["coverage"]))

    def test_actual_utf8_plan_bytes_digest_and_conservative_workflow_bytes(self):
        body = fixture()
        body["question"] = "中" * 1000
        result = preview(body)
        evidence = result["evidenceRequest"]
        plan = {"schemaVersion": "business-evidence-v1", "sources": evidence["sources"], "autoCollect": True,
                "collector": {"version": 1, "surface": "business_collection", "pageSize": 100}, "analysisRequest": evidence["analysisRequest"]}
        self.assertEqual(result["capacity"]["planBytes"], len(canonical(plan).encode()))
        self.assertEqual(result["planDigest"], digest(plan))
        expected_input = {"evidenceRunId": EVIDENCE_ID_PLACEHOLDER, "question": body["question"], "sources": evidence["sources"]}
        self.assertEqual(result["capacity"]["workflowBytes"], len(canonical(expected_input).encode()))
        for field, bound in (("planBytes", "max_plan_bytes"), ("workflowBytes", "max_workflow_bytes")):
            measured = result["capacity"][field]
            self.assertTrue(preview(body, **{bound: measured})["canCollect"])
            denied = preview(body, **{bound: measured - 1})
            self.assertFalse(denied["canCollect"])
            self.assertEqual(denied["sources"], result["sources"])

    def test_support_matrix_and_validator_are_injected_without_django(self):
        body = fixture()
        body["shops"][0].update(platform="自定义平台", datasets=["promotion"])
        self.assertTrue(preview(body, netshop_sources={"promotion": {"自定义平台": ("source", "kind")}})["canCollect"])
        body["shops"] = []
        body["markets"] = [{"platform": "京东", "category": "类目", "scope": "POP", "rankingDimension": "SKU", "priceBandFilter": "全部"}]
        visited = []

        def validate(query):
            visited.append(deepcopy(query))
            query["category"] = "不应泄露的变更"
            return "条件尚不支持" if query["window"] == "yearAgo" else None

        result = preview(body, market_validator=validate)
        self.assertEqual(len(visited), 3)
        self.assertFalse(result["canCollect"])
        self.assertTrue(all(c["query"]["category"] == "类目" for c in result["coverage"]))

        def rejects(_query):
            raise AnalysisContractError("精确条件不存在")

        self.assertTrue(all(c["reason"] == "精确条件不存在" for c in preview(body, market_validator=rejects)["coverage"]))
        with self.assertRaises(AnalysisContractError):
            preview(body, market_validator=lambda _query: False)

    def test_format_duplicates_bounds_and_control_text_fail_closed(self):
        mutations = [
            lambda b: b.update(extra=True), lambda b: b.pop("question"), lambda b: b.update(question=" "),
            lambda b: b.update(question="x" * 1001), lambda b: b.update(question="\x00"),
            lambda b: b.update(startDate="20240201"), lambda b: b.update(endDate="2025-03-01"),
            lambda b: b.update(windows=["previous"]), lambda b: b.update(windows=["current", "current"]),
            lambda b: b.update(windows=["current", []]), lambda b: b.update(shops="shop"),
            lambda b: b["shops"].append(deepcopy(b["shops"][0])),
            lambda b: b["shops"][0].update(shop=" 测试店"), lambda b: b["shops"][0].update(shop="店\t铺"),
            lambda b: b["shops"][0].update(shop="\ud800"), lambda b: b["shops"][0].update(datasets=["bad"]),
            lambda b: b["shops"][0].update(datasets=["sku", "sku"]),
            lambda b: b["shops"][0].update(salesChannels=["A", "A"]),
            lambda b: b["shops"][0].update(salesChannels=[str(i) for i in range(11)]),
            lambda b: b["shops"][0].update(salesChannels=[True]),
            lambda b: b.update(shops=[{**b["shops"][0], "shop": str(i)} for i in range(5)]),
        ]
        for mutate in mutations:
            body = fixture()
            mutate(body)
            with self.subTest(body=repr(body)), self.assertRaises(AnalysisContractError):
                preview(body)
        market = {"platform": "京东", "category": "类目", "scope": "POP", "rankingDimension": "SKU", "priceBandFilter": "全部"}
        for markets in ([market, deepcopy(market)], [{**market, "category": str(i)} for i in range(8)]):
            with self.assertRaises(AnalysisContractError):
                preview({**fixture(), "markets": markets})
        for limit in (True, 0, -1, "12"):
            with self.assertRaises(AnalysisContractError):
                preview(fixture(), max_sources=limit)

    def test_question_matches_api_normalization_and_capacity_is_parameterized(self):
        body = fixture()
        body["question"] = " 第一行\r\n第二行\t不要执行来源文本\0 \r"
        result = preview(body)
        self.assertEqual(result["evidenceRequest"]["analysisRequest"]["question"], "第一行\n第二行\t不要执行来源文本")
        self.assertEqual(result["evidenceRequest"]["analysisRequest"]["requestedDimensions"], ["shop", "category", "spu", "sku", "keyword"])
        self.assertEqual(result["evidenceRequest"]["analysisRequest"]["requestedWindows"], body["windows"])
        original = deepcopy(body)
        preview(body)
        self.assertEqual(body, original)
        self.assertFalse(preview(body, max_sources=3)["canCollect"])
        self.assertTrue(preview(body, max_sources=4)["canCollect"])

    def test_context_and_workflow_measurement_helpers_for_owning_api(self):
        result = preview(fixture())
        context = result["evidenceRequest"]["analysisRequest"]
        self.assertEqual(validate_analysis_request(context), context)
        copy = validate_analysis_request(context)
        copy["requestedDimensions"].clear()
        self.assertTrue(context["requestedDimensions"])
        for key, value in (("schemaVersion", "bad"), ("question", ""), ("requestedDimensions", ["unknown"]),
                           ("requestedDimensions", []), ("requestedDimensions", ["shop", "shop"]),
                           ("requestedWindows", ["yearAgo"]), ("requestedWindows", ["current", "current"])):
            with self.subTest(key=key, value=value), self.assertRaises(AnalysisContractError):
                validate_analysis_request({**context, key: value})
        sources = result["sources"]
        actual = workflow_input_bytes(context["question"], sources, evidence_run_id="evidence-123")
        self.assertEqual(result["capacity"]["workflowBytes"] - actual, 45 - len("evidence-123"))
        for broken in (None, [{"nan": float("nan")}], [{"invalid": set()}]):
            with self.assertRaises(AnalysisContractError):
                workflow_input_bytes("question", broken)
