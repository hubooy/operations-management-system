"""Synthetic finance identity diagnostics; no database or authority claim."""
from collections import defaultdict
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from django.db import connection
from django.test import TestCase as DjangoTestCase
from django.test.utils import CaptureQueriesContext

from finance.shop_mapping_diagnostic import inspect, project
from finance.errors import FinanceApiError
from finance.tests.factories import finance_month
from finance.tests.test_business_analysis_source import FinanceBusinessSourceTests
from access_control.models import AppUser


REVISION = {"revision": 7, "source_digest": "a" * 64}


def metadata(month):
    return ([{"month": month, "batch_id": "batch-" + month,
        "status": "completed"}], [{"id": "batch-" + month,
        "status": "completed", "content_hash": "b" * 64,
        "published_state_token": "c" * 64}])


def groups(lines):
    cells = defaultdict(list)
    for line in lines:
        if line["scopeType"] == "shop":
            key = (line["month"], line["scopeKey"], line["scopeName"],
                line["groupName"])
            cells[key].append(line)
    return [{"month": key[0], "scope_key": key[1],
        "scope_name": key[2], "group_name": key[3],
        "line_count": len(rows),
        "max_source_row_count": max(row["sourceRowCount"] for row in rows)}
        for key, rows in cells.items()]


class FinanceShopMappingDiagnosticTests(TestCase):
    def test_same_name_two_platform_groups_stays_ambiguous(self):
        month = finance_month("2026-08")
        published, batches = metadata("2026-08")
        result = project(["2026-08"], published, batches,
            groups(month["lines"]), REVISION)
        self.assertEqual(result["candidateCount"], 2)
        self.assertEqual({row["platformCandidate"] for row in
            result["candidates"]}, {"京东", "天猫"})
        self.assertTrue(all("same_name_multiple_finance_scopes_or_groups"
            in row["ambiguityFlags"] for row in result["candidates"]))
        self.assertTrue(all(row["netshopStableIdentity"] is None
            for row in result["candidates"]))
        self.assertFalse(result["financeShopMappingVerified"])
        self.assertFalse(result["mappingAuthorityVerified"])

    def test_missing_month_and_possible_pre_normalization_merge(self):
        month = finance_month("2026-08")
        lines = deepcopy(month["lines"])
        next(row for row in lines if row["scopeType"] == "shop"
            and row["groupName"] == "京东")["sourceRowCount"] = 2
        published, batches = metadata("2026-08")
        result = project(["2026-08", "2026-09"], published, batches,
            groups(lines), REVISION)
        self.assertEqual(result["missingMonthCount"], 1)
        self.assertEqual(result["months"][1]["status"], "missing_month")
        jd = next(row for row in result["candidates"]
            if row["platformCandidate"] == "京东")
        self.assertIn("possible_pre_normalization_merge",
            jd["ambiguityFlags"])
        self.assertFalse(result["sourcePreMergeAmbiguityRecoverable"])

    def test_one_scope_key_in_two_groups_and_unknown_platform(self):
        published, batches = metadata("2026-08")
        items = [{"month": "2026-08", "scope_key": "shop:同名店",
            "scope_name": "同名店", "group_name": group,
            "line_count": 1, "max_source_row_count": 1}
            for group in ("甲组", "乙组")]
        result = project(["2026-08"], published, batches, items, REVISION)
        self.assertEqual(result["ambiguousCandidateCount"], 2)
        for row in result["candidates"]:
            self.assertIsNone(row["platformCandidate"])
            self.assertIn("scope_key_multiple_names_or_groups",
                row["ambiguityFlags"])
            self.assertIn("platform_unknown", row["ambiguityFlags"])

    def test_incomplete_publication_does_not_expose_as_completed(self):
        published, batches = metadata("2026-08")
        batches[0]["status"] = "processing"
        result = project(["2026-08"], published, batches, [], REVISION)
        self.assertEqual(result["months"][0]["status"],
            "incomplete_publication")
        with self.assertRaises(FinanceApiError):
            project(["2026-08"], published, batches, [{
                "month": "2026-08", "scope_key": "shop:店A",
                "scope_name": "店A", "group_name": "京东",
                "line_count": 1, "max_source_row_count": 1}], REVISION)

    def test_duplicate_or_truncated_groups_fail_closed(self):
        published, batches = metadata("2026-08")
        item = {"month": "2026-08", "scope_key": "shop:店A",
            "scope_name": "店A", "group_name": "京东",
            "line_count": 1, "max_source_row_count": 1}
        with self.assertRaises(FinanceApiError):
            project(["2026-08"], published, batches, [item, item], REVISION)
        with patch("finance.shop_mapping_diagnostic.MAX_CANDIDATES", 1), \
                self.assertRaises(FinanceApiError):
            project(["2026-08"], published, batches, [item, {
                **item, "scope_key": "shop:店B", "scope_name": "店B"}],
                REVISION)
        with self.assertRaises(FinanceApiError):
            project(["2026-08"], published, batches, [
                {**item, "line_count": 60_000},
                {**item, "scope_key": "shop:店B", "scope_name": "店B",
                    "line_count": 60_000}], REVISION)
        with self.assertRaises(FinanceApiError):
            project(["2026-09", "2026-08"], published, batches,
                [item], REVISION)

    def test_inspect_closed_before_any_database_read(self):
        with patch("finance.shop_mapping_diagnostic.owner._actor") as actor:
            with self.assertRaises(FinanceApiError):
                inspect(object(), ["2026-08"])
            actor.assert_not_called()


class FinanceShopMappingOwningTests(DjangoTestCase):
    setUp = FinanceBusinessSourceTests.setUp

    def test_imported_month_is_read_without_amounts_or_writes(self):
        with CaptureQueriesContext(connection) as captured:
            result = inspect(self.principal, ["2026-08"], enabled=True)
        self.assertEqual(result["months"][0]["status"], "completed_metadata")
        self.assertGreaterEqual(result["candidateCount"], 1)
        self.assertFalse(result["financeShopMappingVerified"])
        self.assertTrue(all(item["netshopStableIdentity"] is None
            for item in result["candidates"]))
        sql = "\n".join(item["sql"].lower() for item in captured)
        for field in ("amount_cents", "rate_bps", "raw_value"):
            self.assertNotIn(field, sql)
        self.assertTrue(all(item["sql"].lstrip().upper().startswith("SELECT")
            for item in captured))

    def test_account_revocation_before_return_blocks_diagnostic(self):
        from finance import shop_mapping_diagnostic as diagnostic
        original = diagnostic.project

        def revoke(*args):
            result = original(*args)
            AppUser.objects.filter(email=self.principal.email).update(
                status="disabled")
            return result

        with patch.object(diagnostic, "project", side_effect=revoke):
            with self.assertRaises(FinanceApiError):
                inspect(self.principal, ["2026-08"], enabled=True)
