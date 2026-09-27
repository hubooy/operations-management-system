"""Pure prospective contract checks; no Django setup, database or registry."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest
from . import business_promotion_runtime_contract as contract
from . import business_screening_runtime_contract as screening


def inputs():
    query = {"platform": "京东", "shop": "志高商用设备旗舰店", "dataset": "promotion",
             "startDate": "2024-02-01", "endDate": "2024-02-29", "window": "current"}
    sources = [{"key": "ads", "domain": "netshop", "query": query},
               {"key": "ads-prior", "domain": "netshop", "query": {**query, "window": "previous"}},
               {"key": "ads-year", "domain": "netshop", "query": {**query, "window": "yearAgo"}}]
    return sources, {"reportId": "report-1", "runId": "run-1", "screeningId": "screen-1", "sealedDigest": "a"*64}, {
        "sourceKey": "ads", "baselineKey": "ads-prior", "view": "keyword_sku"}


def reference(args):
    return {"contextDigest": contract.selection(*args)["contextDigest"], "tableBindingDigest": "b"*64,
            "rowIndex": 0, "rowId": "c"*64, "metric": "spendCents", "field": "difference"}


class PromotionRuntimeContractTests(TestCase):
    def test_new_graph_preserves_pinned_old_graph_and_five_role_dependencies(self):
        expected = {
            False: "b825ed989cd07e29fb0a463c636b7febe4a597baebac348cf99203d4f0d82430",
            True: "606304c4dfd49f3d2fb5eafbc057e6bfda2fb63ca9b0e7ad73615fc2c73b6bd5",
        }
        for budget in (False, True):
            old = screening.graph(budget)
            self.assertEqual(digest(old), expected[budget])
            proposed = contract.graph(budget)
            self.assertEqual(digest(screening.graph(budget)), expected[budget])
            self.assertNotEqual(digest(proposed), expected[budget])
            nodes = proposed["nodes"]
            self.assertEqual([node["key"] for node in nodes], [*screening.ROLES, "human_review"])
            self.assertEqual([node["dependsOn"] for node in nodes],
                [[], [], [], ["commerce", "promotion", "market_b2b"],
                 ["commerce", "promotion", "market_b2b", "independent_review"], ["report"]])
            self.assertEqual([node["type"] for node in nodes], ["agent"]*5+["human_review"])
            self.assertEqual(set(contract.TOOL_ORDER), contract.TOOLS)
            self.assertEqual(len(contract.TOOL_ORDER), 4)
            for node in nodes[:5]:
                instruction = node["instruction"]
                self.assertIn(contract.PACKAGE_TOOL, instruction)
                self.assertNotIn(screening.PACKAGE_TOOL, instruction)
                self.assertNotIn(screening.TABLE_TOOL, instruction)
                self.assertEqual(contract.PROMOTION_TOOL in instruction, node["key"] in contract.PROMOTION_ROLES)
                self.assertIn(str(screening.OUTPUT_LIMITS[node["key"]]), instruction)
                if budget and node["key"] in screening.BUDGET_NODES:
                    self.assertIn("读完固定预算", instruction)
                    self.assertIn(contract.BUDGET_TOOL, instruction)
                elif not budget:
                    self.assertNotIn("读完固定预算", instruction)
        with self.assertRaises(AnalysisContractError): contract.graph(1)

    def test_snapshot_freezes_both_views_source_base_context_and_algorithm(self):
        sources, context, _ = inputs()
        selector = {"sourceKey": "ads", "baselineKey": "ads-prior"}
        fixed = contract.freeze_snapshot(sources, context, selector)
        self.assertEqual(fixed["executionProfile"], contract.PROFILE)
        self.assertEqual(fixed["promotionSelector"], {**selector, "views": list(contract.PROMOTION_VIEWS)})
        self.assertEqual(fixed["sealedDigest"], context["sealedDigest"])
        self.assertEqual(fixed["promotionAlgorithmVersion"], contract.ALGORITHM_VERSION)
        self.assertFalse(fixed["authorityVerified"])
        self.assertFalse(fixed["registered"])
        self.assertEqual(contract.checked_snapshot(sources, context, fixed), fixed)
        self.assertEqual(contract.freeze_snapshot(list(reversed(sources)), context, selector), fixed)
        no_base = contract.freeze_snapshot(sources, context, {"sourceKey": "ads"})
        self.assertEqual(no_base["promotionSelector"], {"sourceKey": "ads", "views": list(contract.PROMOTION_VIEWS)})
        self.assertNotEqual(no_base["contextDigest"], fixed["contextDigest"])
        selector["sourceKey"] = "ads-year"
        self.assertEqual(fixed["promotionSelector"]["sourceKey"], "ads")
        for change in (lambda a: a[0][0]["query"].update(shop="其他店"),
                       lambda a: a[1].update(reportId="other"),
                       lambda a: a[1].update(runId="other"),
                       lambda a: a[1].update(screeningId="other"),
                       lambda a: a[1].update(sealedDigest="d"*64)):
            altered = [deepcopy(sources), deepcopy(context)]
            change(altered)
            with self.assertRaises(AnalysisContractError):
                contract.checked_snapshot(*altered, fixed)
        for mutation in (lambda v: v["promotionSelector"].update(baselineKey="ads-year"),
                         lambda v: v["promotionSelector"].update(views=["keyword_sku"]),
                         lambda v: v.update(contextDigest="0"*64),
                         lambda v: v.update(sealedDigest="0"*64),
                         lambda v: v.update(promotionAlgorithmVersion="v2"),
                         lambda v: v.update(authorityVerified=True),
                         lambda v: v.update(registered=0),
                         lambda v: v.update(extra=True)):
            altered = deepcopy(fixed); mutation(altered)
            with self.assertRaises(AnalysisContractError):
                contract.checked_snapshot(sources, context, altered)
        for bad in ({"sourceKey": "ads-prior"}, {"sourceKey": "ads", "baselineKey": "ads"},
                    {"sourceKey": "ads", "baselineKey": "ads-year", "extra": 1}):
            with self.assertRaises(AnalysisContractError):
                contract.freeze_snapshot(sources, context, bad)

    def test_scoped_reference_rejects_wrong_role_selection_or_claimed_number(self):
        sources, context, selector = inputs()
        fixed = contract.freeze_snapshot(sources, context, {"sourceKey": "ads", "baselineKey": "ads-prior"})
        raw = reference((sources, context, selector))
        for role in contract.PROMOTION_ROLES:
            result = contract.scoped_row_reference(role, sources, context, fixed, selector, raw)
            self.assertEqual(result["role"], role)
            self.assertFalse(result["authorityVerified"])
            self.assertFalse(result["resolved"])
        for role in ("commerce", "market_b2b", "human_review", "reporter", None):
            with self.assertRaises(AnalysisContractError):
                contract.scoped_row_reference(role, sources, context, fixed, selector, raw)
        for bad in ({**selector, "baselineKey": "ads-year"}, {"sourceKey": "ads", "view": "keyword_sku"},
                    {**selector, "view": "plan"}, {**selector, "extra": 1}):
            with self.assertRaises(AnalysisContractError):
                contract.scoped_row_reference("promotion", sources, context, fixed, bad, raw)
        with self.assertRaises(AnalysisContractError):
            contract.scoped_row_reference("promotion", sources, context, fixed, selector, {**raw, "value": 100})

    def test_prospective_four_tools_preserve_existing_responsibilities(self):
        self.assertEqual(len(contract.TOOLS), 4)
        self.assertEqual(dict(contract.TOOL_CAPABILITIES)[contract.TABLE_TOOL], ("native", "mapped"))
        self.assertEqual(set(dict(contract.TOOL_CAPABILITIES)[contract.PROMOTION_TOOL]), set(contract.VIEWS))
        self.assertNotEqual(contract.PROFILE, "business-agent-screening-reference-v1")
        self.assertTrue(all(name.endswith("_v1") for name in contract.TOOLS))

    def test_explicit_current_previous_and_year_ago_are_bound(self):
        args = inputs()
        for view in contract.VIEWS:
            for base in ("ads-prior", "ads-year"):
                args[2].update(view=view, baselineKey=base)
                value = contract.selection(*args)
                self.assertFalse(value["authorityVerified"])
                self.assertFalse(value["registered"])
                self.assertEqual(value["baseline"]["key"], base)
                self.assertEqual(value["contextDigest"], digest({k:v for k,v in value.items() if k != "contextDigest"}))

    def test_wrong_platform_shop_dataset_window_or_source_rejects(self):
        for change in ({"platform": "天猫"}, {"shop": "另店"}, {"dataset": "sku"}, {"window": "current"},
                       {"startDate": "2024-02-02"}):
            args = inputs(); args[0][1]["query"].update(change)
            with self.assertRaises(AnalysisContractError): contract.selection(*args)
        for change in ({"sourceKey": "absent"}, {"sourceKey": "ads-prior"}, {"baselineKey": "ads"},
                       {"view": "plan"}, {"view": "searchTerm"}, {"view": []}, {"extra": 1}):
            args = inputs(); args[2].update(change)
            with self.assertRaises(AnalysisContractError): contract.selection(*args)

    def test_full_catalog_context_and_view_changes_invalidate_reference(self):
        args = inputs(); claimed = reference(args)
        changes = [lambda a: a[1].update(reportId="report-2"), lambda a: a[1].update(runId="run-2"),
                   lambda a: a[1].update(screeningId="screen-2"), lambda a: a[1].update(sealedDigest="d"*64),
                   lambda a: a[2].update(view="keyword_sku_context"), lambda a: a[2].update(baselineKey="ads-year"),
                   lambda a: a[0][2]["query"].update(shop="其他店")]
        for change in changes:
            modified = deepcopy(args); change(modified)
            with self.assertRaises(AnalysisContractError): contract.row_reference(*modified, claimed)

    def test_money_fields_allowlisted_numbers_never_accepted(self):
        args = inputs(); raw = reference(args)
        for metric in contract.MONEY_METRICS:
            for field in contract.VALUE_FIELDS:
                value = contract.row_reference(*args, {**raw, "metric": metric, "field": field})
                self.assertFalse(value["resolved"])
                self.assertFalse(value["authorityVerified"])
        for change in ({"metric": "clicks"}, {"metric": "netProfit"}, {"field": "ratio"},
                       {"field": "percentagePoints"}, {"value": 7}, {"number": 7}, {"rowIndex": True},
                       {"rowIndex": 1.0}, {"rowIndex": -1}, {"rowIndex": contract.LIMITS["maxGroups"]},
                       {"rowId": "A"*64}, {"tableBindingDigest": "short"}):
            with self.assertRaises(AnalysisContractError): contract.row_reference(*args, {**raw, **change})

    def test_no_baseline_never_invents_comparison(self):
        args = inputs(); args[2].pop("baselineKey")
        raw = reference(args)
        with self.assertRaises(AnalysisContractError): contract.row_reference(*args, raw)
        result = contract.row_reference(*args, {**raw, "field": "value"})
        self.assertIsNone(result["selection"]["baseline"])

    def test_directory_duplicates_cycles_and_deep_unknown_context_fail_closed(self):
        args = inputs(); args[0].append(deepcopy(args[0][0]))
        with self.assertRaises(AnalysisContractError): contract.selection(*args)
        args = inputs(); args[1]["extra"] = args[1]
        with self.assertRaises(AnalysisContractError): contract.selection(*args)
        args = inputs(); args[2]["sourceKey"] = "ads\ud800"
        with self.assertRaises(AnalysisContractError): contract.selection(*args)

    def test_deterministic_catalog_order_and_input_output_aliases(self):
        args = inputs(); before = deepcopy(args)
        value = contract.selection(*args)
        self.assertEqual(value, contract.selection(list(reversed(args[0])), args[1], args[2]))
        value["source"]["query"]["shop"] = "changed"
        self.assertEqual(args, before)
        raw = reference(args); result = contract.row_reference(*args, raw)
        raw["rowId"] = "e"*64
        self.assertEqual(result["reference"]["rowId"], "c"*64)
