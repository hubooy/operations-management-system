"""Pure tests using the actual finance importer scalar projection, no database."""
import ast
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from finance.tests.factories import finance_line, finance_month
from . import finance_source as source
from .contracts import AnalysisContractError, canonical, digest


def projection():
    path = Path(__file__).parents[1] / "finance" / "import_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_line_model")
    scope = {"FinanceLine": lambda **kwargs: kwargs}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), scope)
    return scope["_line_model"]


PROJECT = projection()


def row(index=1, **kwargs):
    value = PROJECT(finance_line("2026-08", "net_sales", 100_000, **kwargs), "synthetic")
    value.pop("created_at")
    return {"id": index, **value}


def fixture(rows=None):
    return dict(rows=[row()] if rows is None else rows,
        query={"months": ["2026-08"], "scope": {"scope_type": "business", "scope_key": "business",
             "scope_name": "志高事业部", "group_name": ""}},
        revision={"revision": 7, "source_digest": "a" * 64},
        months=[{"month": "2026-08", "batch_id": "synthetic", "status": "completed"}],
        batches=[{"id": "synthetic", "status": "completed", "content_hash": "b" * 64,
                  "raw_file_hash": "c" * 64, "published_state_token": "a" * 64}])


class FinanceSourceTests(TestCase):
    def test_real_import_projection_preserves_all_selected_scalar_fields(self):
        records = []
        for raw in finance_month("2026-08")["lines"]:
            if raw["scopeType"] == "business":
                item = PROJECT(raw, "synthetic"); item.pop("created_at")
                records.append({"id": len(records) + 1, **item})
        result = source.build(**fixture(records))
        for actual, expected in zip(result.page()["rows"], records):
            self.assertEqual({k: actual[k] for k in source.ROW_FIELDS}, expected)
        metrics = result.manifest["coverage"][0]["metrics"]
        self.assertEqual(metrics["net_sales"]["value"], 100_000)
        self.assertEqual(metrics["return_amount"]["value"], -20_000)
        self.assertEqual(metrics["selling_expense_total"]["value"], 10_000)
        self.assertEqual(metrics["gross_margin"]["status"], "missing_subject")
        self.assertFalse(result.manifest["businessCoverageVerified"])
        self.assertFalse(result.manifest["sourceAuthorityVerified"])

    def test_zero_null_absence_text_and_ambiguous_are_distinct(self):
        for amount, kind, expected in ((0, "amount", "present"), (None, "amount", "missing_value"), (None, "text", "non_numeric")):
            item = row(); item.update(amount_cents=amount, value_type=kind)
            m = source.build(**fixture([item])).manifest["coverage"][0]["metrics"]
            self.assertEqual(m["net_sales"]["status"], expected)
            self.assertEqual(m["gross_sales"]["status"], "missing_subject")
        first, second = row(), row(2, subject_name="另一净销售科目")
        m = source.build(**fixture([first, second])).manifest["coverage"][0]["metrics"]["net_sales"]
        self.assertEqual(m, {"status": "ambiguous_subject", "value": None, "unit": "CNY_cent"})

    def test_rates_not_summed_and_profit_not_derived(self):
        item = row(); item.update(metric_key="gross_margin", subject_name="大毛利率", amount_cents=None, rate_bps=0, value_type="rate")
        result = source.build(**fixture([item])).manifest["coverage"][0]["metrics"]
        self.assertEqual(result["gross_margin"]["value"], 0)
        self.assertIsNone(result["profit"]["value"])

    def test_missing_month_and_present_month_without_scope_are_different(self):
        args = fixture([]); args["query"]["months"] = ["2026-08", "2026-09"]
        result = source.build(**args)
        self.assertEqual(result.page()["rows"], [])
        self.assertIsNone(result.page()["pagination"]["nextOffset"])
        coverage = result.manifest["coverage"]
        self.assertEqual([c["published"] for c in coverage], [True, False])
        self.assertEqual(coverage[0]["metrics"]["net_sales"]["status"], "missing_subject")
        self.assertEqual(coverage[1]["metrics"]["net_sales"]["status"], "missing_month")
        self.assertFalse(result.manifest["allRequestedMonthsPublished"])

    def test_period_alignment_leap_day_no_proration(self):
        args = fixture([]); args["query"]["months"] = ["2024-02"]
        args["months"][0]["month"] = "2024-02"
        for first, last, status in (("2024-02-01", "2024-02-29", "exact_full_months"),
                                    ("2024-02-02", "2024-02-29", "different_or_partial_months"),
                                    ("2024-01-20", "2024-02-20", "different_or_partial_months")):
            actual = source.build(**args, analysis_period={"startDate": first, "endDate": last}).manifest["periodAlignment"]
            self.assertEqual(actual["alignment"], status)
            self.assertFalse(actual["dailyProrationAllowed"])
        with self.assertRaises(AnalysisContractError):
            source.build(**args, analysis_period={"startDate": "2023-02-29", "endDate": "2024-02-29"})

    def test_unknown_and_non_contiguous_months_reject(self):
        for months in (["2026-8"], ["2026-08", "2026-08"], ["2026-08", "2026-10"], ["2026-09", "2026-08"], []):
            args = fixture(); args["query"]["months"] = months
            with self.assertRaises(AnalysisContractError): source.build(**args)
        args = fixture(); args["query"]["assumeDaily"] = True
        with self.assertRaises(AnalysisContractError): source.build(**args)

    def test_scope_section_and_metric_roles_never_mix(self):
        for key, value in (("scope_name", "另店"), ("group_name", "另平台"), ("scope_key", "另scope"), ("scope_type", "shop")):
            item = row(); item[key] = value
            with self.assertRaises(AnalysisContractError): source.build(**fixture([item]))
        item = row(); item.update(section="kingdee", is_total=True)
        result = source.build(**fixture([item]))
        self.assertEqual(result.manifest["coverage"][0]["metrics"]["net_sales"]["status"], "missing_subject")
        self.assertTrue(result.page()["rows"][0]["is_total"])

    def test_late_duplicate_or_unknown_month_fails_entire_build(self):
        for last in (row(), dict(row(2), month="2026-09"), dict(row(2), extra="x")):
            seen = []
            def stream():
                seen.append(1); yield row()
                seen.append(2); yield last
            with self.assertRaises(AnalysisContractError): source.build(**fixture(stream()))
            self.assertEqual(seen, [1, 2])

    def test_summary_and_kingdee_same_subject_are_separate_evidence(self):
        summary = row()
        kingdee = dict(row(2), section="kingdee", amount_cents=700, is_total=True)
        result = source.build(**fixture([summary, kingdee]))
        self.assertEqual(result.manifest["coverage"][0]["metrics"]["net_sales"]["value"], 100_000)
        self.assertEqual([r["amount_cents"] for r in result.page()["rows"]], [100_000, 700])
        self.assertEqual(len({r["rowId"] for r in result.page()["rows"]}), 2)

    def test_import_merged_values_remain_ledger_values_not_reconstructed(self):
        item = dict(row(), scope_type="shop", scope_key="shop:同名店", scope_name="同名店",
                    group_name="甲组", source_row_count=2, metric_key="gross_margin",
                    subject_name="大毛利率", amount_cents=None, rate_bps=6000, value_type="rate")
        args = fixture([item]); args["query"]["scope"] = {k: item[k] for k in source.SCOPE_FIELDS}
        result = source.build(**args)
        self.assertEqual(result.page()["rows"][0]["source_row_count"], 2)
        self.assertEqual(result.manifest["coverage"][0]["metrics"]["gross_margin"]["value"], 6000)
        self.assertFalse(result.manifest["businessCoverageVerified"])
        args["query"]["scope"]["group_name"] = "乙组"
        with self.assertRaises(AnalysisContractError): source.build(**args)

    def test_empty_page_still_obeys_complete_envelope_byte_limit(self):
        with patch.object(source, "MAX_PAGE_BYTES", 100), self.assertRaises(AnalysisContractError):
            source.build(**fixture([]))

    def test_single_pass_and_complete_row_prefix_pages(self):
        class Once:
            used = False
            def __iter__(self):
                if self.used: raise AssertionError("second scan")
                self.used = True
                for index in range(1, 104): yield row(index, subject_name=f"科目{index}")
        result = source.build(**fixture(Once()))
        first, second = result.page(), result.page(100)
        self.assertEqual(first["pagination"]["nextOffset"], 100)
        self.assertEqual(second["pagination"]["returned"], 3)
        self.assertIsNone(second["pagination"]["nextOffset"])
        for invalid in (True, 1.0, -1, 99, 103, "100"):
            with self.assertRaises(AnalysisContractError): result.page(invalid)

    def test_unicode_escaping_byte_limit_real_prefix_and_digest(self):
        records = [dict(row(i, subject_name=str(i)), raw_value="中\"\\" * 1000) for i in range(1, 33)]
        result = source.build(**fixture(records))
        offset, found = 0, []
        while True:
            page = result.page(offset); found.extend(page["rows"])
            self.assertLessEqual(len(canonical(page).encode("utf-8")), source.MAX_PAGE_BYTES)
            expected = page.pop("pageDigest"); self.assertEqual(expected, digest(page))
            offset = page["pagination"]["nextOffset"]
            if offset is None: break
        self.assertEqual([r["id"] for r in found], list(range(1, 33)))
        self.assertLess(result.page()["pagination"]["returned"], 32)
        chain = "0" * 64
        for record in found: chain = digest({"previous": chain, "row": record})
        manifest = result.manifest
        self.assertEqual(manifest["rowChainDigest"], chain)
        expected = manifest.pop("sourceDigest")
        self.assertEqual(expected, digest(manifest))

    def test_limits_fail_closed_without_returning_manifest(self):
        args = fixture([row(1, subject_name="a"), row(2, subject_name="b")])
        with patch.object(source, "MAX_ROWS", 1), self.assertRaises(AnalysisContractError): source.build(**args)
        with patch.object(source, "MAX_SOURCE_BYTES", 100), self.assertRaises(AnalysisContractError): source.build(**args)
        with patch.object(source, "MAX_PAGE_BYTES", 100), self.assertRaises(AnalysisContractError): source.build(**args)

    def test_batch_revision_and_input_changes_bind_digests(self):
        base = source.build(**fixture()).manifest["sourceDigest"]
        for place, key, value in (("revision", "revision", 8), ("revision", "source_digest", "d" * 64),
                                  ("batch", "content_hash", "d" * 64), ("row", "amount_cents", -1)):
            args = fixture(); (args["batches"][0] if place == "batch" else args["rows"][0] if place == "row" else args[place])[key] = value
            self.assertNotEqual(source.build(**args).manifest["sourceDigest"], base)
        args = fixture(); args["months"][0]["batch_id"] = "missing"
        with self.assertRaises(AnalysisContractError): source.build(**args)
        args = fixture(); args["batches"][0]["status"] = "processing"
        with self.assertRaises(AnalysisContractError): source.build(**args)

    def test_strict_types_nested_attacks_and_integer_boundaries(self):
        for key, values in {"amount_cents": [True, 1.0, "1", 2**53, {}],
                            "id": [True, 0, []], "is_total": [1, None],
                            "source_row_count": [0, True], "raw_value": [{"nested": {"nested": {}}}], "metric_key": [None]}.items():
            for value in values:
                item = row(); item[key] = value
                with self.assertRaises(AnalysisContractError): source.build(**fixture([item]))
        for number in (-source.MAX_SAFE_INTEGER, source.MAX_SAFE_INTEGER):
            item = row(); item["amount_cents"] = number
            self.assertEqual(source.build(**fixture([item])).page()["rows"][0]["amount_cents"], number)

    def test_mutable_aliases_not_retained_or_returned(self):
        args = fixture(); result = source.build(**args)
        before = result.manifest; page = result.page()
        args["query"]["scope"]["scope_name"] = "改名"
        args["rows"][0]["amount_cents"] = 8
        page["rows"][0]["amount_cents"] = 7
        copied = result.manifest; copied["coverage"].clear()
        self.assertEqual(result.manifest, before)
        self.assertEqual(result.page()["rows"][0]["amount_cents"], 100_000)

    def test_metadata_snapshotted_before_generator_runs(self):
        args = fixture()
        def rows():
            args["batches"][0]["content_hash"] = "d" * 64
            args["query"]["months"][0] = "2026-09"
            yield row()
        args["rows"] = rows()
        result = source.build(**args)
        self.assertEqual(result.manifest["batches"][0]["content_hash"], "b" * 64)
        self.assertEqual(result.manifest["query"]["months"], ["2026-08"])
