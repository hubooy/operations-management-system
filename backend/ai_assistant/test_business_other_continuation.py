"""Sales/market v2 continuation through real owning helpers and persisted AI pages.

Transport replacement is in-process; this is not signed cross-process HTTP proof.
"""
import copy
import json
import time
from contextlib import nullcontext
from unittest.mock import patch
from urllib.parse import urlencode
from django.core import signing
from django.http import QueryDict
from django.test import TestCase, override_settings
from access_control.models import AppUser
from sales import analysis as sales_analysis, analysis_continuation as sales_continuation
from sales.models import SalesDataRevision
from sales.tests.factories import make_line
from market import analysis as market_analysis, analysis_continuation as market_continuation
from market.errors import MarketApiError
from market.models import MarketDataRevision, MarketRankingEntry
from . import business_evidence as evidence, business_evidence_store as store, business_collection as collection
from . import business_collection_continuation as continuation, models as m, test_business_evidence as fixtures
from .policy import AiError, uid

DOMAINS = {"sales": (sales_analysis, sales_continuation), "market": (market_analysis, market_continuation)}


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class OtherCollectionContinuationTests(TestCase):
    user = fixtures.BusinessEvidenceTests.user
    call = fixtures.BusinessEvidenceTests.call

    def setUp(self):
        fixtures.BusinessEvidenceTests.setUp(self)
        for domain in ("sales", "erp"):
            SalesDataRevision.objects.get_or_create(domain=domain, defaults={"revision": 1, "source_digest": "a"*64})
        for index in range(13, 116):
            make_line(index, f"continuation-sales-{index}", channel=self.query["channel"], online_spec_code="M1").save()
        MarketDataRevision.objects.update_or_create(domain="market", defaults={"revision": 7, "source_digest": "a"*64})
        MarketRankingEntry.objects.bulk_create([MarketRankingEntry(natural_key=f"continuation-market-{index}",
            source_row_number=index+1, period_start="2026-08-01", period_end="2026-08-01", category="饮水机", scope="POP",
            ranking_dimension="SKU", price_band_filter="全部", sku_code=f"SKU{index}", product_name="合成产品", brand="合成品牌",
            rank=index+1, gmv_cents=987654, gmv_low_cents=100, gmv_high_cents=200, quantity_low=1, quantity_high=3,
            last_import_batch_id="synthetic") for index in range(105)])
        self.queries = {"sales": self.query, "market": {"platform": "京东", "category": "饮水机", "scope": "POP",
            "rankingDimension": "SKU", "priceBandFilter": "全部", "startDate": "2026-08-01", "endDate": "2026-08-01"}}
        self.tools = [{**self.catalog[0], "name": name} for name in (
            "get_data_freshness", "get_business_source_page", *continuation.TOOLS.values())]
        self.calls = []
        self.expired = True

    def start(self, domain):
        self.domain = domain
        self.run_id = evidence.create({"schemaVersion": "business-evidence-v2", "clientRequestId": uid("continue"),
            "sources": [{"key": domain, "domain": domain, "query": self.queries[domain]}],
            "collectionMode": "bulk", "autoCollect": True}, self.admin)["item"]["id"]
        return self.run_id

    def execute(self, name, arguments, principal, **kwargs):
        self.calls.append((name, copy.deepcopy(arguments)))
        self.assertEqual(kwargs["surface"], "business_collection")
        if name == "get_data_freshness":
            data = {"dataCutoffDate": "2026-08-01"}
        elif name in continuation.TOOLS.values():
            self.assertEqual(name, continuation.TOOLS[self.domain])
            self.assertNotIn("domain", arguments)
            data = DOMAINS[self.domain][1].read_page(principal, QueryDict(urlencode(arguments)))
        else:
            self.assertEqual(name, "get_business_source_page")
            self.assertEqual(arguments["domain"], self.domain)
            query = {key: value for key, value in arguments.items() if key != "domain"}
            clock = patch.object(signing.TimestampSigner, "timestamp", return_value=signing.b62_encode(int(time.time())-7200)) if self.expired else nullcontext()
            with clock:
                data = DOMAINS[self.domain][0].read_page(principal, {"operation": "analysis_records", **query})
        return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}

    def collect(self, execute=None, commit=None, tools=None):
        row = evidence.get_run(self.run_id, self.admin)
        with patch("ai_assistant.transport.catalog", return_value=self.tools if tools is None else tools), patch(
                "ai_assistant.transport.execute_tool", side_effect=execute or self.execute):
            return evidence.collect(row.id, {"sourceKey": self.domain, "expectedVersion": row.version}, self.admin,
                "other-continuation", commit=commit)

    def source(self):
        return store.source_record(evidence.get_run(self.run_id, self.admin), self.domain)

    def test_fresh_and_expired_pages_both_seal_preserving_original_ledger(self):
        for domain in DOMAINS:
            for expired in (False, True):
                with self.subTest(domain=domain, expired=expired):
                    self.expired = expired
                    self.start(domain); self.collect()
                    first = m.AiBusinessEvidenceChunk.objects.get(run_id=self.run_id, sequence=1)
                    raw = (first.payload_json, first.payload_digest)
                    cursor = json.loads(first.payload_json)["pagination"]["nextCursor"]
                    if expired:
                        with self.assertRaises(signing.SignatureExpired): signing.loads(cursor, salt=DOMAINS[domain][0].SALT, max_age=3600)
                    else: signing.loads(cursor, salt=DOMAINS[domain][0].SALT, max_age=3600)
                    self.collect()
                    row = evidence.get_run(self.run_id, self.admin)
                    evidence.finish(row.id, {"expectedVersion": row.version, "action": "seal"}, self.admin)
                    first.refresh_from_db()
                    self.assertEqual((first.payload_json, first.payload_digest), raw)
                    self.assertEqual(self.calls[-1][0], continuation.TOOLS[domain])
                    self.assertEqual(self.calls[-1][1]["cursor"], cursor)
                    self.assertEqual(self.calls[-1][1]["limit"], 100)
                    self.assertEqual((self.source().page_count, self.source().row_count), (2, 115 if domain == "sales" else 105))
        self.assertFalse(m.AiAgentProviderDispatches.objects.exists())

    def test_missing_owning_tool_fails_closed_after_homepage(self):
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                self.start(domain)
                tools = [entry for entry in self.tools if entry["name"] != continuation.TOOLS[domain]]
                self.collect(tools=tools)
                count = len(self.calls)
                with self.assertRaises(AiError) as caught: self.collect(tools=tools)
                self.assertEqual(caught.exception.code, "access_denied")
                self.assertEqual(len(self.calls), count)
                self.assertEqual(self.source().page_count, 1)

    def test_lost_read_response_then_resume_uses_same_old_cursor_once(self):
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                self.start(domain); self.collect()
                checkpoint = self.source().checkpoint_json
                def lost(*args, **kwargs):
                    self.execute(*args, **kwargs)
                    raise AiError("response lost", "service_unavailable", 503)
                with self.assertRaises(AiError): self.collect(lost)
                self.assertEqual(self.source().checkpoint_json, checkpoint)
                self.collect()
                self.assertEqual(self.calls[-1], self.calls[-2])
                self.assertEqual(self.source().page_count, 2)

    def test_pause_resume_tick_and_audit_failure_keep_old_checkpoint(self):
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                self.start(domain); self.collect()
                old = self.source().checkpoint_json
                def fail(*args): raise AiError("audit failure")
                with self.assertRaises(AiError): self.collect(commit=fail)
                self.assertEqual(self.source().checkpoint_json, old)
                row = evidence.get_run(self.run_id, self.admin)
                collection.control(row.id, {"action": "pause", "expectedVersion": row.version}, self.admin)
                row.refresh_from_db()
                collection.control(row.id, {"action": "resume", "expectedVersion": row.version}, self.admin)
                with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.transport.execute_tool", side_effect=self.execute):
                    for _ in range(3):
                        if evidence.get_run(self.run_id, self.admin).status == "sealed": break
                        result = collection.tick()
                        self.assertNotIn(result["status"], ("paused", "superseded"), result)
                self.assertEqual(evidence.get_run(self.run_id, self.admin).status, "sealed")

    def test_permission_version_aba_rejects_late_pages_in_both_domains(self):
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                self.start(domain); self.collect()
                def changed(*args, **kwargs):
                    data = self.execute(*args, **kwargs)
                    user = AppUser.objects.get(email=self.admin.email)
                    AppUser.objects.filter(pk=user.pk).update(status="disabled", version=user.version+1)
                    AppUser.objects.filter(pk=user.pk).update(status="active", version=user.version+2)
                    return data
                with self.assertRaises(AiError) as caught: self.collect(changed)
                self.assertEqual(caught.exception.code, "access_denied")
                self.assertEqual(self.source().page_count, 1)

    def test_changed_source_revision_never_appends_or_restarts(self):
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                self.start(domain); self.collect()
                original = self.source().checkpoint_json
                model = SalesDataRevision if domain == "sales" else MarketDataRevision
                row = model.objects.get(domain=domain)
                model.objects.filter(pk=row.pk).update(revision=row.revision+1)
                error = sales_continuation.ContinuationError if domain == "sales" else MarketApiError
                with self.assertRaises(error): self.collect()
                self.assertEqual(self.source().checkpoint_json, original)
                self.assertEqual(self.source().page_count, 1)
