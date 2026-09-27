"""Pure v4-capacity promotion relation over complete one-window page streams."""
from copy import deepcopy
import hashlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from . import promotion_relation_v4 as service
from . import test_promotion_views as native_fixture
from .contracts import AnalysisContractError, PageReconciler, canonical, digest


def fact(number, *, keyword="切肉机", search="搜索", promoted="P-SKU",
         trigger="T-SKU", attributed="A-SKU", spend=100, gmv=500):
    value = native_fixture.fact(spend=spend, gmv=gmv)
    value["raw"].update({"关键词": keyword, "搜索词": search,
        "推广SKU": promoted, "触发SKU ID": trigger,
        "跟单SKU ID": attributed})
    return value


def data(rows, *, window="current", early=False):
    source, pages, _ = native_fixture.fixture(rows, window=window, limit=100)
    if early:
        assert len(pages) == 1 and len(pages[0]["items"]) >= 3
        first, second = deepcopy(pages[0]), deepcopy(pages[0])
        first["items"], second["items"] = pages[0]["items"][:2], pages[0]["items"][2:]
        first["pagination"].update(hasMore=True, nextCursor="signed-early-cursor")
        second["pagination"].update(hasMore=False, nextCursor=None)
        second["control"] = None; second["coverage"] = None
        second["availableDates"] = None
        for page in (first, second):
            page["pageEvidence"] = {"rowCount":len(page["items"]),
                "sha256":digest(page["items"])}
        pages = [first, second]
    verifier = PageReconciler()
    for page in pages:
        verifier.consume(page, request_cursor=verifier.expected_cursor)
    proof = {"schemaVersion":service.REPLAY_SCHEMA,
        "runId":"synthetic-v4-run", "sourceId":"synthetic-v4-source",
        "runVersion":len(pages)+1,"sourceVersion":len(pages)+1,
        "sourceKey":source["key"], "queryDigest":digest(source["query"]),
        "sourceRef":pages[0]["sourceRef"],
        "sourceRevision":pages[0]["sourceRevision"],
        "receiptChainDigest":digest(["internal-tool-audit"]),
        "pageCount":len(pages), "rowCount":verifier.rows,
        "storedBytes":sum(len(canonical(page).encode("utf-8")) for page in pages),
        "reconciliation":verifier.result(), "coverage":pages[0]["coverage"],
        "fullSourceReplayVerified":True,"internalToolAuditBound":True,
        "upstreamSignatureVerified":False,"sealed":False,
        "reportGenerationSupported":False}
    proof["proofDigest"] = digest(proof)
    source = {key:source[key] for key in ("key","domain","query")}
    return source,pages,proof


class PromotionRelationV4Tests(TestCase):
    def test_true_early_page_null_identity_roles_and_nonadditive_views(self):
        source,pages,proof = data([fact(1),fact(2,attributed="OTHER",spend=200),
            fact(3,keyword=None,search=None,promoted=None,attributed="A-SKU",spend=300)],early=True)
        self.assertEqual(len(pages[0]["items"]),2)
        self.assertTrue(pages[0]["pagination"]["hasMore"])
        self.assertEqual(pages[0]["pagination"]["limit"],100)
        with service.prepare(source,iter(pages),proof) as prepared:
            header = prepared.manifest
            self.assertEqual(header["sourcePageCount"],2)
            self.assertEqual(header["sourceRowCount"],3)
            self.assertTrue(header["viewsAreNonAdditive"])
            self.assertFalse(header["sourceAuthorityVerified"])
            self.assertFalse(header["sealed"])
            tables = {view:list(prepared.scan(view)) for view in service.VIEWS}
            for view, rows in tables.items():
                self.assertEqual(sum(item["sourceFactCount"] for item in rows),3)
                self.assertEqual(sum(item["metrics"]["spendCents"]["value"] or 0
                    for item in rows),600)
                self.assertEqual(sum(item["metrics"]["reportedGmvCents"]["value"] or 0
                    for item in rows),1500)
            self.assertTrue(any(item["entity"].get("promotedSkuId") is None
                and "keyword" in item["missingIdentityFields"]
                and "searchTerm" in item["missingIdentityFields"]
                for item in tables["promoted_sku"]))
            self.assertTrue(any(item["entity"]["attributedSkuId"] == "OTHER"
                for item in tables["attributed_sku"]))
            self.assertFalse(any("attributedSkuId" in item["entity"]
                for item in tables["promoted_sku"]))
            self.assertEqual({spec["metrics"]["spendCents"]["value"]
                for spec in header["tables"]},{600})

    def test_each_period_runs_separately_and_mixed_pages_fail(self):
        records = [fact(1)]
        by_window = {window:data(records,window=window)
            for window in service.WINDOWS}
        for window, args in by_window.items():
            with service.prepare(*args) as prepared:
                self.assertEqual(prepared.manifest["window"],window)
                self.assertEqual({row["window"] for row in prepared.scan("full_relation")},
                    {window})
        current, previous = by_window["current"], by_window["previous"]
        with self.assertRaises(AnalysisContractError):
            with service.prepare(current[0], previous[1], current[2]):
                pass
        with self.assertRaises(AnalysisContractError):
            with service.prepare(current[0],current[1],previous[2]):
                pass

    def test_one_thousand_unique_rows_stream_chunks_and_conserve_every_view(self):
        rows = [fact(i,keyword=f"词-{i:04}",search=f"搜索-{i:04}",
            promoted=f"SKU-{i:04}") for i in range(1000)]
        source,pages,proof = data(rows)
        self.assertEqual(len(pages),10)
        with service.prepare(source,iter(pages),proof) as prepared:
            header = prepared.manifest
            self.assertEqual(header["sourceRowCount"],1000)
            self.assertEqual([spec["groupCount"] for spec in header["tables"]],
                [1000,1000,1000])
            for spec in header["tables"]:
                chunks = list(prepared.ndjson_chunks(spec["view"]))
                self.assertGreater(len(chunks),1)
                self.assertTrue(all(len(chunk) <= service.MAX_CHUNK_BYTES
                    for chunk in chunks))
                self.assertEqual(sum(len(chunk) for chunk in chunks),spec["ndjsonBytes"])
                self.assertEqual(hashlib.sha256(b"".join(chunks)).hexdigest(),
                    spec["ndjsonSha256"])
                self.assertEqual(spec["sourceFactCount"],1000)
                self.assertEqual(spec["metrics"]["spendCents"],
                    {"value":100000,"presentRows":1000,"missingRows":0})

    def test_duplicate_id_or_content_and_missing_tail_reject(self):
        source,pages,proof = data([fact(1),fact(2),fact(3)])
        duplicate_id = deepcopy(pages)
        duplicate_id[0]["items"][1]["rowId"] = duplicate_id[0]["items"][0]["rowId"]
        duplicate_id[0]["pageEvidence"]["sha256"] = digest(duplicate_id[0]["items"])
        with self.assertRaises(AnalysisContractError):
            with service.prepare(source,duplicate_id,proof):
                pass
        duplicate_hash = deepcopy(pages)
        duplicate_hash[0]["items"][1]["sourceRowHash"] = duplicate_hash[0]["items"][0]["sourceRowHash"]
        duplicate_hash[0]["pageEvidence"]["sha256"] = digest(duplicate_hash[0]["items"])
        verifier = PageReconciler(); verifier.consume(duplicate_hash[0])
        changed = deepcopy(proof)
        changed.update(reconciliation=verifier.result(),
            storedBytes=len(canonical(duplicate_hash[0]).encode("utf-8")))
        changed["proofDigest"] = digest({k:v for k,v in changed.items() if k != "proofDigest"})
        with self.assertRaisesRegex(AnalysisContractError,"重复"):
            with service.prepare(source,duplicate_hash,changed):
                pass
        source,pages,proof = data([fact(1),fact(2),fact(3)],early=True)
        with self.assertRaises(AnalysisContractError):
            with service.prepare(source,pages[:1],proof):
                pass

    def test_extra_metric_rejects_before_reconciler_accumulates_keys(self):
        source,pages,proof = data([fact(1)])
        tampered = deepcopy(pages)
        tampered[0]["items"][0]["metrics"]["unboundedMetric"] = 1
        tampered[0]["pageEvidence"]["sha256"] = digest(tampered[0]["items"])
        with patch.object(service.PageReconciler,"consume") as consume, \
                self.assertRaises(AnalysisContractError):
            with service.prepare(source,tampered,proof):
                pass
        consume.assert_not_called()

    def test_capacity_preflight_scratch_and_output_fail_instead_of_truncate(self):
        source,pages,proof = data([fact(1)])
        oversized = deepcopy(proof)
        oversized["rowCount"] = 575_095
        oversized["proofDigest"] = digest({k:v for k,v in oversized.items()
            if k != "proofDigest"})
        # 575095 is within the v4 physical row cap; a fake proof still fails
        # on actual complete replay, never on an old v2 200000-row limit.
        self.assertGreater(oversized["rowCount"],200000)
        self.assertLess(oversized["rowCount"],service.MAX_SOURCE_ROWS)
        with self.assertRaises(AnalysisContractError):
            with service.prepare(source,pages,oversized):
                pass
        with self.assertRaises(AnalysisContractError):
            with service.prepare(source,pages,proof,max_scratch_bytes=1):
                pass
        with self.assertRaises(AnalysisContractError):
            with service.prepare(source,pages,proof,max_output_bytes=1):
                pass

    def test_closed_context_rejects_suspended_scan(self):
        source,pages,proof = data([fact(1)])
        with service.prepare(source,pages,proof) as prepared:
            path = Path(prepared._db.execute("PRAGMA database_list").fetchone()[2])
            scan = prepared.scan("full_relation")
            self.assertIsNotNone(next(scan))
        self.assertFalse(path.exists())
        with self.assertRaises(AnalysisContractError):
            next(scan)
        with self.assertRaises(AnalysisContractError):
            prepared.manifest
