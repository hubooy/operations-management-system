from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from .contracts import AnalysisContractError, canonical, digest
from . import evidence_v2 as contract


def sources(count=19):
    return [{"key": "source-"+str(i), "domain": "sales", "query": {"platform": "京东", "shop": "合成店",
        "channel": "合成渠道"+str(i), "startDate": "2024-02-01", "endDate": "2024-02-29"}} for i in range(count)]


def pages(value, limit=10):
    result, offset = [], 0
    while True:
        page = contract.directory_page(value, run_id="evidence-fixed", evidence_version=7, offset=offset, limit=limit)
        result.append(page)
        if page["nextOffset"] is None:
            return result
        offset = page["nextOffset"]


def rehash(page):
    page["pageDigest"] = digest({k: v for k, v in page.items() if k != "pageDigest"})
    return page


class EvidenceV2Tests(TestCase):
    def verify(self, value, expected):
        return contract.validate_directory_pages(value, expected, run_id="evidence-fixed", evidence_version=7)

    def test_48_sources_compact_header_and_small_reference_do_not_expand_fact_capacity(self):
        value = sources(48)
        built = contract.build_catalog(value)
        self.assertEqual(built["header"]["sourceCount"], 48)
        self.assertEqual(built["header"]["limits"], {"factBytes": 64*1024*1024, "factPages": 2000})
        self.assertNotIn("sources", built["header"])
        self.assertNotIn("entries", built["header"])
        self.assertLessEqual(len(canonical(built["header"]).encode()), 16000)
        reference = contract.workflow_reference(value, run_id="evidence-fixed", evidence_version=7,
            sealed_digest="a"*64, question="中"*1000)
        self.assertNotIn("sources", reference)
        self.assertLessEqual(len(canonical(reference).encode()), 8000)
        self.assertEqual(reference["evidencePlanDigest"], built["planDigest"])
        self.assertTrue(self.verify(pages(value), value)["complete"])
        for count in (0, 49):
            with self.assertRaises(AnalysisContractError):
                contract.build_catalog(sources(count))

    def test_normalization_is_stable_nonmutating_and_default_window_explicit(self):
        value = sources()
        original = deepcopy(value)
        expected = contract.build_catalog(value)
        self.assertEqual(value, original)
        self.assertEqual(expected, contract.build_catalog(list(reversed(value))))
        for entry in expected["entries"]:
            self.assertEqual(entry["query"]["window"], "current")
            self.assertEqual(entry["queryDigest"], digest(entry["query"]))
        self.assertEqual([r["ordinal"] for r in expected["entries"]], list(range(1, 20)))

    def test_duplicate_keys_and_normalized_query_identity_rejected(self):
        first = sources(2)
        first[1]["key"] = first[0]["key"]
        second = sources(1)
        second.append({**deepcopy(second[0]), "key": "another"})
        second[1]["query"]["window"] = "current"
        for value in (first, second):
            with self.assertRaises(AnalysisContractError):
                contract.build_catalog(value)

    def test_exact_platform_shop_and_domain_identity_stays_separate(self):
        value = sources(1)
        for i, changes in enumerate(({"platform": "天猫"}, {"shop": "另一店"}), 1):
            other = deepcopy(value[0])
            other["key"] = "source-"+str(i)
            other["query"].update(changes)
            value.append(other)
        self.assertEqual(contract.build_catalog(value)["header"]["sourceCount"], 3)

    def test_malformed_unsupported_and_mixed_period_sources_rejected(self):
        changes = [("domain", []), ("key", "bad/key"), ("query", {"sql": "select"})]
        for field, replacement in changes:
            value = sources(1)
            value[0][field] = replacement
            with self.assertRaises(AnalysisContractError):
                contract.build_catalog(value)
        for field, replacement in (("platform", True), ("shop", " 有空白"), ("channel", "坏\ud800"), ("startDate", "2024-2-1"), ("window", 1)):
            value = sources(1)
            value[0]["query"][field] = replacement
            with self.assertRaises(AnalysisContractError):
                contract.build_catalog(value)
        value = sources(2)
        value[1]["query"]["endDate"] = "2024-02-28"
        with self.assertRaises(AnalysisContractError):
            contract.build_catalog(value)

    def test_query_rejects_nested_or_cyclic_values_without_copying_them(self):
        cycle = []
        cycle.append(cycle)
        class RefuseCopy:
            def __deepcopy__(self, memo):
                raise AssertionError("untrusted values must never be copied")
        for replacement in (cycle, {"nested": cycle}, RefuseCopy(), ["x"]*10000):
            value = sources(1)
            value[0]["query"]["channel"] = replacement
            with self.assertRaises(AnalysisContractError):
                contract.build_catalog(value)
        value = sources(1)
        value[0]["query"].update({"extra-"+str(i): cycle for i in range(10000)})
        with self.assertRaises(AnalysisContractError):
            contract.build_catalog(value)
        value = [{"key": "bad", "domain": "netshop", "query": {"platform": "天猫", "shop": "店", "dataset": "sku",
            "startDate": "2024-02-01", "endDate": "2024-02-29"}}]
        with self.assertRaises(AnalysisContractError):
            contract.build_catalog(value)

    def test_optional_metadata_requires_exact_planned_fact_windows_current_master_allowed(self):
        value = sources(1)
        for window in ("previous", "yearAgo"):
            value.append({"key": window, "domain": "sales", "query": {**value[0]["query"], "window": window}})
        value.append({"key": "master", "domain": "netshop", "query": {"platform": "京东", "shop": "合成店", "dataset": "master",
            "startDate": "2024-02-01", "endDate": "2024-02-29"}})
        request = {"schemaVersion": "business-analysis-request-v1", "question": " 分析\r\n问题 ",
            "requestedDimensions": ["shop", "sku"], "requestedWindows": ["current", "previous", "yearAgo"]}
        built = contract.build_catalog(value, analysis_request=request)
        self.assertEqual(built["header"]["analysisRequest"]["question"], "分析\n问题")
        self.assertNotIn("analysisRequest", contract.build_catalog(value)["header"])
        for altered in ([s for s in value if s["key"] != "yearAgo"], [*value[:-1], {**value[-1], "query": {**value[-1]["query"], "window": "yearAgo"}}]):
            with self.assertRaises(AnalysisContractError):
                contract.build_catalog(altered, analysis_request=request)
        with self.assertRaises(AnalysisContractError):
            contract.build_catalog(value, analysis_request={**request, "requestedWindows": ["current"]})

    def test_trusted_header_rebuild_rejects_rehashed_or_typed_changes(self):
        value = sources(1)
        built = contract.build_catalog(value)
        self.assertEqual(contract.validate_header(built["header"], value), built["header"])
        for field, replacement in (("sourceCount", True), ("sourceCount", 1.0), ("catalogDigest", "b"*64), ("capacityProfile", "unlimited")):
            altered = deepcopy(built["header"])
            altered[field] = replacement
            with self.assertRaises(AnalysisContractError):
                contract.validate_header(altered, value)

    def test_missing_duplicate_out_of_order_and_extra_pages_rejected(self):
        value = sources(21)
        original = pages(value)
        for altered in ([], original[:-1], original[1:], [original[0], original[0], *original[1:]],
                [original[1], original[0], original[2]], [*original, original[-1]]):
            with self.assertRaises(AnalysisContractError):
                self.verify(altered, value)

    def test_rehashing_tampered_item_or_binding_cannot_forge_trusted_catalog(self):
        value = sources(2)
        original = pages(value)
        for field, replacement in (("runId", "other"), ("evidenceVersion", 8), ("catalogDigest", "c"*64), ("planDigest", "d"*64),
                ("total", 3), ("returned", 1), ("nextOffset", 1), ("offset", 1)):
            altered = deepcopy(original)
            altered[0][field] = replacement
            rehash(altered[0])
            with self.assertRaises(AnalysisContractError):
                self.verify(altered, value)
        altered = deepcopy(original)
        altered[0]["items"][0]["query"]["shop"] = "另一店"
        altered[0]["items"][0]["queryDigest"] = digest(altered[0]["items"][0]["query"])
        rehash(altered[0])
        with self.assertRaises(AnalysisContractError):
            self.verify(altered, value)
        altered = deepcopy(original)
        altered[0]["items"].reverse()
        rehash(altered[0])
        with self.assertRaises(AnalysisContractError):
            self.verify(altered, value)

    def test_bool_float_substitutions_and_untrusted_nested_objects_rejected(self):
        value = sources(1)
        original = pages(value, 1)
        for field, replacement in (("requestedLimit", True), ("returned", True), ("total", 1.0), ("evidenceVersion", 7.0), ("offset", False)):
            altered = deepcopy(original)
            altered[0][field] = replacement
            with self.assertRaises(AnalysisContractError):
                self.verify(altered, value)
        nested = []
        nested.append(nested)
        altered = deepcopy(original)
        altered[0]["items"][0]["query"] = nested
        with self.assertRaises(AnalysisContractError):
            self.verify(altered, value)
        for kwargs in ({"offset": True}, {"limit": 1.0}, {"offset": 1}, {"limit": 21}, {"evidence_version": True}):
            with self.assertRaises(AnalysisContractError):
                contract.directory_page(value, **{"run_id": "evidence-fixed", "evidence_version": 7, **kwargs})

    def test_byte_bounded_directory_uses_actual_prefix_and_rejects_oversized_single_row(self):
        value = sources(3)
        one = contract.directory_page(value, run_id="evidence-fixed", evidence_version=7, limit=3)
        single = contract.directory_page(value, run_id="evidence-fixed", evidence_version=7, limit=1)
        cap = len(canonical(single).encode())+10
        self.assertGreater(len(canonical(one).encode()), cap)
        with patch.object(contract, "MAX_DIRECTORY_PAGE_BYTES", cap):
            result = pages(value, limit=3)
            self.assertEqual([p["returned"] for p in result], [1, 1, 1])
            self.assertEqual([p["nextOffset"] for p in result], [1, 2, None])
            self.assertTrue(self.verify(result, value)["complete"])
        with patch.object(contract, "MAX_DIRECTORY_PAGE_BYTES", 10), self.assertRaises(AnalysisContractError):
            pages(value)

    def test_reference_validates_version_digest_and_question_without_claiming_seal(self):
        default = {"run_id": "evidence-fixed", "evidence_version": 7, "sealed_digest": "a"*64, "question": "分析"}
        for changed in ({"evidence_version": True}, {"evidence_version": 7.0}, {"sealed_digest": "A"*64}, {"run_id": "bad/id"}, {"question": ""}):
            with self.assertRaises(AnalysisContractError):
                contract.workflow_reference(sources(1), **{**default, **changed})

    def test_all_supported_directory_sizes_and_variable_page_limits_cover_once(self):
        for count in (1, 16, 19, 35, 48):
            value = sources(count)
            for limit in (1, 7, 20):
                verified = self.verify(iter(pages(value, limit)), value)
                self.assertEqual(verified["sourceCount"], count)
                self.assertEqual(verified["pageCount"], (count+limit-1)//limit)

    def test_stream_cannot_change_trusted_sources_between_verified_pages(self):
        value = sources(2)
        first = pages(value, 1)[0]
        def tampered_stream():
            yield first
            value[0]["query"]["shop"] = "被篡改的店"
            yield contract.directory_page(value, run_id="evidence-fixed", evidence_version=7, offset=1, limit=1)
        with self.assertRaises(AnalysisContractError):
            self.verify(tampered_stream(), value)
