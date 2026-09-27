"""Three SKU roles and complete keyword/search-term relation stay separate."""
from copy import deepcopy
import hashlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from . import promotion_relation_v2 as service
from . import test_promotion_views as fixtures
from .contracts import AnalysisContractError, PageReconciler, canonical, digest


def fact(*, keyword="切肉机", search="用户搜索", promoted="P-SKU",
         trigger="T-SKU", attributed="A-SKU", spend=100, gmv=500):
    row = fixtures.fact(spend=spend, gmv=gmv)
    row["raw"].update({"关键词": keyword, "搜索词": search,
        "推广SKU": promoted, "触发SKU ID": trigger,
        "跟单SKU ID": attributed})
    return row


def selected(current, previous=None, year_ago=None):
    return {"current": current, "previous": previous, "yearAgo": year_ago}


class PromotionRelationV2Tests(TestCase):
    def test_complete_relation_and_two_sku_views_are_distinct_nonadditive(self):
        source = fixtures.fixture([fact(), fact(attributed="OTHER", spend=200, gmv=800),
            fact(promoted=None, trigger="T-SKU", attributed="A-SKU", spend=300, gmv=900),
            fact(search=None, promoted="P-SKU", attributed="A-SKU", spend=400, gmv=1000)])
        with service.prepare(selected(source)) as value:
            header = value.manifest
            self.assertEqual(header["sourceTraversal"]["rows"], 4)
            self.assertEqual(header["sourceTraversal"]["pages"], 2)
            self.assertFalse(header["authorityVerified"])
            self.assertTrue(header["viewsAreNonAdditive"])
            groups = {view: list(value.scan("current", view)) for view in service.VIEWS}
            for view, rows in groups.items():
                self.assertEqual(sum(row["sourceFactCount"] for row in rows), 4)
                self.assertEqual(sum(row["metrics"]["spendCents"]["value"] or 0
                    for row in rows), 1000)
                self.assertEqual(sum(row["metrics"]["reportedGmvCents"]["value"] or 0
                    for row in rows), 3200)
            full = groups["full_relation"]
            self.assertTrue(any(row["entity"]["promotedSkuId"] == "P-SKU"
                and row["entity"]["triggerSkuId"] == "T-SKU"
                and row["entity"]["attributedSkuId"] == "OTHER" for row in full))
            self.assertTrue(any("searchTerm" in row["missingIdentityFields"] for row in full))
            promoted = groups["promoted_sku"]
            attributed = groups["attributed_sku"]
            self.assertTrue(any(row["entity"]["promotedSkuId"] is None
                and row["metrics"]["spendCents"]["value"] == 300 for row in promoted))
            self.assertTrue(any(row["entity"]["attributedSkuId"] == "A-SKU"
                for row in attributed))
            self.assertFalse(any("attributedSkuId" in row["entity"] for row in promoted))
            self.assertFalse(any("promotedSkuId" in row["entity"] for row in attributed))
            self.assertEqual({item["metrics"]["spendCents"]["value"]
                for item in header["tables"]}, {1000})
            spec = next(item for item in header["tables"] if item["window"] == "current"
                and item["view"] == "full_relation")
            encoded = b"".join((canonical(row)+"\n").encode("utf-8")
                for row in value.scan("current", "full_relation"))
            self.assertEqual(len(encoded), spec["ndjsonBytes"])
            self.assertEqual(hashlib.sha256(encoded).hexdigest(), spec["ndjsonSha256"])

    def test_current_previous_yearago_source_identity_and_no_implicit_baseline(self):
        current = fixtures.fixture([fact()], window="current")
        previous = fixtures.fixture([], window="previous")
        year_ago = fixtures.fixture([], window="yearAgo")
        with service.prepare(selected(current, previous, year_ago)) as value:
            header = value.manifest
            self.assertEqual(set(header["sources"]), set(service.WINDOWS))
            self.assertEqual([header["sources"][key]["source"]["query"]["window"]
                for key in service.WINDOWS], list(service.WINDOWS))
            self.assertEqual(len(list(value.scan("previous", "full_relation"))), 0)
            self.assertEqual(len(list(value.scan("yearAgo", "promoted_sku"))), 0)
        with service.prepare(selected(current)) as value:
            self.assertIsNone(value.manifest["sources"]["previous"])
            self.assertIsNone(value.manifest["sources"]["yearAgo"])
            with self.assertRaises(AnalysisContractError):
                list(value.scan("previous", "full_relation"))

    def test_duplicate_row_id_or_content_hash_reject_without_dedup(self):
        source, pages, expected = fixtures.fixture([fact(),fact(spend=200)], limit=2)
        repeated_id = deepcopy(pages)
        repeated_id[0]["items"][1]["rowId"] = repeated_id[0]["items"][0]["rowId"]
        repeated_id[0]["pageEvidence"]["sha256"] = digest(repeated_id[0]["items"])
        with self.assertRaises(AnalysisContractError):
            with service.prepare(selected((source, repeated_id, expected))):
                pass
        repeated_hash = deepcopy(pages)
        repeated_hash[0]["items"][1]["sourceRowHash"] = repeated_hash[0]["items"][0]["sourceRowHash"]
        repeated_hash[0]["pageEvidence"]["sha256"] = digest(repeated_hash[0]["items"])
        verifier = PageReconciler(); verifier.consume(repeated_hash[0])
        proof = verifier.result()
        rewritten_source = {**source, "evidenceDigest": proof["evidenceDigest"]}
        with self.assertRaisesRegex(AnalysisContractError, "重复源行/内容"):
            with service.prepare(selected((rewritten_source, repeated_hash, proof))):
                pass

    def test_cross_store_role_window_and_lexical_identity_reject(self):
        current = fixtures.fixture([fact()])
        for previous in (fixtures.fixture([fact()], window="previous", shop="另一店"),
                fixtures.fixture([fact()], window="previous", first="2026-08-02", last="2026-08-02")):
            with self.assertRaises(AnalysisContractError):
                with service.prepare(selected(current, previous)):
                    pass
        wrong = deepcopy(current)
        wrong[1][0]["items"][0]["dimensions"]["promotedSkuId"] = " ATTRIBUTED "
        wrong[1][0]["pageEvidence"]["sha256"] = digest(wrong[1][0]["items"])
        with self.assertRaises(AnalysisContractError):
            with service.prepare(selected(tuple(wrong))):
                pass
        paged = fixtures.fixture([fact(), fact(), fact()], limit=2)
        changed_limit = deepcopy(paged)
        changed_limit[1][1]["pagination"]["limit"] = 3
        with self.assertRaises(AnalysisContractError):
            with service.prepare(selected(tuple(changed_limit))):
                pass

    def test_reference_scale_capacity_preflight_does_not_read_or_truncate(self):
        source, _, expected = fixtures.fixture([fact()])
        too_large = deepcopy(expected)
        too_large["rowCount"] = 575_095
        def must_not_read():
            raise AssertionError("capacity preflight must precede source replay")
            yield
        with self.assertRaisesRegex(AnalysisContractError,
                "reference_scale_capacity_gap"):
            with service.prepare(selected((source, must_not_read(), too_large))):
                pass
        data = fixtures.fixture([fact()])
        with self.assertRaises(AnalysisContractError):
            with service.prepare(selected(data), max_scratch_bytes=1):
                pass
        with patch.object(service, "MAX_OUTPUT_BYTES", 1), self.assertRaises(AnalysisContractError):
            with service.prepare(selected(data)):
                pass

    def test_closed_context_and_half_scan_cleanup(self):
        with service.prepare(selected(fixtures.fixture([fact()]))) as value:
            suspended = value.scan("current", "full_relation")
            self.assertIsNotNone(next(suspended))
            path = Path(value._db.execute("PRAGMA database_list").fetchone()[2])
        self.assertFalse(path.exists())
        with self.assertRaises(AnalysisContractError):
            next(suspended)
        with self.assertRaises(AnalysisContractError):
            value.manifest
