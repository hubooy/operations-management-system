from copy import deepcopy
from unittest import TestCase

from .contracts import AnalysisContractError, canonical, digest
from . import mapping_plan as mapping


def source(key, domain="sales", *, window="current", shop="合成店", channel="精确渠道", dataset="master"):
    query = {"platform": "京东", "shop": shop, "startDate": "2026-08-01", "endDate": "2026-08-31", "window": window}
    query["channel" if domain == "sales" else "dataset"] = channel if domain == "sales" else dataset
    return {"key": key, "domain": domain, "query": query}


def fixture():
    return ([source("sales"), source("previous", window="previous"), source("year", window="yearAgo"), source("master", "netshop")],
        [{"salesKey": key, "masterKey": "master"} for key in ("sales", "previous", "year")])


class MappingPlanTests(TestCase):
    def test_canonical_plan_preserves_all_choices_stable_order_and_full_digest(self):
        sources, pairs = fixture()
        original = deepcopy((sources, pairs))
        value = mapping.build(sources, pairs)
        self.assertEqual(value, mapping.build(list(reversed(sources)), list(reversed(pairs))))
        self.assertEqual(value["planDigest"], digest(value["plan"]))
        self.assertEqual(value["plan"]["schemaVersion"], "business-mapping-plan-v1")
        self.assertEqual(value["plan"]["algorithmVersion"], "exact-product-partition-v1")
        self.assertEqual([p["salesKey"] for p in value["plan"]["pairs"]], ["previous", "sales", "year"])
        for pair in value["plan"]["pairs"]:
            self.assertEqual(pair["pairKey"], digest([mapping.ALGORITHM_VERSION, pair["salesKey"], pair["masterKey"]]))
            self.assertEqual(len(pair["pairKey"]), 64)
        self.assertEqual((sources, pairs), original)
        validated = mapping.validate(value, sources, pairs)
        validated["plan"]["pairs"].clear()
        self.assertEqual(len(value["plan"]["pairs"]), 3)

    def test_no_guessing_merging_duplicates_or_empty_plans(self):
        sources, pairs = fixture()
        for invalid in ([], (), None, True, pairs+[pairs[0]], [{"salesKey": "sales"}],
                [{"salesKey": "sales", "masterKey": "missing"}], [{"salesKey": "master", "masterKey": "master"}]):
            with self.subTest(invalid=invalid), self.assertRaises(AnalysisContractError): mapping.build(sources, invalid)
        sources.append(source("master_other", "netshop", shop="别店"))
        with self.assertRaises(AnalysisContractError):
            mapping.build(sources, pairs+[{"salesKey": "sales", "masterKey": "master_other"}])

    def test_domain_current_master_and_exact_store_boundaries(self):
        sources, pairs = fixture()
        for replacement in (source("master", "netshop", dataset="promotion"), source("master", "netshop", window="previous"),
                source("master", "netshop", shop="合成店(别名)"), source("master")):
            altered = [*sources[:-1], replacement]
            with self.subTest(replacement=replacement), self.assertRaises(AnalysisContractError): mapping.build(altered, pairs)
        changed = deepcopy(sources); changed[-1]["query"]["platform"] = "天猫"
        with self.assertRaises(AnalysisContractError): mapping.build(changed, pairs)
        changed = deepcopy(sources); changed[0]["query"]["shop"] = " 合成店"
        with self.assertRaises(AnalysisContractError): mapping.build(changed, pairs)

    def test_complete_catalog_bounded_and_unreferenced_invalid_source_not_ignored(self):
        sources, pairs = fixture()
        with self.assertRaises(AnalysisContractError): mapping.build(sources+[source("extra", "netshop", dataset="unsupported")], pairs)
        with self.assertRaises(AnalysisContractError): mapping.build(sources+[deepcopy(sources[0])], pairs)
        many = [source(f"sales{i}", channel=f"渠道{i}") for i in range(48)]+[source("master", "netshop")]
        with self.assertRaises(AnalysisContractError): mapping.build(many, pairs)
        sources, pairs = fixture()
        with self.assertRaises(AnalysisContractError): mapping.build(sources, pairs*16)

    def test_all_47_pairs_of_48_sources_are_retained(self):
        sources = [source(f"sales{i}", channel=f"渠道{i}") for i in range(47)]+[source("master", "netshop")]
        pairs = [{"salesKey": s["key"], "masterKey": "master"} for s in sources[:-1]]
        built = mapping.build(sources, pairs)
        self.assertEqual(len(built["plan"]["pairs"]), 47)
        self.assertLessEqual(len(canonical(built["plan"]).encode()), 16000)

    def test_exact_16000_utf8_bytes_pass_one_extra_rejected_without_truncation(self):
        master_key = "m"*160
        sales_keys = [f"s{i:02}" for i in range(47)]
        def inputs():
            return ([source(key, channel=f"渠道{i}") for i, key in enumerate(sales_keys)]+[source(master_key, "netshop")],
                [{"salesKey": key, "masterKey": master_key} for key in sales_keys])
        sources, pairs = inputs()
        remaining = 16000-len(canonical(mapping.normalize(sources, pairs)).encode("utf-8"))
        self.assertGreater(remaining, 0)
        for i in range(len(sales_keys)):
            delta = min(remaining, 160-len(sales_keys[i]))
            sales_keys[i] += "x"*delta
            remaining -= delta
        self.assertEqual(remaining, 0)
        sources, pairs = inputs()
        self.assertEqual(len(canonical(mapping.normalize(sources, pairs)).encode("utf-8")), 16000)
        index = next(i for i, key in enumerate(sales_keys) if len(key) < 160)
        sales_keys[index] += "x"
        sources, pairs = inputs()
        with self.assertRaisesRegex(AnalysisContractError, "16000"): mapping.build(sources, pairs)
        self.assertEqual(len(pairs), 47)

    def test_validate_rebuild_rejects_rehashed_theft_and_unknown_fields_versions(self):
        sources, pairs = fixture()
        value = mapping.build(sources, pairs)
        wrong = mapping.build(sources, pairs[:1])
        with self.assertRaises(AnalysisContractError): mapping.validate(wrong, sources, pairs)
        for path, replacement in (("schemaVersion", "business-mapping-plan-v2"), ("algorithmVersion", "guessed-mapping"), ("budgetPlan", {})):
            wrong = deepcopy(value); wrong["plan"][path] = replacement; wrong["planDigest"] = digest(wrong["plan"])
            with self.subTest(path=path), self.assertRaises(AnalysisContractError): mapping.validate(wrong, sources, pairs)
        wrong = deepcopy(value); wrong["plan"]["pairs"][0]["pairKey"] = "a"*64; wrong["planDigest"] = digest(wrong["plan"])
        with self.assertRaises(AnalysisContractError): mapping.validate(wrong, sources, pairs)
        wrong = deepcopy(value); wrong["plan"]["pairs"].reverse(); wrong["planDigest"] = digest(wrong["plan"])
        with self.assertRaises(AnalysisContractError): mapping.validate(wrong, sources, pairs)

    def test_validate_untrusted_structures_fail_before_unbounded_serialization(self):
        sources, pairs = fixture()
        value = mapping.build(sources, pairs)
        for replacement in (True, 1, 1.0, None, {}, "x"*1000000, [value]*100000):
            wrong = deepcopy(value); wrong["plan"]["pairs"] = replacement
            with self.subTest(kind=type(replacement).__name__), self.assertRaises(AnalysisContractError): mapping.validate(wrong, sources, pairs)
        wrong = deepcopy(value); wrong["plan"]["pairs"][0]["salesKey"] = {"nested": []}
        with self.assertRaises(AnalysisContractError): mapping.validate(wrong, sources, pairs)
        with self.assertRaises(AnalysisContractError): mapping.build(sources, [{"salesKey": True, "masterKey": "master"}])
        wrong = deepcopy(value); wrong["planDigest"] = "a"*1000000
        with self.assertRaises(AnalysisContractError): mapping.validate(wrong, sources, pairs)

    def test_comparison_requires_current_baseline_same_master_and_exact_query(self):
        sources, pairs = fixture()
        plan = mapping.normalize(sources, pairs)
        keys = {p["salesKey"]: p["pairKey"] for p in plan["pairs"]}
        for name, window in (("previous", "previous"), ("year", "yearAgo")):
            self.assertEqual(mapping.validate_baseline_pair(sources, plan, keys["sales"], keys[name]),
                {"currentPairKey": keys["sales"], "baselinePairKey": keys[name], "window": window})
        for a, b in (("previous", "sales"), ("previous", "year"), ("sales", "sales")):
            with self.subTest(a=a,b=b), self.assertRaises(AnalysisContractError): mapping.validate_baseline_pair(sources, plan, keys[a], keys[b])
        altered = deepcopy(sources); altered[1]["query"]["channel"] = "不同渠道"
        with self.assertRaises(AnalysisContractError): mapping.validate_baseline_pair(altered, plan, keys["sales"], keys["previous"])
        altered = deepcopy(sources); altered[1]["query"]["startDate"] = "2026-08-02"
        with self.assertRaises(AnalysisContractError): mapping.validate_baseline_pair(altered, plan, keys["sales"], keys["previous"])

    def test_cross_shop_master_and_unknown_or_tampered_pair_are_not_comparable(self):
        sources = [source("sales"), source("previous", window="previous", shop="别店"), source("master", "netshop"), source("master2", "netshop", shop="别店")]
        pairs = [{"salesKey": "sales", "masterKey": "master"}, {"salesKey": "previous", "masterKey": "master2"}]
        plan = mapping.normalize(sources, pairs); keys = {p["salesKey"]: p["pairKey"] for p in plan["pairs"]}
        with self.assertRaises(AnalysisContractError): mapping.validate_baseline_pair(sources, plan, keys["sales"], keys["previous"])
        with self.assertRaises(AnalysisContractError): mapping.validate_baseline_pair(sources, plan, keys["sales"], "a"*64)
        wrong = deepcopy(plan); wrong["algorithmVersion"] = "unsupported"
        with self.assertRaises(AnalysisContractError): mapping.validate_baseline_pair(sources, wrong, keys["sales"], keys["previous"])
        wrong = deepcopy(plan); wrong["pairs"][0]["pairKey"] = "b"*64
        with self.assertRaises(AnalysisContractError): mapping.validate_baseline_pair(sources, wrong, keys["sales"], keys["previous"])

    def test_omitted_window_is_current_without_mutating_catalog(self):
        sources, pairs = fixture(); del sources[0]["query"]["window"]; del sources[-1]["query"]["window"]
        before = deepcopy(sources)
        plan = mapping.normalize(sources, pairs); keys = {p["salesKey"]: p["pairKey"] for p in plan["pairs"]}
        self.assertEqual(mapping.validate_baseline_pair(sources, plan, keys["sales"], keys["previous"])["window"], "previous")
        self.assertEqual(sources, before)
