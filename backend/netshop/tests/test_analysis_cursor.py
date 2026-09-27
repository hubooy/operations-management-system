"""Real ORM/signing tests. Run only in the isolated PostgreSQL harness."""
import copy
import time
from unittest.mock import patch

from django.core import signing
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis.contracts import PageReconciler, canonical
from netshop import analysis, analysis_cursor as service
from netshop.errors import NetshopApiError
from netshop.models import NetshopDataRevision, NetshopImportBatch, NetshopRow
from netshop.tests import test_analysis as facts
from sales.auth import Principal


class ExpiredAnalysisCursorTests(TestCase):
    # Reuse fixture methods only; don't import or inherit another TestCase.
    query = facts.AnalysisRecordsTests.query

    def setUp(self):
        facts.AnalysisRecordsTests.setUp(self)
        self.principal = Principal("cursor-admin@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.update_or_create(email=self.principal.email, defaults={"role": role, "status": "active",
            "scope": None, "display_name": "Synthetic", "version": 1, "created_at": now, "updated_at": now})

    def expired(self, **overrides):
        with patch.object(signing.TimestampSigner, "timestamp", return_value=signing.b62_encode(int(time.time())-7200)):
            return analysis.read_page(*self.query(**overrides))

    def read(self, first, **overrides):
        query = {key: value for key, value in self.params.items() if key != "limit"}
        arguments = {"expected_source_ref": first["sourceRef"], "expected_revision": first["sourceRevision"],
            "expected_last_id": int(first["items"][-1]["rowId"]), "limit": int(self.params["limit"])}
        principal = overrides.pop("principal", self.principal)
        cursor = overrides.pop("cursor", first["pagination"]["nextCursor"])
        query = overrides.pop("query", query)
        return service.read_expired_page(principal, query, cursor, **{**arguments, **overrides})

    def expired_payload(self, payload, salt=None):
        with patch.object(signing.TimestampSigner, "timestamp", return_value=signing.b62_encode(int(time.time())-7200)):
            return signing.dumps(payload, salt=salt or analysis.CURSOR_SALT, compress=True)

    def test_actual_expiration_complete_replay_no_writes_and_old_bytes_unchanged(self):
        first = self.expired()
        cursor = first["pagination"]["nextCursor"]
        before = canonical(first)
        with self.assertRaises(signing.SignatureExpired):
            signing.loads(cursor, salt=analysis.CURSOR_SALT, max_age=3600)
        with self.assertRaises(NetshopApiError):
            analysis.read_page(*self.query(cursor=cursor))
        with CaptureQueriesContext(connection) as queries:
            second = self.read(first)
        self.assertTrue(queries.captured_queries)
        self.assertTrue(all(item["sql"].lstrip().upper().startswith("SELECT") for item in queries.captured_queries))
        verifier = PageReconciler()
        verifier.consume(first)
        verifier.consume(second, request_cursor=cursor)
        self.assertEqual(verifier.result()["metrics"]["spendCents"]["value"], 323)
        self.assertEqual(canonical(first), before)
        self.assertIsNone(second["control"])
        self.assertEqual(second["sourceRef"], first["sourceRef"])
        # A lost read response is repeatable without committing another page.
        self.assertEqual(second, self.read(first))

    def test_fresh_uses_original_reader_and_bad_signatures_never_renew(self):
        fresh = analysis.read_page(*self.query())
        with self.assertRaises(NetshopApiError):
            self.read(fresh)
        self.assertFalse(analysis.read_page(*self.query(cursor=fresh["pagination"]["nextCursor"]))["pagination"]["hasMore"])
        first = self.expired()
        payload = signing.loads(first["pagination"]["nextCursor"], salt=analysis.CURSOR_SALT)
        for cursor in (first["pagination"]["nextCursor"]+"x", self.expired_payload(payload, "sales-business-analysis-v1"), "not-a-token"):
            with self.subTest(cursor=cursor), patch.object(analysis, "read_page") as reader, self.assertRaises(NetshopApiError):
                self.read(first, cursor=cursor)
            reader.assert_not_called()

    def test_signed_payload_extra_bool_float_negative_or_checkpoint_mismatch_reject(self):
        first = self.expired()
        payload = signing.loads(first["pagination"]["nextCursor"], salt=analysis.CURSOR_SALT)
        for value in (True, float(payload["lastId"]), 0, -1, 9007199254740992):
            with self.subTest(value=value), self.assertRaises(NetshopApiError):
                self.read(first, cursor=self.expired_payload({**payload, "lastId": value}), expected_last_id=value)
        for changed in ({**payload, "extra": 1}, {**payload, "binding": "f"*64}):
            with self.assertRaises(NetshopApiError):
                self.read(first, cursor=self.expired_payload(changed))
        with self.assertRaises(NetshopApiError):
            self.read(first, expected_last_id=payload["lastId"]+1)

    def test_query_scope_window_limit_or_revision_cannot_change(self):
        first = self.expired()
        base = {key: value for key, value in self.params.items() if key != "limit"}
        for changed in ({"shop": "样例店B"}, {"window": "previous"}, {"window": "yearAgo"},
                {"startDate": "2026-09-02"}, {"dataset": "b2b"}, {"cursor": "replacement"}):
            with self.subTest(changed=changed), self.assertRaises(NetshopApiError):
                self.read(first, query={**base, **changed})
        with self.assertRaises(NetshopApiError):
            self.read(first, limit=2)
        NetshopDataRevision.objects.filter(domain="netshop").update(revision=8)
        with self.assertRaises(NetshopApiError):
            self.read(first)

    def test_real_actor_absent_disabled_scoped_and_no_local_fallback(self):
        first = self.expired()
        for actor in (Principal("absent@example.test", "", "admin", None),
                Principal("local-admin@teruisi.local", "", "admin", None),
                Principal(self.principal.email, "", "viewer", None),
                Principal(self.principal.email, "", "admin", {"platforms": ["京东"]})):
            with self.subTest(actor=actor.email), self.assertRaises(NetshopApiError) as error:
                self.read(first, principal=actor)
            self.assertEqual(error.exception.status, 403)
        for change in ({"status": "disabled"}, {"scope": {"platforms": ["京东"]}}):
            AppUser.objects.filter(email=self.principal.email).update(**change)
            with self.assertRaises(NetshopApiError) as error:
                self.read(first)
            self.assertEqual(error.exception.status, 403)
            AppUser.objects.filter(email=self.principal.email).update(status="active", scope=None)

    def test_late_revocation_or_user_version_change_discards_actual_page(self):
        first = self.expired()
        original = analysis.read_page
        for change in ({"status": "disabled"}, {"version": 2}):
            def mutate(*args):
                page = original(*args)
                AppUser.objects.filter(email=self.principal.email).update(**change)
                return page
            with patch.object(analysis, "read_page", side_effect=mutate), self.assertRaises(NetshopApiError) as error:
                self.read(first)
            self.assertEqual(error.exception.status, 403)
            AppUser.objects.filter(email=self.principal.email).update(status="active", version=1)

    def test_late_revision_change_discards_actual_page(self):
        first = self.expired()
        original = analysis.read_page
        def mutate(*args):
            page = original(*args)
            NetshopDataRevision.objects.filter(domain="netshop").update(revision=8)
            return page
        with patch.object(analysis, "read_page", side_effect=mutate), self.assertRaises(NetshopApiError) as error:
            self.read(first)
        self.assertEqual(error.exception.code, "analysis_revision_changed")

    def test_master_batch_change_rejects_even_without_global_revision_change(self):
        template = NetshopRow.objects.get(source_row_key="analysis-1")
        for batch_index, snapshot in enumerate(("2026-09-01", "2026-09-02"), 1):
            batch = NetshopImportBatch.objects.create(id=f"cursor-master-{batch_index}", source="jd_product_master",
                dataset="product_master", platform="京东", shop_name="样例店A", file_name="synthetic.xlsx", file_size_bytes=1,
                file_hash=f"{batch_index:064x}", raw_file_hash=f"{batch_index:064x}", content_hash=f"{batch_index:064x}",
                scope_key=f"{batch_index:064x}", status="completed", snapshot_date=snapshot,
                created_at="2026-09-18", completed_at="2026-09-18")
            for row_index in range(2):
                row = copy.copy(template)
                row.pk = None
                row.source_row_key = f"cursor-master-{batch_index}-{row_index}"
                row.source_row_hash = f"{batch_index*10+row_index:064x}"
                row.source, row.dataset = batch.source, batch.dataset
                row.first_import_batch_id = row.last_import_batch_id = batch.id
                row.snapshot_date = snapshot
                row.save()
            if batch_index == 1:
                self.params["dataset"] = "master"
                first = self.expired()
                self.assertEqual(len(self.read(first)["items"]), 1)
        with self.assertRaises(NetshopApiError) as error:
            self.read(first)
        self.assertEqual(error.exception.code, "analysis_revision_changed")
