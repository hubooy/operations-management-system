"""Pure contract tests: no Django setup, database, HTTP or authority claims."""
import ast
import hashlib
import json
from pathlib import Path
import re
import unittest

from market import analysis_options_contract as contract
from market.errors import MarketApiError


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def scope(*ranges):
    return {"sourceType": "market_ranking", "ranges": sorted(ranges or [source_range()], key=canonical)}


def source_range(**overrides):
    return {"category": "饮水机", "scope": "POP", "rankingDimension": "SKU", "priceBandFilter": "全部",
            "periodStart": "2026-09-01", "periodEnd": "2026-09-01", **overrides}


def entry(index=0, *, wide=0):
    return {"identity": {"platform": "京东", "category": f"{index:05d}" + "😀" * wide,
                         "scope": "😀" * wide or "POP", "rankingDimension": "SKU",
                         "priceBandFilter": "😀" * wide or "全部"},
            "firstDate": "2026-09-01", "lastDate": "2026-09-03"}


def page(entries, **changes):
    return contract.make_page(entries, **{"revision": "7:abcdef012345", "generation": "generation-1",
        "directory_digest": "a" * 64, "query": {}, "has_more": False, "next_cursor": None, **changes})


def owning_pure_functions(filename, names, namespace):
    # Execute actual pure function ASTs without importing owning ORM modules. This
    # is only a format compatibility check, never a publication/permission test.
    path = Path(__file__).parent / filename
    parsed = ast.parse(path.read_text(encoding="utf-8-sig"))
    nodes = [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name in names
             or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class MarketOptionsContractTests(unittest.TestCase):
    def test_actual_import_normalization_and_analysis_validator_compatibility(self):
        from business_analysis.contracts import comparison_periods, AnalysisContractError
        env = {"re": re, "hashlib": hashlib, "MarketApiError": MarketApiError, "canonical_json": canonical}
        owning_pure_functions("import_service.py", {
            "MAX_ROWS", "MAX_SAFE_INTEGER", "HEX64", "ISO_DATE", "ROW_KEYS", "PAYLOAD_KEYS", "_text", "_integer",
            "_natural_key", "_scope_identity", "_lock_identity", "_scope_key", "_combined_scope_key",
            "_canonical_business_rows", "_content_hash", "_normalize_row", "validate_import_payload"}, env)
        owning_pure_functions("tests/factories.py", {"natural_key", "market_row", "prepared_payload"}, env)
        payload = env["prepared_payload"](env["market_row"](periodStart="2026-09-01", periodEnd="2026-09-01"))
        normalized = env["validate_import_payload"](payload)
        result = contract.normalize_completed_scope(normalized["scope"], status="completed")
        self.assertEqual(len(result["entries"]), 1)
        validator = owning_pure_functions("analysis.py", {"FIELDS", "validate"}, {
            "comparison_periods": comparison_periods, "AnalysisContractError": AnalysisContractError,
            "MarketApiError": MarketApiError})["validate"]
        query = {**result["entries"][0]["identity"], "operation": "analysis_records",
                 "startDate": "2026-09-01", "endDate": "2026-09-03"}
        self.assertEqual(validator(query)["priceBandFilter"], "全部")
        # Existing import only checks date syntax; a legacy successful batch may
        # contain an impossible date. The new directory must fail, not omit it.
        bad_payload = env["prepared_payload"](env["market_row"](periodStart="2026-02-30", periodEnd="2026-02-30"))
        bad_normalized = env["validate_import_payload"](bad_payload)
        with self.assertRaises(contract.OptionsContractError):
            contract.normalize_completed_scope(bad_normalized["scope"], status="completed")

    def test_daily_envelope_is_not_contiguous_coverage(self):
        result = contract.normalize_completed_scope(scope(source_range(), source_range(periodStart="2026-09-03", periodEnd="2026-09-03"),
            source_range(periodStart="2026-09-01", periodEnd="2026-09-30")), status="completed")
        self.assertEqual(result["rangeCount"], 3)
        self.assertEqual(result["excludedNonDailyRanges"], 1)
        self.assertFalse(result["authorityVerified"])
        out = page(result["entries"])
        dates = out["items"][0]["dateMetadata"]
        self.assertEqual((dates["firstDate"], dates["lastDate"]), ("2026-09-01", "2026-09-03"))
        self.assertFalse(dates["coverageVerified"])
        self.assertFalse(out["authorityVerified"])
        self.assertNotIn("ready", out)

    def test_valid_nondaily_scope_explicitly_excluded(self):
        out = contract.normalize_completed_scope(scope(source_range(periodEnd="2026-09-30")), status="completed")
        self.assertEqual(out["entries"], [])
        self.assertEqual(out["excludedNonDailyRanges"], 1)

    def test_missing_empty_bad_history_cannot_be_empty_success(self):
        for value in (None, {}, {"ranges": []}, {"sourceType": "market_ranking", "ranges": []},
                      scope(source_range(periodStart="2026-02-30", periodEnd="2026-02-30")),
                      scope(source_range(periodStart="2026-09-02", periodEnd="2026-09-01")),
                      scope(source_range(periodStart="20260901")), scope(source_range(periodEnd=None))):
            with self.subTest(value=value), self.assertRaises(contract.OptionsContractError):
                contract.normalize_completed_scope(value, status="completed")
        for status in ("pending", "failed", None, True, "completed "):
            with self.assertRaises(contract.OptionsContractError):
                contract.normalize_completed_scope(scope(), status=status)

    def test_complete_scope_validated_before_daily_exclusion(self):
        bad = source_range(periodEnd="2026-09-30", priceBandFilter=" ")
        with self.assertRaises(contract.OptionsContractError):
            contract.normalize_completed_scope(scope(source_range(), bad), status="completed")

    def test_strict_fields_and_scalar_types(self):
        for change in ({"x": 1}, {"category": {"nested": []}}, {"scope": True},
                       {"rankingDimension": "sku"}, {"priceBandFilter": 12.0}):
            with self.assertRaises(contract.OptionsContractError):
                contract.normalize_completed_scope(scope(source_range(**change)), status="completed")
        cyclic = {}
        cyclic["category"] = cyclic
        with self.assertRaises(contract.OptionsContractError):
            contract.normalize_identity({**entry()["identity"], **cyclic})
        for value in ("x" * 201, " POP", "POP ", "a\x7fb", "a\x00b", "a\ud800b"):
            with self.assertRaises(contract.OptionsContractError):
                contract.normalize_completed_scope(scope(source_range(scope=value)), status="completed")

    def test_normalized_sorted_unique_ranges_required(self):
        values = scope(source_range(), source_range(category="A"))
        values["ranges"].reverse()
        for bad in (values, scope(source_range(), source_range())):
            with self.assertRaises(contract.OptionsContractError):
                contract.normalize_completed_scope(bad, status="completed")

    def test_original_band_dimension_scope_never_aliased(self):
        ranges = [source_range(priceBandFilter=value) for value in ("全部", "全价格带", "0-500", "0—500")]
        ranges += [source_range(rankingDimension="SPU"), source_range(scope="自营")]
        entries = contract.normalize_completed_scope(scope(*ranges), status="completed")["entries"]
        result = page(entries)
        self.assertEqual(len({v["optionKey"] for v in result["items"]}), 6)
        self.assertEqual({v["identity"]["priceBandFilter"] for v in result["items"]}, {"全部", "全价格带", "0-500", "0—500"})
        self.assertTrue(all("shop" not in v["identity"] for v in result["items"]))

    def test_merge_exact_identity_copy_and_digest_order(self):
        a, b = [entry()], [entry(), entry(1)]
        a[0]["firstDate"] = "2025-01-01"
        out = contract.merge_entries(a, b)
        self.assertEqual(out[0]["firstDate"], "2025-01-01")
        self.assertEqual(contract.directory_digest(out), hashlib.sha256(canonical(out).encode()).hexdigest())
        out[0]["identity"]["category"] = "changed"
        self.assertEqual(a[0]["identity"]["category"], "00000")
        self.assertEqual(b[0]["firstDate"], "2026-09-01")
        self.assertEqual(contract.merge_entries([], []), [])

    def test_5000_ranges_and_10000_identities_complete_no_truncation(self):
        batch = scope(*(source_range(category=f"C{i:05d}") for i in range(5000)))
        self.assertEqual(len(contract.normalize_completed_scope(batch, status="completed")["entries"]), 5000)
        batch["ranges"].append(source_range(category="Z"))
        with self.assertRaises(contract.OptionsContractError):
            contract.normalize_completed_scope(batch, status="completed")
        directory = contract.merge_entries([entry(i) for i in range(5000)], [entry(i) for i in range(5000, 10000)])
        self.assertEqual(len(directory), contract.MAX_IDENTITIES)
        with self.assertRaises(contract.OptionsContractError):
            contract.merge_entries(directory, [entry(10000)])
        self.assertEqual(len(directory), 10000)

    def test_directory_byte_budget_independent_of_identity_count(self):
        values = [entry(i, wide=195) for i in range(7000)]
        self.assertLess(len(values), contract.MAX_IDENTITIES)
        self.assertGreater(len(canonical(values).encode()), contract.MAX_DIRECTORY_BYTES)
        with self.assertRaises(contract.OptionsContractError):
            contract.merge_entries([], values)

    def test_utf8_page_exact_boundary_and_one_byte_over(self):
        selected = None
        for width in range(50, 110):
            candidate = [entry(i, wide=width) for i in range(20)]
            try:
                result = page(candidate, has_more=True, next_cursor="x")
            except contract.OptionsContractError:
                continue
            gap = contract.MAX_PAGE_BYTES - len(canonical(result).encode())
            if 0 <= gap < contract.MAX_CURSOR_LENGTH - 1:
                selected, cursor = candidate, "x" * (gap + 1)
                break
        self.assertIsNotNone(selected)
        result = page(selected, has_more=True, next_cursor=cursor)
        self.assertEqual(len(canonical(result).encode()), 38000)
        self.assertLess(len(canonical(result)), 38000)
        with self.assertRaises(contract.OptionsContractError):
            page(selected, has_more=True, next_cursor=cursor + "x")

    def test_whole_page_rejects_wide_identities_not_prefix_success(self):
        with self.assertRaises(contract.OptionsContractError):
            page([entry(i, wide=195) for i in range(20)])
        with self.assertRaises(contract.OptionsContractError):
            page([entry(i) for i in range(21)])

    def test_page_order_duplicate_query_and_pagination_guards(self):
        for values in ([entry(1), entry(0)], [entry(0), entry(0)]):
            with self.assertRaises(contract.OptionsContractError):
                page(values)
        for changes in ({"has_more": 1}, {"has_more": True, "next_cursor": "x"}, {"next_cursor": "x"},
                        {"query": {"platform": "天猫"}}, {"query": {"category": "absent"}},
                        {"query": {"q": "absent"}}, {"query": {"limit": "20"}}, {"generation": True},
                        {"directory_digest": "a" * 63}, {"revision": ""}):
            with self.assertRaises(contract.OptionsContractError):
                page([entry()], **changes)
        out = page([entry()], query={"platform": "京东", "q": "POP"})
        self.assertEqual(out["pagination"]["returned"], 1)
        self.assertEqual(page([])["items"], [])  # Pure empty is NOT an authoritative ready result.

    def test_page_digests_cover_all_protocol_fields_and_no_aliases(self):
        values = [entry()]
        result = page(values)
        digest = result.pop("pageDigest")
        self.assertEqual(digest, hashlib.sha256(canonical(result).encode()).hexdigest())
        for changes in ({"generation": "next"}, {"revision": "8:abcdef012345"}, {"directory_digest": "b" * 64}):
            self.assertNotEqual(page(values, **changes)["pageDigest"], digest)
            self.assertNotEqual(page([], **changes)["pageDigest"], page([])["pageDigest"])
        result["items"][0]["identity"]["scope"] = "changed"
        self.assertEqual(values[0]["identity"]["scope"], "POP")

    def test_fixed_capacity_constants(self):
        self.assertEqual((contract.MAX_BATCH_RANGES, contract.MAX_IDENTITIES, contract.MAX_DIRECTORY_BYTES,
                          contract.PAGE_SIZE, contract.MAX_PAGE_BYTES), (5000, 10000, 16 * 1024 * 1024, 20, 38000))


if __name__ == "__main__":
    unittest.main()
