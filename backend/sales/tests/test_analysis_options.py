"""ERP options owning/real-role regressions; run only in isolated PostgreSQL."""
import importlib
import os
import uuid
from datetime import date
from unittest.mock import patch
from urllib.parse import urlencode

from django.core import signing
from django.db import connection, transaction, DatabaseError
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import clear_url_caches
from django.utils import timezone
from access_control.models import AccessRole, AppUser
from erp_reference.models import ErpReferenceImportScopeHead, ErpReferenceWriteAuthority
from erp_reference.import_service import SOURCE_SCOPE_KEYS
from sales import analysis_options as service, analysis_options_projection as projection
from sales.auth import Principal
from sales.models import SalesAnalysisOption, SalesAnalysisOptionsState, SalesDataRevision, SalesOrderLine, SalesWriteAuthority
from sales.tests import test_write_service as writer_fixtures
from sales.tests.cutover_fixtures import install_writer_runtime_guard
from sales.tests.factories import make_line, signed_headers, TEST_SECRET
from sales.write_service import complete_staged_import


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_EXPECT_READ_ONLY=False,
    SALES_WRITE_AUTHORITY_EPOCH=writer_fixtures.AUTHORITY_EPOCH,
    SALES_WRITE_CUTOVER_ID=writer_fixtures.CUTOVER_ID)
class SalesOptionsOwningTests(TransactionTestCase):
    _begin_stage = writer_fixtures.SalesWriteServiceTests._begin_stage

    def setUp(self):
        self.principal = Principal("admin@example.test", "Synthetic", "admin", None)
        now = timezone.now()
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank":40,"label":"Admin"})
        AppUser.objects.update_or_create(email=self.principal.email, defaults={"role":role,"status":"active","scope":None,
            "display_name":"Synthetic","created_at":now,"updated_at":now,"version":1})
        SalesWriteAuthority.objects.update_or_create(id=1, defaults={"status":"active",
            "authority_epoch":writer_fixtures.AUTHORITY_EPOCH,"cutover_id":writer_fixtures.CUTOVER_ID,"activated_at":now})
        # TransactionTestCase flushes data-migration seeds after the first test.
        ErpReferenceWriteAuthority.objects.get_or_create(id=1)
        for source,key in SOURCE_SCOPE_KEYS.items():
            ErpReferenceImportScopeHead.objects.get_or_create(scope_key=key, defaults={"source_key":source})
        install_writer_runtime_guard(writer_fixtures.CUTOVER_ID)
        SalesDataRevision.objects.get_or_create(domain="sales", defaults={"revision":0,"source_digest":"a"*64})
        SalesAnalysisOptionsState.objects.get_or_create(id=1)

    def lines(self, count=1):
        SalesOrderLine.objects.bulk_create([make_line(i+1,f"options-{i}",shop_name=f"店铺{i:03d}") for i in range(count)])

    def rebuild(self):
        return projection.publish_rebuild(projection.prepare_rebuild(self.principal),self.principal)

    def get(self, query=None, *, process_role="reader", email="admin@example.test", role="admin", scope=None, method="GET"):
        import sales.urls
        import teruisi_backend.urls
        path="/api/sales/analysis-options"+("?"+urlencode(query,doseq=True) if query else "")
        try:
            with override_settings(DJANGO_PROCESS_ROLE=process_role), patch.dict(os.environ,{"TERUISI_DJANGO_INTERNAL_SECRET":TEST_SECRET}):
                importlib.reload(sales.urls); importlib.reload(teruisi_backend.urls); clear_url_caches()
                if process_role=="sales_writer":
                    self.assertNotIn("analysis-options",[str(item.pattern) for item in sales.urls.urlpatterns])
                headers=signed_headers(path,email=email,role=role,scope=scope,method=method)
                return self.client.get(path,headers=headers) if method=="GET" else self.client.post(path,data=b"",content_type="application/json",headers=headers)
        finally:
            importlib.reload(sales.urls); importlib.reload(teruisi_backend.urls); clear_url_caches()

    def test_uninitialized_and_explicit_verified_empty_are_distinct(self):
        self.assertEqual(self.get().status_code,503)
        result=self.rebuild()
        self.assertEqual(result["identityCount"],0)
        response=self.get()
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()["items"],[])
        self.assertTrue(response.json()["authorityVerified"])
        self.assertFalse(result["coverageVerified"])

    def test_actual_page_keyset_is_metadata_only_and_exactly_twenty(self):
        self.lines(25); self.rebuild()
        with CaptureQueriesContext(connection) as captured:
            response=self.get()
        self.assertEqual(response.status_code,200,response.content)
        page=response.json()
        self.assertEqual(page["pagination"]["returned"],20)
        self.assertEqual(response.headers["X-Sales-Data-Revision"],page["revision"])
        queries="\n".join(item["sql"] for item in captured.captured_queries).lower()
        for forbidden in ("sales_order_lines","sales_import_batches","erp_product_master","insert ","update ","delete "):
            self.assertNotIn(forbidden,queries)
        self.assertIn("limit 21",queries)
        next_page=self.get({"cursor":page["pagination"]["nextCursor"]})
        self.assertEqual(next_page.status_code,200,next_page.content)
        self.assertEqual(next_page.json()["pagination"]["returned"],5)
        self.assertFalse(next_page.json()["pagination"]["hasMore"])
        self.assertFalse(page["items"][0]["dateMetadata"]["coverageVerified"])

    def test_raw_identity_fallback_exclusion_and_distinct_platforms(self):
        values=[{}, {"shop_name":""}, {"shop_name":" 京东一店 "}, {"channel":""},
            {"warehouse":"刷刷仓"}, {"platform":"平台别名","shop_name":"未分类"},
            {"platform":"京東"}, {"platform":"x"*201}]
        SalesOrderLine.objects.bulk_create([make_line(i+1,f"raw-{i}",**v) for i,v in enumerate(values)])
        self.rebuild()
        page=self.get().json()
        self.assertEqual({tuple(x["identity"].values()) for x in page["items"]},
            {("渠道A","京东","京东一店"),("渠道A","京東","京东一店"),("渠道A","平台别名","未分类")})
        self.assertEqual(len(self.get({"platform":"京东","shop":"京东一店"}).json()["items"]),1)

    def test_invalid_or_duplicate_query_writer_surface_and_method_rejected(self):
        self.rebuild()
        for query in ({"q":"a"},{"limit":"10"},{"platform":["京东","天猫"]},{"shop":"x"},{"channel":" x"}):
            self.assertEqual(self.get(query).status_code,400,query)
        self.assertEqual(self.get(process_role="sales_writer").status_code,404)
        self.assertEqual(self.get(method="POST").status_code,405)

    def test_actual_actor_status_scope_cursor_version_and_missing_user(self):
        self.lines(21); self.rebuild()
        cursor=self.get().json()["pagination"]["nextCursor"]
        AppUser.objects.filter(email=self.principal.email).update(version=2)
        self.assertEqual(self.get({"cursor":cursor}).status_code,403)
        self.assertEqual(self.get().status_code,200)
        self.assertEqual(self.get(email="missing@example.test").status_code,403)
        self.assertEqual(self.get(role="viewer").status_code,403)
        AppUser.objects.filter(email=self.principal.email).update(scope="京东一店")
        self.assertEqual(self.get().status_code,403)
        AppUser.objects.filter(email=self.principal.email).update(scope=None,status="disabled")
        self.assertEqual(self.get().status_code,403)

    def test_cursor_query_erp_revision_generation_signature_and_expiry(self):
        self.lines(21); self.rebuild()
        cursor=self.get().json()["pagination"]["nextCursor"]
        self.assertEqual(self.get({"cursor":cursor,"platform":"京东"}).status_code,409)
        self.assertEqual(self.get({"cursor":cursor+"x"}).status_code,409)
        with patch.object(signing.TimestampSigner,"unsign",side_effect=signing.SignatureExpired()):
            self.assertEqual(self.get({"cursor":cursor}).status_code,409)
        SalesDataRevision.objects.filter(domain="erp").update(revision=6)
        self.assertEqual(self.get({"cursor":cursor}).status_code,409)
        self.assertEqual(self.get().status_code,200)
        # Restore actual ERP revision before the full runtime reconstruction.
        SalesDataRevision.objects.filter(domain="erp").update(revision=5)
        self.rebuild()
        self.assertEqual(self.get({"cursor":cursor}).status_code,409)

    def test_actual_import_replace_stales_then_deletes_identity_and_shrinks_dates(self):
        rows=[writer_fixtures.normalized_row(1), writer_fixtures.normalized_row(2,shipTime="2024-01-02 10:00:00"),
            writer_fixtures.normalized_row(3,shopName="将移除店铺")]
        first=self._begin_stage(rows,fingerprint="options-first",raw_hash="a"*64)
        self.assertEqual(complete_staged_import(first,self.principal.email)["status"],"imported")
        self.rebuild()
        before=self.get().json()
        self.assertEqual(len(before["items"]),2)
        observed=next(x for x in before["items"] if x["identity"]["shop"]=="志高测试店")
        self.assertEqual((observed["dateMetadata"]["firstDate"],observed["dateMetadata"]["lastDate"]),("2024-01-01","2024-01-02"))
        second=self._begin_stage([writer_fixtures.normalized_row(4,shipTime="2024-01-02 10:00:00")],fingerprint="options-second",raw_hash="b"*64)
        self.assertEqual(complete_staged_import(second,self.principal.email)["status"],"imported")
        self.assertEqual(SalesOrderLine.objects.count(),1)
        self.assertEqual(self.get().status_code,503)
        self.rebuild()
        after=self.get().json()
        self.assertEqual(len(after["items"]),1)
        self.assertEqual(after["items"][0]["dateMetadata"]["firstDate"],"2024-01-02")
        self.assertEqual(after["items"][0]["dateMetadata"]["lastDate"],"2024-01-02")

    def test_prepare_outside_lock_stale_or_json_not_publishable(self):
        self.lines()
        with transaction.atomic(), self.assertRaises(projection.OptionsError):
            projection.prepare_rebuild(self.principal)
        prepared=projection.prepare_rebuild(self.principal)
        with self.assertRaises(projection.OptionsError):
            projection.publish_rebuild({"_raw":prepared._raw},self.principal)
        SalesDataRevision.objects.filter(domain="sales").update(revision=7)
        with self.assertRaises(projection.OptionsError):
            projection.publish_rebuild(prepared,self.principal)
        self.assertEqual(SalesAnalysisOption.objects.count(),0)
        self.assertEqual(SalesAnalysisOptionsState.objects.get(id=1).status,"not_ready")

    def test_publish_no_fact_or_master_scan_inside_transaction(self):
        self.lines(); prepared=projection.prepare_rebuild(self.principal)
        def guard(execute,sql,params,many,context):
            if connection.in_atomic_block and sql.lstrip().upper().startswith("SELECT"):
                for forbidden in ("sales_order_lines","erp_product_master","erp_combo_items"):
                    self.assertNotIn(forbidden,sql)
            return execute(sql,params,many,context)
        with connection.execute_wrapper(guard):
            self.assertEqual(projection.publish_rebuild(prepared,self.principal)["identityCount"],1)

    def test_capacity_and_bad_exact_metadata_fail_without_publishing_partial(self):
        self.lines(2)
        with patch.object(projection,"MAX_IDENTITIES",1), self.assertRaises(projection.OptionsError):
            projection.prepare_rebuild(self.principal)
        self.assertEqual(SalesAnalysisOption.objects.count(),0)
        SalesOrderLine.objects.filter(pk=1).update(shop_name="bad\tidentity",shop_key="bad\tidentity")
        with self.assertRaises(projection.OptionsError):
            projection.prepare_rebuild(self.principal)
        self.assertEqual(SalesAnalysisOptionsState.objects.get(id=1).status,"not_ready")

    def test_null_business_date_is_db_rejected_and_invalid_aggregate_is_controlled(self):
        self.lines()
        with self.assertRaises(DatabaseError), transaction.atomic():
            SalesOrderLine.objects.filter(pk=1).update(business_date=None)
        from django.db.models.query import QuerySet
        original=QuerySet.iterator
        def corrupted(query,*args,**kwargs):
            for row in original(query,*args,**kwargs):
                if query.model is SalesOrderLine and type(row) is dict and "firstDate" in row:
                    row={**row,"firstDate":None}
                yield row
        with patch.object(QuerySet,"iterator",corrupted), self.assertRaises(projection.OptionsError) as error:
            projection.prepare_rebuild(self.principal)
        self.assertEqual(error.exception.code,"options_not_ready")

    def test_damaged_index_and_late_permission_or_generation_are_rejected(self):
        self.lines(); self.rebuild()
        SalesAnalysisOption.objects.update(entry_digest="b"*64)
        self.assertEqual(self.get().status_code,503)
        self.rebuild()
        original=service.row_entry
        def changed(row):
            value=original(row)
            SalesAnalysisOptionsState.objects.filter(id=1).update(generation=uuid.uuid4().hex)
            return value
        with patch.object(service,"row_entry",side_effect=changed):
            self.assertEqual(self.get().status_code,409)
        def revoked(row):
            value=original(row)
            AppUser.objects.filter(email=self.principal.email).update(status="disabled")
            return value
        with patch.object(service,"row_entry",side_effect=revoked):
            self.assertEqual(self.get().status_code,403)

    def test_real_restricted_roles_use_exact_new_grants_and_no_user_sensitive_columns(self):
        if connection.vendor!="postgresql": self.skipTest("Requires actual PostgreSQL roles")
        from psycopg import sql
        from sales.analysis_options_permissions import provision
        self.lines(); self.rebuild()
        prepared=projection.prepare_rebuild(self.principal)
        reader,writer=["erp_options_"+uuid.uuid4().hex[:12] for _ in range(2)]
        with connection.cursor() as cursor:
            for role in (reader,writer):
                cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(sql.Identifier(role)))
                cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
                cursor.execute(sql.SQL("GRANT SELECT ON sales_data_revisions TO {}").format(sql.Identifier(role)))
            # Existing writer metadata subset: no facts/master are needed to publish.
            cursor.execute(sql.SQL("GRANT UPDATE ON sales_data_revisions TO {}").format(sql.Identifier(writer)))
            cursor.execute(sql.SQL("GRANT SELECT ON sales_write_authority,sales_cutover_attestations TO {}").format(sql.Identifier(writer)))
            provision(cursor,reader=reader,writer=writer)
        try:
            for role in (reader,writer):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                    self.assertEqual(len(service.read_page(self.principal,{})["items"]),1)
                    from teruisi_backend.health import _validate_sales_options, ReadinessError
                    with override_settings(DJANGO_PROCESS_ROLE="reader" if role==reader else "sales_writer"):
                        _validate_sales_options(cursor)
                    if role==writer:
                        self.assertEqual(projection.publish_rebuild(prepared,self.principal)["identityCount"],1)
                    for column in ("display_name","created_at","migration_generation"):
                        with self.assertRaises(DatabaseError), transaction.atomic():
                            cursor.execute(sql.SQL("SELECT {} FROM access_control_users LIMIT 1").format(sql.Identifier(column)))
                    with self.assertRaises(DatabaseError), transaction.atomic(): cursor.execute("UPDATE access_control_users SET status='disabled'")
                    if role==reader:
                        with self.assertRaises(DatabaseError), transaction.atomic(): cursor.execute("DELETE FROM sales_analysis_options")
                    # These minimal test roles intentionally have no legacy fact grant;
                    # production reader keeps its pre-existing sales query privileges.
                    with self.assertRaises(DatabaseError), transaction.atomic(): cursor.execute("SELECT id FROM sales_order_lines LIMIT 1")
        finally:
            with connection.cursor() as cursor:
                for role in (reader,writer):
                    cursor.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                    cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
