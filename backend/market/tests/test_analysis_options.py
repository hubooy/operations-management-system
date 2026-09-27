"""Actual owning services/HTTP tests; PostgreSQL execution belongs to root harness."""
import ast
import importlib
import json
import os
from pathlib import Path
import re
import uuid
from unittest.mock import patch
from urllib.parse import urlencode

from django.db import connection, transaction, DatabaseError
from django.test import TransactionTestCase, override_settings
from django.urls import clear_url_caches
from django.utils import timezone
from access_control.models import AccessRole, AppUser
from market import analysis_options as service, analysis_options_projection as projection
from market.errors import MarketApiError
from market.import_service import import_market_payload
from market.models import MarketAnalysisOption, MarketAnalysisOptionsState, MarketDataRevision, MarketImportBatch, MarketWriteAuthority
from market.tests import factories
from sales.auth import Principal
from sales.tests.factories import TEST_SECRET, signed_headers

EPOCH = "11111111-1111-4111-8111-111111111111"
CUTOVER = "market-options-candidate"


@override_settings(MARKET_WRITE_AUTHORITY_EPOCH=EPOCH, MARKET_WRITE_CUTOVER_ID=CUTOVER)
class MarketOptionsOwningTests(TransactionTestCase):
    def setUp(self):
        self.principal = Principal("admin@example.test", "Synthetic", "admin", None)
        now = timezone.now()
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        AppUser.objects.update_or_create(email=self.principal.email, defaults={"role": role, "status": "active", "scope": None,
            "display_name": "Synthetic", "created_at": now, "updated_at": now, "version": 1})
        MarketDataRevision.objects.get_or_create(domain="market", defaults={"revision": 0, "source_digest": "a" * 64})
        MarketWriteAuthority.objects.update_or_create(id=1, defaults={"status": "postgres", "authority_epoch": uuid.UUID(EPOCH),
            "cutover_id": CUTOVER, "migration_verify_run_id": "market-options-test", "activated_at": now})
        MarketAnalysisOptionsState.objects.get_or_create(id=1)

    def imported(self, count=1, **changes):
        rows = [factories.market_row(sourceRowNumber=i+1, skuCode=f"S{i}", category=f"C{i:03d}",
            periodStart="2026-09-01", periodEnd="2026-09-01", **changes) for i in range(count)]
        return import_market_payload(factories.prepared_payload(*rows), self.principal.email)

    def ready(self, count=1):
        self.imported(count)
        return projection.publish_rebuild(projection.prepare_rebuild(self.principal), self.principal)

    def get(self, query=None, *, process_role="market_reader", email="admin@example.test", role="admin", scope=None):
        import market.urls
        import teruisi_backend.urls
        path = "/api/market/analysis-options" + ("?" + urlencode(query, doseq=True) if query else "")
        try:
            with override_settings(DJANGO_PROCESS_ROLE=process_role), patch.dict(os.environ, {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
                importlib.reload(market.urls)
                # include() captured the prior urlpatterns list: refreshing only
                # the child module does not reproduce a fresh writer process.
                importlib.reload(teruisi_backend.urls)
                clear_url_caches()
                if process_role == "market_writer":
                    self.assertNotIn("analysis-options", [str(item.pattern) for item in market.urls.urlpatterns])
                return self.client.get(path, headers=signed_headers(path, email=email, role=role, scope=scope))
        finally:
            importlib.reload(market.urls)
            importlib.reload(teruisi_backend.urls)
            clear_url_caches()

    def tearDown(self):
        import market.urls
        import teruisi_backend.urls
        importlib.reload(market.urls)
        importlib.reload(teruisi_backend.urls)
        clear_url_caches()

    def test_not_initialized_bad_history_and_valid_empty_are_distinct(self):
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "options_not_ready")
        projection.publish_rebuild(projection.prepare_rebuild(self.principal), self.principal)
        self.assertEqual(self.get().json()["items"], [])
        # A new import cannot silently certify damaged historic metadata.
        self.imported()
        MarketImportBatch.objects.update(scope_json={})
        with self.assertRaises(MarketApiError):
            projection.prepare_rebuild(self.principal)

    def test_actual_import_rebuild_and_http_page_use_only_index_metadata(self):
        self.ready(25)
        seen = []
        def guard(execute, sql, params, many, context):
            upper = sql.upper()
            self.assertFalse(any(word in upper for word in ("MARKET_RANKING_ENTRIES", "MARKET_IMPORT_BATCHES", "SALES_ORDER_LINES")), sql)
            self.assertNotIn(upper.lstrip().split()[0], {"INSERT", "UPDATE", "DELETE"})
            seen.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(guard):
            first = self.get()
            self.assertEqual(first.status_code, 200, first.content)
            cursor = first.json()["pagination"]["nextCursor"]
            second = self.get({"cursor": cursor})
        self.assertEqual([len(first.json()["items"]), len(second.json()["items"])], [20, 5])
        identities = [v["identity"]["category"] for page in (first, second) for v in page.json()["items"]]
        self.assertEqual(identities, [f"C{i:03d}" for i in range(25)])
        self.assertTrue(first.json()["authorityVerified"])
        self.assertTrue(all(not v["dateMetadata"]["coverageVerified"] for v in first.json()["items"]))
        self.assertLessEqual(len(first.content), 38000)
        self.assertEqual(first["Cache-Control"], "no-store")
        self.assertTrue(any("LIMIT 21" in sql for sql in seen))

    def test_filters_raw_price_and_strict_http_parameters(self):
        self.ready(3)
        out = self.get({"category": "C001", "priceBandFilter": "全部", "rankingDimension": "SKU", "q": "C001"})
        self.assertEqual(out.status_code, 200, out.content)
        self.assertEqual(len(out.json()["items"]), 1)
        self.assertEqual(self.get({"priceBandFilter": "全价格带"}).json()["items"], [])
        for query in ({"limit": "21"}, {"scope": ""}, {"unknown": "x"}, {"category": ["C001", "C002"]}):
            self.assertEqual(self.get(query).status_code, 400)
        self.assertIn(self.get(process_role="market_writer").status_code, (404, 405))
        self.assertIn(self.client.post("/api/market/analysis-options", data={}).status_code, (404, 405))

    def test_unicode_casefold_and_literal_search_match_pure_contract(self):
        rows = [factories.market_row(sourceRowNumber=i+1, skuCode=f"Unicode{i}", category=name,
            periodStart="2026-09-01", periodEnd="2026-09-01") for i, name in enumerate(("Straße", "İstanbul", "A%B_C"))]
        import_market_payload(factories.prepared_payload(*rows), self.principal.email)
        projection.publish_rebuild(projection.prepare_rebuild(self.principal), self.principal)
        for query, expected in (("STRASSE", "Straße"), ("i\u0307", "İstanbul"), ("%B_", "A%B_C")):
            response = self.get({"q": query})
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual([item["identity"]["category"] for item in response.json()["items"]], [expected])

    def test_rebuild_bounded_metadata_no_fact_scan_and_failure_leaves_uninitialized(self):
        self.imported()
        def guard(execute, sql, params, many, context):
            self.assertNotIn("market_ranking_entries", sql.lower())
            self.assertNotIn(sql.lstrip().split()[0].upper(), {"INSERT", "UPDATE", "DELETE"})
            return execute(sql, params, many, context)
        with connection.execute_wrapper(guard):
            prepared = projection.prepare_rebuild(self.principal)
        self.assertIsInstance(prepared, projection.PreparedProjection)
        with patch.object(projection, "MAX_REBUILD_BYTES", 1), self.assertRaises(MarketApiError):
            projection.prepare_rebuild(self.principal)
        self.assertEqual(MarketAnalysisOptionsState.objects.get(id=1).status, "not_ready")

    def test_actual_account_revocation_scope_local_and_late_change(self):
        self.ready(21)
        cursor = self.get().json()["pagination"]["nextCursor"]
        for fields in ({"status": "disabled"}, {"scope": {"shop": "restricted"}}):
            AppUser.objects.filter(email=self.principal.email).update(**fields)
            self.assertEqual(self.get().status_code, 403)
            AppUser.objects.filter(email=self.principal.email).update(status="active", scope=None)
        self.assertEqual(self.get(email="local-admin@teruisi.local").status_code, 403)
        self.assertEqual(self.get(role="viewer").status_code, 403)
        self.assertEqual(self.get(scope={"shops": ["x"]}).status_code, 403)
        AppUser.objects.filter(email=self.principal.email).update(version=2)
        self.assertEqual(self.get({"cursor": cursor}).status_code, 403)
        original = service.row_entry
        def revoke(row):
            result = original(row)
            AppUser.objects.filter(email=self.principal.email).update(status="disabled")
            return result
        with patch.object(service, "row_entry", side_effect=revoke):
            self.assertEqual(self.get().status_code, 403)

    def test_cursor_query_revision_generation_and_expiry(self):
        self.ready(21)
        cursor = self.get().json()["pagination"]["nextCursor"]
        self.assertEqual(self.get({"cursor": cursor, "q": "C"}).status_code, 409)
        self.assertEqual(self.get({"cursor": cursor + "x"}).status_code, 409)
        from django.core.signing import TimestampSigner
        with patch.object(TimestampSigner, "unsign", side_effect=__import__("django.core.signing", fromlist=["SignatureExpired"]).SignatureExpired()):
            self.assertEqual(self.get({"cursor": cursor}).status_code, 409)
        MarketDataRevision.objects.filter(domain="market").update(revision=8)
        self.assertEqual(self.get({"cursor": cursor}).status_code, 409)
        self.assertEqual(self.get().status_code, 200)  # Non-import market changes don't permanently disable directory.
        fresh = self.get().json()["pagination"]["nextCursor"]
        projection.publish_rebuild(projection.prepare_rebuild(self.principal), self.principal)
        self.assertEqual(self.get({"cursor": fresh}).status_code, 409)

    def test_prepare_is_outside_transaction_stale_or_json_cannot_publish(self):
        self.imported()
        with transaction.atomic(), self.assertRaises(MarketApiError):
            projection.prepare_rebuild(self.principal)
        prepared = projection.prepare_rebuild(self.principal)
        with self.assertRaises(MarketApiError):
            projection.publish_rebuild(json.loads(prepared._raw), self.principal)
        MarketDataRevision.objects.filter(domain="market").update(revision=9)
        with self.assertRaises(MarketApiError):
            projection.publish_rebuild(prepared, self.principal)
        self.assertEqual(MarketAnalysisOptionsState.objects.get(id=1).status, "not_ready")
        self.assertEqual(MarketAnalysisOption.objects.count(), 0)

    def test_import_sync_and_rollback_bad_range_marks_blocked_without_losing_import(self):
        initial = self.ready()
        changed = factories.market_row(sourceRowNumber=1, skuCode="different", category="new", periodStart="2026-09-03", periodEnd="2026-09-03")
        payload = factories.prepared_payload(changed)
        with self.assertRaises(RuntimeError), patch("market.import_service._create_image_job", side_effect=RuntimeError("late rollback")):
            import_market_payload(payload, self.principal.email)
        self.assertEqual(MarketAnalysisOptionsState.objects.get(id=1).generation, initial["generation"])
        self.assertEqual(MarketAnalysisOption.objects.count(), 1)
        original_sync = projection.synchronize_import
        observed = []
        def check_scope(full_scope, revision):
            self.assertIsInstance(full_scope, dict)
            self.assertEqual(full_scope, payload["scope"])
            observed.append(full_scope)
            return original_sync(full_scope, revision)
        with patch.object(projection, "synchronize_import", side_effect=check_scope):
            import_market_payload(payload, self.principal.email)
        self.assertEqual(len(observed), 1)
        self.assertEqual(MarketAnalysisOptionsState.objects.get(id=1).status, "ready")
        self.assertEqual(MarketAnalysisOption.objects.count(), 2)
        self.assertEqual(set(MarketAnalysisOption.objects.values_list("category", flat=True)), {"C000", "new"})
        bad = factories.market_row(sourceRowNumber=1, skuCode="legacy", periodStart="2026-02-30", periodEnd="2026-02-30")
        result = import_market_payload(factories.prepared_payload(bad), self.principal.email)
        self.assertEqual(result["status"], "imported")
        self.assertEqual(MarketAnalysisOptionsState.objects.get(id=1).status, "blocked")
        self.assertEqual(self.get().status_code, 503)

    def test_damaged_index_and_late_generation_never_return_success(self):
        self.ready()
        MarketAnalysisOption.objects.update(entry_digest="b" * 64)
        self.assertEqual(self.get().status_code, 503)
        projection.publish_rebuild(projection.prepare_rebuild(self.principal), self.principal)
        original = service.row_entry
        def change(row):
            item = original(row)
            MarketAnalysisOptionsState.objects.filter(id=1).update(generation=uuid.uuid4().hex)
            return item
        with patch.object(service, "row_entry", side_effect=change):
            self.assertEqual(self.get().status_code, 409)

    def test_real_restricted_roles_use_production_grants_and_no_sensitive_user_columns(self):
        if connection.vendor != "postgresql":
            self.skipTest("Actual PostgreSQL roles required")
        self.ready()
        text = (Path(__file__).resolve().parents[3] / "tools/django-market-service.ps1").read_text(encoding="utf-8-sig")
        code = next(part for part in re.findall(r"@'\r?\n(.*?)\r?\n'@", text, re.S) if "reader_tables =" in part)
        tree = ast.parse(code)
        lists = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"reader_tables", "writer_privileges", "auto_id_tables"}}
        from psycopg import sql
        for kind in ("reader", "writer"):
            prepared = projection.prepare_rebuild(self.principal)
            role = f"options_{kind}_{uuid.uuid4().hex[:10]}"
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(sql.Identifier(role)))
                cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
                if kind == "reader":
                    for table in lists["reader_tables"]:
                        cursor.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(table), sql.Identifier(role)))
                else:
                    for table, privileges in lists["writer_privileges"].items():
                        cursor.execute(sql.SQL("GRANT {} ON {} TO {}").format(sql.SQL(",").join(map(sql.SQL, privileges)), sql.Identifier(table), sql.Identifier(role)))
                    for table in lists["auto_id_tables"]:
                        cursor.execute("SELECT pg_get_serial_sequence(%s,'id')", [table])
                        sequence = cursor.fetchone()[0]
                        if sequence:
                            cursor.execute(sql.SQL("GRANT USAGE ON SEQUENCE {} TO {}").format(sql.Identifier(*sequence.split(".")), sql.Identifier(role)))
                # This statement must come from the actual candidate provisioner.
                grant = next(node for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.startswith("GRANT SELECT (email, role, status, scope, version)"))
                cursor.execute(sql.SQL(grant.value).format(sql.Identifier(role)))
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                    self.assertEqual(service.read_page(self.principal, {})["items"][0]["identity"]["category"], "C000")
                    if kind == "writer":
                        self.assertEqual(projection.publish_rebuild(prepared, self.principal)["identityCount"], 1)
                    for column in ("display_name", "created_at", "migration_generation"):
                        with self.assertRaises(DatabaseError), transaction.atomic():
                            cursor.execute(sql.SQL("SELECT {} FROM access_control_users LIMIT 1").format(sql.Identifier(column)))
                    with self.assertRaises(DatabaseError), transaction.atomic():
                        cursor.execute("UPDATE access_control_users SET status='disabled'")
                    if kind == "reader":
                        with self.assertRaises(DatabaseError), transaction.atomic():
                            cursor.execute("DELETE FROM market_analysis_options")
            finally:
                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                    cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
