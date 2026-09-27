"""Isolated PostgreSQL + actual signature/ORM/role routing; no production calls."""
import copy
import importlib
import time
import uuid
from types import ModuleType
from unittest.mock import patch
from urllib.parse import urlencode

from django.core import signing
from django.db import DatabaseError, connection, transaction
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import clear_url_caches, include, path

from access_control.models import AppUser
from business_analysis.contracts import PageReconciler, canonical
from netshop import analysis, analysis_continuation as service, analysis_cursor
from netshop.errors import NetshopApiError
from netshop.models import NetshopDataRevision, NetshopRow
from netshop.tests import test_analysis_cursor as fixtures
from sales.auth import Principal
from sales.tests.factories import TEST_SECRET, signed_headers

PATH = "/api/netshop/analysis-records/continuation"


class AnalysisContinuationTests(TestCase):
    query = fixtures.ExpiredAnalysisCursorTests.query

    def setUp(self):
        fixtures.ExpiredAnalysisCursorTests.setUp(self)
        self.params.update(limit="100", window="current")
        template = NetshopRow.objects.get(source_row_key="analysis-1")
        rows = []
        for i in range(101):
            row = copy.copy(template)
            row.pk = None
            row.source_row_key = f"continuation-{i}"
            row.source_row_hash = f"{i+1000:064x}"
            rows.append(row)
        NetshopRow.objects.bulk_create(rows)

    def first(self, expired=False):
        if expired:
            with patch.object(signing.TimestampSigner, "timestamp", return_value=signing.b62_encode(int(time.time())-7200)):
                return analysis.read_page(*self.query())
        return analysis.read_page(*self.query())

    def params_for(self, first, **overrides):
        return QueryDict(urlencode({**self.params, "cursor": first["pagination"]["nextCursor"],
            "expectedSourceRef": first["sourceRef"], "expectedRevision": first["sourceRevision"],
            "expectedLastId": first["items"][-1]["rowId"], **overrides}))

    def api(self, first, **overrides):
        url = PATH+"?"+self.params_for(first, **overrides).urlencode()
        return self.client.get(url, headers=signed_headers(url, email=self.principal.email))

    def test_unexpired_reads_original_page_without_renewal(self):
        first = self.first()
        cursor = first["pagination"]["nextCursor"]
        with patch.object(analysis_cursor, "read_expired_page") as renewal:
            actual = service.read_page(self.principal, self.params_for(first))
        renewal.assert_not_called()
        self.assertEqual(actual, analysis.read_page(*self.query(cursor=cursor)))

    def test_real_expired_continuation_preserves_old_page_and_reconciles_read_only(self):
        first = self.first(expired=True)
        original = canonical(first)
        verifier, current = PageReconciler(), first
        verifier.consume(first)
        while not verifier.finished:
            logical_cursor = current["pagination"]["nextCursor"]
            with CaptureQueriesContext(connection) as sql:
                current = service.read_page(self.principal, self.params_for(current))
            self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in sql.captured_queries))
            verifier.consume(current, request_cursor=logical_cursor)
        self.assertTrue(verifier.result()["reconciled"])
        self.assertEqual(verifier.rows, 103)
        self.assertEqual(canonical(first), original)

    def test_exact_parameter_set_fixed_limit_and_scalar_types(self):
        first = self.first()
        for change in ({"limit": "1"}, {"limit": "0100"}, {"window": ""}, {"expectedLastId": "01"},
                {"expectedLastId": "true"}, {"expectedLastId": "1.0"}, {"expectedLastId": "9007199254740992"},
                {"expectedRevision": "7:bad"}, {"expectedSourceRef": "x"}, {"cursor": ""}, {"cursor": "x"*1601}):
            with self.subTest(change=change), self.assertRaises(NetshopApiError):
                service.read_page(self.principal, self.params_for(first, **change))
        good = self.params_for(first)
        for key in good:
            missing = good.copy(); del missing[key]
            duplicate = good.copy(); duplicate.appendlist(key, good[key])
            for params in (missing, duplicate):
                with self.subTest(key=key), self.assertRaises(NetshopApiError):
                    service.validate_request(params)
        with self.assertRaises(NetshopApiError):
            service.validate_request(QueryDict(good.urlencode()+"&unexpected=1"))

    def test_invalid_signature_payload_and_changed_constraints_never_renew(self):
        first = self.first()
        payload = signing.loads(first["pagination"]["nextCursor"], salt=analysis.CURSOR_SALT)
        cursors = [first["pagination"]["nextCursor"]+"x", signing.dumps(payload, salt="wrong"),
            signing.dumps({**payload, "extra": 1}, salt=analysis.CURSOR_SALT),
            signing.dumps({**payload, "lastId": True}, salt=analysis.CURSOR_SALT)]
        for cursor in cursors:
            with self.subTest(cursor=cursor), patch.object(analysis_cursor, "read_expired_page") as renewal, self.assertRaises(NetshopApiError):
                service.read_page(self.principal, self.params_for(first, cursor=cursor))
            renewal.assert_not_called()
        for change in ({"expectedLastId": int(payload["lastId"])+1}, {"expectedSourceRef": "f"*64}, {"expectedRevision": "8:"+"a"*12},
                {"shop": "样例店B"}, {"window": "previous"}, {"dataset": "b2b"}, {"startDate": "2026-09-02"}):
            with self.subTest(change=change), self.assertRaises(NetshopApiError):
                service.read_page(self.principal, self.params_for(first, **change))

    def test_expiration_boundary_only_accepts_direct_signature_expired_cause(self):
        first = self.first()
        original = analysis.read_page
        calls = []
        # Real token is still fresh at classification, then ages at old reader.
        actual_loads = signing.loads
        clock = int(time.time())+7200
        count = 0
        def loads(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                return actual_loads(*args, **kwargs)
            with patch("django.core.signing.time.time", return_value=clock):
                return actual_loads(*args, **kwargs)
        def reader(*args):
            calls.append(args[2])
            return original(*args)
        # New temporary token must be minted at the same synthetic clock.
        with patch.object(signing, "loads", side_effect=loads), patch.object(signing.TimestampSigner, "timestamp", return_value=signing.b62_encode(clock)), patch.object(analysis, "read_page", side_effect=reader):
            result = service.read_page(self.principal, self.params_for(first))
        self.assertEqual(result["sourceRef"], first["sourceRef"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], first["pagination"]["nextCursor"])
        self.assertNotEqual(calls[1], calls[0])
        for cause in (None, signing.BadSignature("bad"), ValueError("bad")):
            error = NetshopApiError("cannot infer", code="invalid_cursor", status=409)
            error.__cause__ = cause
            with patch.object(analysis, "read_page", side_effect=error), patch.object(analysis_cursor, "read_expired_page") as renewal, self.assertRaises(NetshopApiError):
                service.read_page(self.principal, self.params_for(first))
            renewal.assert_not_called()

    def test_late_actor_version_and_revision_change_discard_success(self):
        first = self.first()
        original = analysis.read_page
        for change in ({"version": 2}, {"status": "disabled"}, {"scope": {"platforms": ["京东"]}}):
            def mutate(*args):
                result = original(*args)
                AppUser.objects.filter(email=self.principal.email).update(**change)
                return result
            with patch.object(analysis, "read_page", side_effect=mutate), self.assertRaises(NetshopApiError) as error:
                service.read_page(self.principal, self.params_for(first))
            self.assertEqual(error.exception.status, 403)
            AppUser.objects.filter(email=self.principal.email).update(version=1, status="active", scope=None)
        def revised(*args):
            result = original(*args)
            NetshopDataRevision.objects.filter(domain="netshop").update(revision=8)
            return result
        with patch.object(analysis, "read_page", side_effect=revised), self.assertRaises(NetshopApiError) as error:
            service.read_page(self.principal, self.params_for(first))
        self.assertEqual(error.exception.code, "analysis_revision_changed")

    def test_real_user_required_without_local_fallback(self):
        first = self.first()
        for principal in (Principal("missing@example.test", "", "admin", None), Principal("local-admin@teruisi.local", "", "admin", None),
                Principal(self.principal.email, "", "viewer", None), Principal(self.principal.email, "", "admin", {"platforms": []})):
            with self.subTest(principal=principal), self.assertRaises(NetshopApiError) as error:
                service.read_page(principal, self.params_for(first))
            self.assertEqual(error.exception.status, 403)

    def test_actual_minimal_reader_grants_and_missing_version_fail_closed(self):
        if connection.vendor != "postgresql":
            self.skipTest("Actual PostgreSQL role required")
        from psycopg import sql
        from netshop.analysis_permissions import grant_actor_read, validate_actor_read
        first = self.first(expired=True)
        role = "continuation_reader_"+uuid.uuid4().hex[:10]
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            original_role = cursor.fetchone()[0]
            cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(sql.Identifier(role)))
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            for model in (NetshopRow, NetshopDataRevision):
                cursor.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(model._meta.db_table), sql.Identifier(role)))
            from netshop.models import NetshopImportBatch
            cursor.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(NetshopImportBatch._meta.db_table), sql.Identifier(role)))
            grant_actor_read(cursor, role)
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                validate_actor_read(cursor)
                result = service.read_page(self.principal, self.params_for(first))
                self.assertEqual(result["sourceRef"], first["sourceRef"])
                for query in ("SELECT display_name FROM access_control_users LIMIT 1",
                        "UPDATE access_control_users SET status='disabled'", "DELETE FROM netshop_rows"):
                    with self.assertRaises(DatabaseError), transaction.atomic():
                        cursor.execute(query)
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(original_role)))
                cursor.execute(sql.SQL("REVOKE SELECT (version) ON access_control_users FROM {}").format(sql.Identifier(role)))
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                with self.assertRaisesRegex(ValueError, "netshop_analysis_actor_columns_missing"):
                    validate_actor_read(cursor)
                with self.assertRaises(DatabaseError), transaction.atomic():
                    service.read_page(self.principal, self.params_for(first))
        finally:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(original_role)))
                cursor.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_signed_get_header_post_and_both_route_roles(self):
        import netshop.urls
        first = self.first(expired=True)
        response = self.api(first)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["X-Netshop-Data-Revision"], first["sourceRevision"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("renewal", response.json())
        url = PATH+"?"+self.params_for(first).urlencode()
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertIn(self.client.get(url).status_code, (401, 403))
        for role in ("viewer", "analyst", "operator"):
            self.assertEqual(self.client.get(url, headers=signed_headers(url, email=self.principal.email, role=role)).status_code, 403)
        try:
            for role, status in (("netshop_reader", 200), ("netshop_writer", 404)):
                with override_settings(DJANGO_PROCESS_ROLE=role):
                    module = ModuleType("isolated_continuation_"+role)
                    module.urlpatterns = [path("api/netshop/", include(importlib.reload(netshop.urls).urlpatterns))]
                    clear_url_caches()
                    with override_settings(ROOT_URLCONF=module):
                        response = self.client.get(url, headers=signed_headers(url, email=self.principal.email))
                        self.assertEqual(response.status_code, status, response.content)
        finally:
            importlib.reload(netshop.urls)
            clear_url_caches()
