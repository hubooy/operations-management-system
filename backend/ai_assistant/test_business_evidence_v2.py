"""V2 admission/storage tests; source readers are synthetic and models unused."""
import json
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import urlencode
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from sales.analysis import read_page
from sales.analysis_continuation import read_page as continuation_page
from sales.models import SalesDataRevision
from business_analysis.evidence_v2 import validate_directory_pages
from . import business_evidence as evidence, business_evidence_store as store, models as m
from . import test_business_evidence as fixtures
from .policy import AiError, canonical, digest, uid


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessEvidenceV2Tests(TestCase):
    user = fixtures.BusinessEvidenceTests.user
    call = fixtures.BusinessEvidenceTests.call
    def setUp(self):
        fixtures.BusinessEvidenceTests.setUp(self)
        for domain in ("sales", "erp"):
            SalesDataRevision.objects.get_or_create(domain=domain, defaults={"revision": 1, "source_digest": "a"*64})

    def request(self, count=1, **values):
        sources = [{"key": "sales" if i == 0 else f"sales-{i}", "domain": "sales",
            "query": {**self.query, **({"shop": f"合成店{i}", "channel": f"合成渠道{i}"} if i else {})}} for i in range(count)]
        return {"schemaVersion": "business-evidence-v2", "clientRequestId": uid("create"),
            "sources": sources, "collectionMode": "bulk", **values}

    def start(self, **values):
        return evidence.create(self.request(**values), self.admin)["item"]["id"]

    def execute(self, name, args, principal, **kwargs):
        self.assertEqual(kwargs["surface"], "business_collection")
        if name == "get_business_sales_continuation_page":
            self.assertNotIn("domain", args)
            return {"toolName": name, "ok": True, "auditStatus": "recorded",
                "data": continuation_page(principal, QueryDict(urlencode(args)))}
        data = {"dataCutoffDate": "2026-08-01"} if name == "get_data_freshness" else read_page(principal,
            {"operation": "analysis_records", **{k: v for k, v in args.items() if k != "domain"}})
        return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}

    def collect(self, run_id, version=1, *, execute=None, commit=None):
        catalog = [self.catalog[0], *[{**self.catalog[0], "name": name} for name in (
            "get_business_source_page", "get_business_sales_continuation_page")]]
        with patch("ai_assistant.transport.catalog", return_value=catalog), patch("ai_assistant.transport.execute_tool", side_effect=execute or self.execute):
            return evidence.collect(run_id, {"sourceKey": "sales", "expectedVersion": version}, self.admin, "v2-test", commit=commit)

    def test_large_catalog_create_paged_read_and_legacy_exact_replay(self):
        legacy = evidence.create(self.body, self.admin)["item"]
        old = m.AiBusinessEvidenceRun.objects.get(pk=legacy["id"])
        before = (old.plan_json, old.state_json, old.request_digest)
        for count in (16, 19, 35, 48):
            request = self.request(count)
            item = evidence.create(request, self.admin)["item"]
            row = evidence.get_run(item["id"], self.admin)
            self.assertEqual(item["progress"]["sourceCount"], count)
            self.assertNotIn("sources", item["plan"])
            self.assertEqual(row.state_json, "{}")
            self.assertLessEqual(len(row.plan_json.encode()), 16000)
            self.assertEqual(m.AiBusinessEvidenceSource.objects.filter(run=row).count(), count)
            self.assertTrue(evidence.create(request, self.admin)["replayed"])
            pages, offset = [], 0
            while offset is not None:
                page = evidence.directory(row.id, {"offset": str(offset), "limit": "20"}, self.admin)
                pages.append(page)
                self.assertLessEqual(len(canonical(page).encode()), 38000)
                offset = page["nextOffset"]
            validate_directory_pages(pages, request["sources"], run_id=row.id, evidence_version=1)
            evidence.finish(row.id, {"expectedVersion": 1, "action": "cancel"}, self.admin)
        self.assertTrue(evidence.create(self.body, self.admin)["replayed"])
        old.refresh_from_db()
        self.assertEqual((old.plan_json, old.state_json, old.request_digest), before)
        self.assertFalse(m.AiBusinessEvidenceSource.objects.filter(run=old).exists())

    def test_creation_rejects_invalid_scopes_and_rolls_back_partial_directory(self):
        for change in (self.request(49), self.request(collectionMode="standard"), self.request(analysisRequest=None)):
            with self.assertRaises(AiError):
                evidence.create(change, self.admin)
        request = self.request(2)
        request["sources"][1]["query"] = deepcopy(request["sources"][0]["query"])
        with self.assertRaises(AiError):
            evidence.create(request, self.admin)
        request = self.request(2)
        request["sources"][1]["query"]["startDate"] = "2026-07-31"
        with self.assertRaises(AiError):
            evidence.create(request, self.admin)
        manager = m.AiBusinessEvidenceSource.objects
        original = manager.create
        counter = [0]
        def fail_second(**values):
            counter[0] += 1
            if counter[0] == 2:
                raise AiError("synthetic insert failure")
            return original(**values)
        with patch.object(manager, "create", side_effect=fail_second), self.assertRaises(AiError):
            evidence.create(self.request(2), self.admin)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.count(), 0)
        self.assertEqual(m.AiBusinessEvidenceSource.objects.count(), 0)

    def test_new_identity_binds_auto_collect_and_owner_and_schema(self):
        request = self.request(autoCollect=True)
        run_id = evidence.create(request, self.admin)["item"]["id"]
        self.assertEqual(evidence.get_run(run_id, self.admin).collection_status, "queued")
        for body in ({**request, "autoCollect": False}, {**request, "schemaVersion": "business-evidence-v9"},
                     {k: v for k, v in request.items() if k != "schemaVersion"}):
            with self.assertRaises(AiError):
                evidence.create(body, self.admin)
        other = self.user("v2-other@example.invalid", "admin", None)
        with self.assertRaises(AiError):
            evidence.create({**self.request(), "expectedPrincipalKey": evidence.principal_key(self.admin)}, other)
        for principal in (other, self.viewer, self.owner):
            with self.assertRaises(AiError):
                evidence.directory(run_id, {}, principal)
            with self.assertRaises(AiError):
                evidence.source_detail(run_id, "sales", principal)

    def test_collect_seal_read_table_and_compact_discovery(self):
        run_id = self.start()
        with self.assertRaises(AiError):
            evidence.finish(run_id, {"expectedVersion": 1, "action": "seal"}, self.admin)
        item = self.collect(run_id)["item"]
        self.assertEqual(item["sources"]["sales"]["rowCount"], 12)
        self.assertNotIn("metadata", item["sources"]["sales"])
        row = evidence.get_run(run_id, self.admin)
        self.assertEqual(row.state_json, "{}")
        source = m.AiBusinessEvidenceSource.objects.get(run=row)
        self.assertEqual((source.version, source.checkpoint_run_version, source.page_count), (2, 2, 1))
        self.assertEqual(source.stored_bytes, row.stored_bytes)
        with self.assertRaises(AiError):
            self.collect(run_id)
        with patch("ai_assistant.transport.execute_tool") as remote:
            sealed = evidence.finish(run_id, {"expectedVersion": 2, "action": "seal"}, self.admin)["item"]
            self.assertEqual(sealed["seal"]["evidenceVersion"], 3)
            self.assertEqual(sealed["seal"]["sourceCount"], 1)
            detail = evidence.source_detail(run_id, "sales", self.admin)
            self.assertEqual(detail["reconciliation"]["rowCount"], 12)
            table = evidence.analysis_table(run_id, {"sourceKey": "sales", "dimension": "shop"}, self.admin)
            self.assertEqual(table["rows"][0]["metrics"]["netSalesCents"]["value"], 120000)
            remote.assert_not_called()
        with CaptureQueriesContext(connection) as queries:
            listed = evidence.listing({}, self.admin)
        self.assertEqual((listed["items"][0]["sourceCount"], listed["items"][0]["completedSources"], listed["items"][0]["rowCount"]), (1, 1, 12))
        self.assertFalse(any("checkpoint_json" in q["sql"] for q in queries))
        self.assertEqual(len(evidence.chunk(run_id, "sales", {"sequence": "1", "rowOffset": "10"}, self.admin)["items"]), 2)

    def test_late_commit_failure_or_fact_quota_rolls_back_all_rows(self):
        run_id = self.start()
        def reject(*args):
            raise AiError("receipt failure")
        with self.assertRaises(AiError):
            self.collect(run_id, commit=reject)
        for setting in ("MAX_BYTES", "MAX_PAGES"):
            with patch("ai_assistant.business_evidence."+setting, 0), self.assertRaises(AiError):
                self.collect(run_id)
        row = evidence.get_run(run_id, self.admin)
        source = m.AiBusinessEvidenceSource.objects.get(run=row)
        self.assertEqual((row.version, row.state_json, row.stored_bytes), (1, "{}", 0))
        self.assertEqual((source.version, source.page_count, source.checkpoint_json), (1, 0, "{}"))
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.exists())

    def test_checkpoint_size_failure_is_atomic(self):
        run_id = self.start()
        def oversized(name, args, principal, **kwargs):
            result = self.execute(name, args, principal, **kwargs)
            if name == "get_data_freshness":
                result["data"]["metadata"] = "x"*32768
            return result
        with self.assertRaises(AiError):
            self.collect(run_id, execute=oversized)
        self.assertEqual(evidence.get_run(run_id, self.admin).version, 1)
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.exists())

    def test_quota_counts_v2_metadata_and_v1_facts_without_loading_checkpoints(self):
        run_id = self.start()
        row = evidence.get_run(run_id, self.admin)
        source = m.AiBusinessEvidenceSource.objects.get(run=row)
        exact = sum(len(v.encode()) for v in (row.plan_json, row.state_json, source.query_json, source.checkpoint_json))
        with CaptureQueriesContext(connection) as queries:
            store.check_quota(self.admin, 4*10000-exact, 10000)
        self.assertTrue(any("OCTET_LENGTH" in q["sql"] for q in queries))
        with self.assertRaises(AiError):
            store.check_quota(self.admin, 4*10000-exact+1, 10000)
        legacy = evidence.create(self.body, self.admin)["item"]["id"]
        # A v1 parent contributes only its original fact bytes, not JSON overhead.
        m.AiBusinessEvidenceRun.objects.filter(pk=legacy).update(stored_bytes=123, version=2)
        store.check_quota(self.admin, 4*10000-exact-123, 10000)
        with self.assertRaises(AiError):
            store.check_quota(self.admin, 4*10000-exact-122, 10000)
        with patch("ai_assistant.business_evidence.MAX_BYTES", 1), self.assertRaises(AiError):
            evidence.create(self.request(), self.admin)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.count(), 2)

    def test_v1_collect_cannot_bypass_existing_v2_metadata_quota(self):
        self.start()
        legacy = evidence.create(self.body, self.admin)["item"]["id"]
        with patch("ai_assistant.business_evidence_store.check_quota", side_effect=AiError("metadata quota")) as quota:
            with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch("ai_assistant.transport.execute_tool", side_effect=fixtures.BusinessEvidenceTests.execute.__get__(self)), self.assertRaises(AiError):
                evidence.collect(legacy, {"sourceKey": "sales", "expectedVersion": 1}, self.admin, "legacy")
        quota.assert_called_once()
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.exists())

    def test_quota_charges_creation_checkpoint_growth_and_seal_growth(self):
        original = store.check_quota
        with patch("ai_assistant.business_evidence_store.check_quota", wraps=original) as quota:
            run_id = self.start()
        row = evidence.get_run(run_id, self.admin)
        source = m.AiBusinessEvidenceSource.objects.get(run=row)
        created_bytes = sum(len(v.encode()) for v in (row.plan_json, row.state_json, source.query_json, source.checkpoint_json))
        self.assertEqual(quota.call_args.args[1], created_bytes)
        with patch("ai_assistant.business_evidence_store.check_quota", wraps=original) as quota:
            self.collect(run_id)
        source.refresh_from_db()
        self.assertEqual(quota.call_args.args[1], source.stored_bytes+len(source.checkpoint_json.encode())-2)
        with patch("ai_assistant.business_evidence_store.check_quota", wraps=original) as quota:
            evidence.finish(run_id, {"expectedVersion": 2, "action": "seal"}, self.admin)
        row.refresh_from_db()
        self.assertEqual(quota.call_args.args[1], len(row.state_json.encode())-2)
        corrupted = json.loads(row.state_json)
        corrupted["sourcesDigest"] = "b"*64
        row.state_json = canonical(corrupted)
        with self.assertRaises(AiError):
            store.verify_seal(row)

    def test_global_quota_accounts_for_other_owners_without_scope_leak(self):
        run_id = self.start()
        row = evidence.get_run(run_id, self.admin)
        source = m.AiBusinessEvidenceSource.objects.get(run=row)
        exact = sum(len(v.encode()) for v in (row.plan_json, row.state_json, source.query_json, source.checkpoint_json))
        m.AiBusinessEvidenceRun.objects.create(id=uid("evidence"), owner_email="quota-other@example.invalid",
            client_request_id=uid("quota"), request_digest=digest("quota"), plan_json=canonical({"schemaVersion": "business-evidence-v1", "sources": []}),
            status="cancelled", stored_bytes=320000-exact)
        store.check_quota(self.admin, 0, 10000)
        with self.assertRaises(AiError):
            store.check_quota(self.admin, 1, 10000)
        self.assertEqual(evidence.listing({}, self.admin)["pagination"]["total"], 1)

    def test_directory_rebuilt_not_trusting_digest_and_pagination_types(self):
        run_id = self.start()
        row = evidence.get_run(run_id, self.admin)
        bad = deepcopy(json.loads(row.plan_json))
        bad["catalogDigest"] = "a"*64
        row.plan_json = canonical(bad)
        with self.assertRaises(AiError):
            store.catalog(row)
        for params in ({"offset": "9"*5000}, {"limit": "0"}, {"limit": "21"}, {"offset": "1"}, {"offset": True}):
            with self.assertRaises(AiError):
                evidence.directory(run_id, params, self.admin)

    def test_read_progress_rejects_concurrent_parent_version_change(self):
        for mode in ("mapping", "source"):
            run_id = self.start()
            row = evidence.get_run(run_id, self.admin)
            original = store.compact_sources if mode == "mapping" else store.checkpoint
            def concurrent(*args):
                result = original(*args)
                m.AiBusinessEvidenceRun.objects.filter(pk=run_id).update(version=2)
                return result
            name = "compact_sources" if mode == "mapping" else "checkpoint"
            with patch("ai_assistant.business_evidence_store."+name, side_effect=concurrent), self.assertRaises(AiError) as caught:
                evidence.mapping(row) if mode == "mapping" else evidence.source_detail(run_id, "sales", self.admin)
            self.assertEqual(caught.exception.code, "version_conflict")
