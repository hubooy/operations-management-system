"""Synthetic immutable-ledger projections; no database or source calls."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from business_analysis.contracts import PageReconciler, comparison_periods, digest as fact_digest
from business_analysis import evidence_v2
from . import business_sealed as sealed
from .policy import AiError, canonical, digest


def fixture(domain="sales", *, master=False):
    query = {"platform": "京东", "startDate": "2026-08-01", "endDate": "2026-08-02"}
    if domain == "market":
        query.update(category="合成类目", scope="POP", rankingDimension="SKU", priceBandFilter="全部")
    else:
        query.update(shop="合成店", **({"channel": "直营"} if domain == "sales" else {"dataset": "master" if master else "promotion"}))
    source = {"key": "source", "domain": domain, "query": query}
    filters = {**query, "window": "current", "periods": comparison_periods(query["startDate"], query["endDate"])}
    if domain != "sales":
        del filters["startDate"], filters["endDate"]
    if domain != "netshop":
        filters["limit"] = 10
    if domain == "market":
        filters["shop"] = ""
    pages = []
    for index in range(2):
        row = {"rowId": str(index+1), "platform": "京东", "shopName": "" if domain == "market" else "合成店",
            "date": "2026-08-01", "metrics": {} if master else {"spendCents": 100}, "skuId": "SKU", "spuId": None}
        if domain == "sales":
            row["channel"] = "直营"
        if domain == "market":
            row.update(category=query["category"], dimensions={"marketScope": query["scope"]})
        if master:
            row.update(batchId="batch", snapshotDate="2026-09-01", date=None)
        page = {"schemaVersion": "business-analysis-v1", "sourceRef": "fixed-source-ref", "sourceRevision": "rev1",
            "filters": deepcopy(filters), "source": "erp_sales" if domain == "sales" else "market_daily_top" if domain == "market" else "jd_product_master" if master else "jd_promotion",
            "monetaryUnit": "CNY_CENT", "metricSemantics": {"meaning": "synthetic"},
            "coverage": ({"status": "current_master", "batchId": "batch", "snapshotDate": "2026-09-01", "historicalMapping": False} if master else {"status": "dates_present"}) if index == 0 else None,
            "control": {"rowCount": 2, "typedTotals": {} if master else {"spendCents": 200}} if index == 0 else None,
            "items": [row], "pageEvidence": {"rowCount": 1, "sha256": fact_digest([row])},
            "pagination": {"limit": 10, "hasMore": index == 0, "nextCursor": "next" if index == 0 else None}}
        if domain != "sales":
            page["sourceDataset"] = "market_daily_top" if domain == "market" else "product_master" if master else "ad"
        pages.append(page)
    return source, pages


class BusinessSealedTests(TestCase):
    def setUp(self):
        self.principal = SimpleNamespace(email="owner@example.invalid", role="admin", scope=None)
        self.chunks = []
        manager = MagicMock()
        manager.filter.return_value.order_by.return_value.iterator.side_effect = lambda **kw: iter(self.chunks)
        self.current = patch.object(sealed.store, "assert_current").start()
        self.addCleanup(patch.stopall)
        patch.object(sealed, "current_principal", return_value=self.principal).start()
        patch.object(sealed.m.AiBusinessEvidenceChunk, "objects", manager).start()

    def reader(self, source, pages, *, v2=False):
        verifier = PageReconciler()
        self.chunks = []
        for index, page in enumerate(pages):
            if v2:
                page["pagination"]["limit"] = 100
                if source["domain"] != "netshop":
                    page["filters"]["limit"] = 100
            # Trusted-state construction intentionally permits forged identities:
            # PageReconciler alone would certify them; Reader must reject them.
            page["pageEvidence"]["sha256"] = fact_digest(page["items"])
            verifier.consume(page, request_cursor=verifier.expected_cursor)
            encoded = canonical(page)
            self.chunks.append(SimpleNamespace(sequence=index+1, payload_json=encoded, payload_digest=digest(encoded)))
        metadata = {key: pages[0].get(key) for key in ("sourceRevision", *sealed.METADATA_FIELDS)}
        metadata.update(freshness={"status": "synthetic"}, firstCollectedAt="fixed", lastCollectedAt="fixed")
        self.entry = {"metadata": metadata, "verifier": deepcopy(verifier.__dict__), "pageCount": len(pages)}
        self.row = SimpleNamespace(id="evidence-fixed", owner_email=self.principal.email, scope_json="null", status="sealed", version=4,
            plan_json=canonical({"schemaVersion": "business-evidence-v1", "sources": [source]}),
            state_json=canonical({source["key"]: self.entry}), stored_bytes=sum(len(c.payload_json.encode()) for c in self.chunks))
        if v2:
            self.row.plan_json = canonical(evidence_v2.build_catalog([source])["header"])
            self.row.state_json = "{}"
            patch.object(sealed.store, "verify_seal").start()
            patch.object(sealed.store, "catalog", return_value=[deepcopy(source)]).start()
            patch.object(sealed.store, "source_record", return_value=SimpleNamespace(checkpoint_run_version=3, stored_bytes=self.row.stored_bytes)).start()
            patch.object(sealed.store, "checkpoint", return_value=deepcopy(self.entry)).start()
        return sealed.Reader(self.row, self.principal)

    def test_all_domains_preserve_pages_metadata_and_expected_bytes(self):
        for domain, master in (("sales", False), ("netshop", False), ("netshop", True), ("market", False)):
            with self.subTest(domain=domain, master=master):
                source, pages = fixture(domain, master=master)
                reader = self.reader(source, pages)
                before = (self.row.plan_json, self.row.state_json)
                expected = PageReconciler(); expected.__dict__.update(self.entry["verifier"])
                self.assertEqual(canonical(reader.info("source")), canonical({"metadata": self.entry["metadata"], "expected": expected.result(), "pageCount": 2}))
                self.assertEqual(canonical(list(reader.pages("source"))), canonical(pages))
                self.assertEqual(canonical(list(reader.pages("source"))), canonical(pages))
                self.assertEqual((self.row.plan_json, self.row.state_json), before)

    def test_v2_binding_and_fact_byte_total(self):
        source, pages = fixture("market")
        reader = self.reader(source, pages, v2=True)
        self.assertEqual(list(reader.pages("source")), pages)
        reader._bytes["source"] += 1
        with self.assertRaises(AiError): list(reader.pages("source"))

    def test_rehashed_cross_shop_channel_market_and_date_are_rejected(self):
        cases = [("sales", lambda p: p["items"][0].update(shopName="其他店")),
            ("sales", lambda p: p["items"][0].update(channel="批发")),
            ("sales", lambda p: p["items"][0].update(date="2025-08-01")),
            ("sales", lambda p: p["filters"].update(shop="其他店")),
            ("netshop", lambda p: p.update(sourceDataset="b2b")),
            ("market", lambda p: p["items"][0].update(shopName="用户店")),
            ("market", lambda p: p["filters"].update(shop="用户店")),
            ("market", lambda p: p["items"][0].update(category="其他类目")),
            ("market", lambda p: p["items"][0]["dimensions"].update(marketScope="其他范围")),
            ("market", lambda p: p["items"][0].update(spuId="SPU"))]
        for domain, change in cases:
            with self.subTest(domain=domain, change=change):
                source, pages = fixture(domain)
                change(pages[0])
                reader = self.reader(source, pages)
                with self.assertRaises(AiError): list(reader.pages("source"))

    def test_metadata_revision_and_window_cannot_change_midstream(self):
        for change in (lambda p: p.update(sourceRevision="rev2"), lambda p: p.update(metricSemantics={"wrong": True}),
                       lambda p: p["filters"].update(window="yearAgo"), lambda p: p.update(coverage={"status": "changed"}),
                       lambda p: p["filters"].update(unknown="extra"),
                       lambda p: p["pagination"].update(limit=True)):
            source, pages = fixture()
            change(pages[1])
            reader = self.reader(source, pages)
            stream = reader.pages("source")
            next(stream)
            with self.assertRaises(AiError): list(stream)

    def test_missing_duplicate_reordered_extra_and_digest_bad_chunks_fail(self):
        for mode in ("missing", "duplicate", "reordered", "extra", "digest"):
            source, pages = fixture()
            reader = self.reader(source, pages)
            if mode == "missing": self.chunks.pop()
            if mode == "duplicate": self.chunks[1] = self.chunks[0]
            if mode == "reordered": self.chunks.reverse()
            if mode == "extra": self.chunks.append(self.chunks[-1])
            if mode == "digest": self.chunks[-1].payload_digest = "0"*64
            with self.subTest(mode=mode), self.assertRaises(AiError): list(reader.pages("source"))

    def test_final_reconciliation_and_optimistic_fence_are_required(self):
        source, pages = fixture()
        reader = self.reader(source, pages)
        reader.info("source")
        self.current.side_effect = AiError("version changed", "version_conflict", 409)
        stream = reader.pages("source")
        next(stream); next(stream)
        with self.assertRaises(AiError): next(stream)
        self.current.side_effect = None
        source, pages = fixture()
        reader = self.reader(source, pages)
        reader.info("source")
        reader._infos["source"]["expected"]["rowCount"] = True
        with self.assertRaises(AiError): list(reader.pages("source"))

    def test_public_copies_and_orm_alias_cannot_change_trusted_bindings(self):
        source, pages = fixture()
        reader = self.reader(source, pages)
        reader.sources[0]["query"]["shop"] = "其他店"
        reader.info("source")["metadata"]["sourceRevision"] = "wrong"
        self.row.version = 999
        self.row.plan_json = "{}"
        self.assertEqual(list(reader.pages("source")), pages)
        self.assertEqual(reader._row.version, 4)
        with self.assertRaises(AiError): reader.info("missing")

    def test_owner_sealed_and_scope_checks_apply_even_with_supplied_row(self):
        source, pages = fixture()
        self.reader(source, pages)
        self.row.owner_email = "other@example.invalid"
        with self.assertRaises(AiError): sealed.Reader(self.row, self.principal)
        self.row.owner_email = self.principal.email
        self.row.status = "collecting"
        with self.assertRaises(AiError): sealed.Reader(self.row, self.principal)
        self.row.status = "sealed"
        restricted = SimpleNamespace(email=self.principal.email, scope={"shop": ["other"]})
        with self.assertRaises(AiError): sealed.Reader(self.row, restricted)

    def test_checkpoint_callback_shape_and_exception_are_not_silenced(self):
        source, pages = fixture()
        reader = self.reader(source, pages)
        calls = []
        list(reader.pages("source", calls.append))
        self.assertEqual(calls, [{"stage": "preparing", "sourceKey": "source", "sourcePage": 1}])
        def stop(progress): raise AiError("cancelled", "conflict", 409)
        with self.assertRaises(AiError): list(reader.pages("source", stop))

    def test_empty_source_and_master_snapshot_identity(self):
        source, pages = fixture("netshop", master=True)
        pages[0]["items"][0]["batchId"] = "other-batch"
        reader = self.reader(source, pages)
        with self.assertRaises(AiError): list(reader.pages("source"))
        source, pages = fixture("market")
        page = pages[0]
        page.update(items=[], control={"rowCount": 0, "typedTotals": {"spendCents": 0}},
            pageEvidence={"rowCount": 0, "sha256": fact_digest([])}, pagination={"limit": 10, "hasMore": False, "nextCursor": None})
        reader = self.reader(source, [page])
        self.assertEqual(list(reader.pages("source")), [page])

    def test_legacy_missing_window_is_current_without_page_rewrite(self):
        source, pages = fixture()
        for page in pages: page["filters"].pop("window")
        reader = self.reader(source, pages)
        self.assertEqual(list(reader.pages("source")), pages)
