"""Only for the isolated PostgreSQL rehearsal; no production requests."""
import importlib
import time
import uuid
from types import ModuleType
from unittest.mock import patch
from urllib.parse import urlencode
from django.core import signing
from django.db import connection, transaction, DatabaseError
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import clear_url_caches, include, path
from django.utils import timezone
from access_control.models import AccessRole, AppUser
from business_analysis.contracts import PageReconciler, canonical
from sales.auth import Principal
from sales.tests.factories import TEST_SECRET, signed_headers, make_line
from sales import analysis, analysis_continuation as service
from sales.models import SalesDataRevision
from sales.query import SalesRequestError
from sales.tests import test_analysis as fixtures

PATH = "/api/sales/analysis-records/continuation"
ERROR = SalesRequestError


class AnalysisContinuationTests(TestCase):
    def setUp(self):
        fixtures.SalesAnalysisTests.setUp(self)
        SalesDataRevision.objects.update_or_create(domain="erp", defaults={"revision": 9})
        self.principal = fixtures.SalesAnalysisTests.principal
        for i in range(1000, 1101):
            make_line(i, f"continuation-{i}", channel=self.query["channel"]).save()
        self.query.update(limit=100, window="current")
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank":40,"label":"Admin"})
        now = timezone.now()
        AppUser.objects.update_or_create(email=self.principal.email, defaults={"role":role,"status":"active","scope":None,
            "display_name":"Synthetic","version":1,"created_at":now,"updated_at":now})

    def first(self, expired=False):
        if expired:
            with patch.object(signing.TimestampSigner,"timestamp",return_value=signing.b62_encode(int(time.time())-7200)):
                return analysis.read_page(self.principal,self.query)
        return analysis.read_page(self.principal,self.query)

    def params_for(self, first, **overrides):
        return QueryDict(urlencode({**{k:v for k,v in self.query.items() if k != "operation"},
            "cursor":first["pagination"]["nextCursor"],"expectedSourceRef":first["sourceRef"],
            "expectedRevision":first["sourceRevision"],"expectedLastId":first["items"][-1]["rowId"],**overrides}))

    def test_fresh_original_reader_and_expired_complete_replay_are_read_only(self):
        fresh=self.first()
        with patch.object(signing,"dumps",wraps=signing.dumps) as signer:
            actual=service.read_page(self.principal,self.params_for(fresh))
        if not actual["pagination"]["hasMore"]: signer.assert_not_called()
        self.assertEqual(actual,analysis.read_page(self.principal,{**self.query,"cursor":fresh["pagination"]["nextCursor"]}))
        first=self.first(expired=True);old=canonical(first);current=first;verifier=PageReconciler();verifier.consume(first)
        while not verifier.finished:
            cursor=current["pagination"]["nextCursor"]
            with CaptureQueriesContext(connection) as sql:
                current=service.read_page(self.principal,self.params_for(current))
            self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in sql.captured_queries))
            verifier.consume(current,request_cursor=cursor)
        self.assertEqual(verifier.result()["rowCount"],103)
        self.assertEqual(canonical(first),old)

    def test_parameter_set_fixed_page_and_no_implicit_window(self):
        first=self.first();good=self.params_for(first)
        for key in good:
            missing=good.copy();del missing[key]
            duplicate=good.copy();duplicate.appendlist(key,good[key])
            for params in (missing,duplicate):
                with self.subTest(key=key),self.assertRaises(ERROR):service.validate_request(params)
        for change in ({"limit":"1"},{"limit":"0100"},{"window":""},{"expectedLastId":"1.0"},{"expectedLastId":"01"},
                {"expectedLastId":"9007199254740992"},{"expectedRevision":"wrong"},{"expectedSourceRef":"x"},{"cursor":""},{"cursor":"x"*1601}):
            with self.subTest(change=change),self.assertRaises(ERROR):service.validate_request(self.params_for(first,**change))
        with self.assertRaises(ERROR):service.validate_request(QueryDict(good.urlencode()+"&operation=analysis_records"))

    def test_bad_signed_payload_salt_and_exact_scope_never_fall_back(self):
        first=self.first();payload=signing.loads(first["pagination"]["nextCursor"],salt=analysis.SALT)
        for expired in (False,True):
            timestamp=signing.b62_encode(int(time.time())-(7200 if expired else 0))
            with patch.object(signing.TimestampSigner,"timestamp",return_value=timestamp):
                tokens=[signing.dumps(payload,salt="wrong"),signing.dumps({**payload,"extra":1},salt=analysis.SALT),
                    *[signing.dumps({**payload,"lastId":v},salt=analysis.SALT) for v in (True,float(payload["lastId"]),0,-1)]]
            for token in tokens+[first["pagination"]["nextCursor"]+"x"]:
                with self.subTest(expired=expired),patch.object(analysis,"read_page") as reader,self.assertRaises(ERROR):
                    service.read_page(self.principal,self.params_for(first,cursor=token))
                reader.assert_not_called()
        for change in ({"channel":"other"},{"shop":"other"},{"window":"previous"},{"startDate":"2026-08-02"}):
            with self.subTest(change=change),self.assertRaises(ERROR):service.read_page(self.principal,self.params_for(first,**change))
        with self.assertRaises(ERROR):service.read_page(self.principal,self.params_for(first,expectedLastId=int(payload["lastId"])+1))

    def test_expiration_boundary_only_direct_expired_cause(self):
        first=self.first();original=analysis.read_page;actual_loads=signing.loads;clock=int(time.time())+7200;count=0;calls=[]
        def loads(*args,**kwargs):
            nonlocal count
            count+=1
            if count==1:return actual_loads(*args,**kwargs)
            with patch("django.core.signing.time.time",return_value=clock):return actual_loads(*args,**kwargs)
        def reader(principal,query):
            calls.append(query["cursor"])
            return original(principal,query)
        with patch.object(signing,"loads",side_effect=loads),patch.object(signing.TimestampSigner,"timestamp",return_value=signing.b62_encode(clock)),patch.object(analysis,"read_page",side_effect=reader):
            result=service.read_page(self.principal,self.params_for(first))
        self.assertEqual(result["sourceRef"],first["sourceRef"]);self.assertEqual(len(calls),2);self.assertNotEqual(*calls)
        for cause in (None,signing.BadSignature("bad"),ValueError("bad")):
            error=SalesRequestError("invalid");error.__cause__=cause
            with patch.object(analysis,"read_page",side_effect=error),patch.object(signing,"dumps") as signer,self.assertRaises(ERROR):
                service.read_page(self.principal,self.params_for(first))
            signer.assert_not_called()

    def test_real_actor_and_late_permission_version_fail_closed(self):
        first=self.first();original=analysis.read_page
        for principal in (Principal("absent@example.test","","admin",None),Principal("local-admin@teruisi.local","","admin",None),
                Principal(self.principal.email,"","viewer",None),Principal(self.principal.email,"","admin",{"platforms":[]})):
            with self.assertRaises(ERROR) as caught:service.read_page(principal,self.params_for(first))
            self.assertEqual(caught.exception.status,403)
        for change in ({"version":2},{"status":"disabled"},{"scope":{"platforms":["京东"]}}):
            def changed(*args):
                page=original(*args);AppUser.objects.filter(email=self.principal.email).update(**change);return page
            with patch.object(analysis,"read_page",side_effect=changed),self.assertRaises(ERROR) as caught:
                service.read_page(self.principal,self.params_for(first))
            self.assertEqual(caught.exception.status,403)
            AppUser.objects.filter(email=self.principal.email).update(version=1,status="active",scope=None)

    def test_revision_missing_changed_or_corrupt_cannot_continue(self):
        first=self.first();original=analysis.read_page
        def changed(*args):
            page=original(*args)
            SalesDataRevision.objects.filter(domain="erp").update(revision=10)
            return page
        with patch.object(analysis,"read_page",side_effect=changed),self.assertRaises(ERROR):service.read_page(self.principal,self.params_for(first))
        SalesDataRevision.objects.filter(domain="erp").delete()
        with self.assertRaises(ERROR) as caught:service.read_page(self.principal,self.params_for(first))
        self.assertEqual(caught.exception.status,503)

    def test_actual_minimal_reader_cannot_read_private_users_or_write(self):
        if connection.vendor!="postgresql":self.skipTest("Real PostgreSQL required")
        from psycopg import sql
        first=self.first(expired=True);role="sales_continuation_"+uuid.uuid4().hex[:10]
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user");original_role=cursor.fetchone()[0]
            cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(sql.Identifier(role)))
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            for table in ("sales_order_lines","sales_data_revisions"):
                cursor.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(table),sql.Identifier(role)))
            cursor.execute(sql.SQL("GRANT SELECT (email,role,status,scope,version) ON access_control_users TO {}").format(sql.Identifier(role)))
        try:
            with transaction.atomic(),connection.cursor() as cursor:
                cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                self.assertEqual(service.read_page(self.principal,self.params_for(first))["sourceRef"],first["sourceRef"])
                for query in ("SELECT display_name FROM access_control_users LIMIT 1","UPDATE access_control_users SET status='disabled'","DELETE FROM sales_order_lines"):
                    with self.assertRaises(DatabaseError),transaction.atomic():cursor.execute(query)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(original_role)))
                cursor.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    @patch.dict("os.environ",{"TERUISI_DJANGO_INTERNAL_SECRET":TEST_SECRET})
    def test_signed_route_headers_and_reader_writer_split(self):
        import sales.urls
        first=self.first(expired=True);url=PATH+"?"+self.params_for(first).urlencode()
        response=self.client.get(url,headers=signed_headers(url,email=self.principal.email))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response["X-Sales-Data-Revision"],first["sourceRevision"])
        self.assertEqual(response["X-Sales-Source-Revision"],first["sourceRevision"])
        self.assertIn("no-store",response["Cache-Control"])
        self.assertEqual(self.client.post(url).status_code,405)
        self.assertIn(self.client.get(url).status_code,(401,403))
        for role in ("viewer","analyst","operator"):
            self.assertEqual(self.client.get(url,headers=signed_headers(url,email=self.principal.email,role=role)).status_code,403)
        try:
            for role,status in (("reader",200),("sales_writer",404)):
                with override_settings(DJANGO_PROCESS_ROLE=role):
                    module=ModuleType("isolated_sales_continuation_"+role)
                    module.urlpatterns=[path("api/sales/",include(importlib.reload(sales.urls).urlpatterns))]
                    clear_url_caches()
                    with override_settings(ROOT_URLCONF=module):
                        response=self.client.get(url,headers=signed_headers(url,email=self.principal.email))
                        self.assertEqual(response.status_code,status,response.content)
        finally:
            importlib.reload(sales.urls);clear_url_caches()
