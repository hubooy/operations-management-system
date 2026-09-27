"""Isolated PostgreSQL role target for closed 0071 report links."""
from copy import deepcopy
from importlib import import_module
import json
from uuid import uuid4

import psycopg
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from access_control.models import AppUser
from finance.models import FinanceDataRevision
from netshop.models import NetshopDataRevision
from business_analysis import evidence_v4, mapping_plan

from . import (business_v4_report_link_sql as link, business_reports,
    models as m)
from .control_models import AiWriteAuthority
from .policy import canonical, digest, mutation
from .test_business_integrated_guard import BusinessIntegratedGuardTests as fixture


@override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class V4ReportLinkRoleTarget(TransactionTestCase):
    user = fixture.user
    call = fixture.call
    bundle = fixture.bundle
    input_for = fixture.input_for
    insert = fixture.insert
    seed = fixture.seed
    collect_body = fixture.collect_body
    setUp = fixture.setUp

    def database(self):
        item = connection.settings_dict
        return psycopg.connect(host=item["HOST"], port=item["PORT"],
            dbname=item["NAME"], user=item["USER"],
            password=item["PASSWORD"], autocommit=True)

    def test_catalog_detects_reader_execute_drift(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL 0071")
        migration = import_module(
            "ai_assistant.migrations.0071_business_v4_report_source_link")
        with transaction.atomic(), connection.cursor() as cursor:
            migration.verify_catalog(cursor)
            cursor.execute("REVOKE EXECUTE ON FUNCTION " + link.READ +
                " FROM teruisi_ai_reader")
            with self.assertRaises(RuntimeError):
                migration.verify_catalog(cursor)
            transaction.set_rollback(True)

    def three_window_v2(self):
        """Collect a real small sealed-v2 directory with three empty baselines."""
        body = deepcopy(self.evidence_body)
        body["clientRequestId"] = "v4-link-three-v2"
        request = {"schemaVersion": "business-analysis-request-v1",
            "question": "合成商品关联，不调用模型",
            "requestedDimensions": ["shop"],
            "requestedWindows": ["current", "previous", "yearAgo"]}
        body["analysisRequest"] = request
        source_rows = deepcopy(self.sources)
        for source in self.sources:
            if source["key"] == "master":
                continue
            for window in ("previous", "yearAgo"):
                source_rows.append({"key": source["key"]+"-"+window,
                    "domain": source["domain"],
                    "query": {**source["query"], "window": window}})
        body["sources"] = source_rows
        self.parent = self.collect_body(body)
        self.sources = source_rows
        self.plan = mapping_plan.build(source_rows,
            [{"salesKey": "sales", "masterKey": "master"}])
        return request

    def synthetic_v4_identity(self, request, suffix, *, shop=None,
                              start_date=None, end_date=None):
        """SQL-only identity vector; bypasses v4 page/HMAC guards in test DB.

        This validates 0071 creation-time SQL mechanics, never a true v4
        application seal. The 0071 read result must keep appHmacVerified=false.
        """
        base = self.query
        shop = shop or base["shop"]
        start_date = start_date or base["startDate"]
        end_date = end_date or base["endDate"]
        net = NetshopDataRevision.objects.get(domain="netshop")
        finance, _ = FinanceDataRevision.objects.get_or_create(
            domain="finance", defaults={"revision": 0,
                "source_digest": "0" * 64})
        net_revision = f"{net.revision}:{net.source_digest[:12]}"
        finance_revision = f"{finance.revision}:{finance.source_digest}"
        period = {"startDate": start_date, "endDate": end_date}
        sources = [{"key": f"promotion-{window}", "domain": "netshop",
            "query": {"platform": "京东", "shop": shop,
                "dataset": "promotion", **period, "window": window}}
            for window in ("current", "previous", "yearAgo")]
        sources.append({"key": "finance-context", "domain": "finance",
            "query": {"months": [start_date[:7]],
                "scope": {"scope_key": "shop:"+shop,
                    "scope_type": "shop", "scope_name": shop,
                    "group_name": "京东组"},
                "analysisPeriod": period}})
        measures = [{"sourceKey": row["key"], "measuredRowCount": 0,
            "maxRowUtf8Bytes": 0, "pageEnvelopeUtf8Bytes": 2048,
            "sourceRevisionHint": (finance_revision if row["domain"] ==
                "finance" else net_revision)} for row in sources]
        plan = evidence_v4.build_plan(client_request_id="v4-link-"+suffix,
            sources=sources, measurements=measures,
            analysis_request=request)
        raw = canonical(plan)
        parent_id = "v4-link-"+suffix
        now = timezone.now()
        tables = ("ai_business_v4_runs", "ai_business_v4_sources",
            "ai_business_v4_validation_attempts", "ai_business_v4_seals")
        with transaction.atomic(), connection.cursor() as cursor:
            for table in tables:
                cursor.execute("ALTER TABLE public."+table+
                    " DISABLE TRIGGER USER")
            try:
                parent = m.AiBusinessV4Run.objects.create(id=parent_id,
                    owner_email=self.admin.email, scope_json="null",
                    client_request_id=plan["clientRequestId"],
                    plan_json=raw, plan_digest=digest(raw),
                    run_identity_digest=plan["runIdentityDigest"],
                    status="sealed", collection_status="manual",
                    version=6, page_count=4, row_count=0,
                    stored_bytes=128, created_at=now)
                claims = []
                for entry in plan["sourcePlans"]:
                    revision = (finance_revision if entry["domain"] ==
                        "finance" else net_revision)
                    ref = digest([suffix, entry["sourceKey"]])
                    m.AiBusinessV4Source.objects.create(
                        id=f"{suffix}-{entry['ordinal']}", run=parent,
                        source_key=entry["sourceKey"],
                        ordinal=entry["ordinal"], domain=entry["domain"],
                        temporal_role=entry["temporalRole"],
                        query_json=canonical(entry["query"]),
                        query_digest=entry["queryDigest"],
                        source_identity_digest=entry["sourceIdentityDigest"],
                        source_revision_hint=entry["sourceRevisionHint"],
                        source_ref=ref, source_revision=revision,
                        checkpoint_json="{}", version=2, page_count=1,
                        row_count=0, stored_bytes=32, finished=True,
                        created_at=now, updated_at=now)
                    claims.append({"sourceKey": entry["sourceKey"],
                        "domain": entry["domain"],
                        "queryDigest": entry["queryDigest"],
                        "sourceRef": ref, "sourceRevision": revision,
                        "sourceVersion": 2, "pageCount": 1,
                        "rowCount": 0, "storedBytes": 32,
                        "liveRevision": revision,
                        "revisionFreshness": "current_revision"})
                actor = AppUser.objects.get(email=self.admin.email)
                attempt = m.AiBusinessV4ValidationAttempt.objects.create(
                    id="attempt-"+suffix, run=parent, run_version=5,
                    plan_digest=parent.plan_digest,
                    directory_digest="d" * 64,
                    actor_email=actor.email, actor_version=actor.version,
                    key_id="1" * 16, created_at=now)
                seal_body = {"runId": parent.id,
                    "evidenceVersion": parent.version,
                    "planDigest": parent.plan_digest,
                    "reportGenerationSupported": False,
                    "sources": claims}
                raw_body = canonical(seal_body)
                seal = m.AiBusinessV4Seal.objects.create(run=parent,
                    attempt=attempt, evidence_version=parent.version,
                    body_json=raw_body, body_digest=digest(raw_body),
                    body_mac="a" * 64, key_id="1" * 16,
                    created_at=now)
            finally:
                # Drain deferred FK events before ALTER TABLE restores the
                # test-only user triggers within this transaction.
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                for table in reversed(tables):
                    cursor.execute("ALTER TABLE public."+table+
                        " ENABLE TRIGGER USER")
        return parent, seal

    def workflow_for(self, bundle):
        snapshot, data, _ = bundle
        graph = business_reports.graph_v2()
        with mutation(self.admin):
            return m.AiWorkflowRuns.objects.create(
                id="flow-"+snapshot["reportId"],
                owner_email=self.admin.email, scope_json="null",
                client_request_id="flow-"+snapshot["reportId"],
                request_digest="a" * 64, name="合成关联存储",
                graph_json=canonical(graph), graph_digest=digest(graph),
                input_json=canonical(data), dry_run=True)

    def insert_with_intent(self, bundle, flow, parent, seal, *,
                           expected_success, drift_authority=False,
                           expected_error="ai_v4_report_link_report_identity_invalid"):
        snapshot, _, _ = bundle
        authority_fields = {"status": "postgres", "authority_epoch": uuid4(),
            "cutover_id": "v4-report-link-isolated",
            "migration_verify_run_id": "v4-report-link-isolated",
            "activated_at": timezone.now()}
        authority, created = AiWriteAuthority.objects.get_or_create(id=1,
            defaults=authority_fields)
        if not created and authority.status == "d1":
            AiWriteAuthority.objects.filter(pk=1).update(**authority_fields)
            authority.refresh_from_db()
        elif not created and authority.status != "postgres":
            self.fail("unexpected isolated AI write authority state")
        report_id = snapshot["reportId"]
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_writer")
            db.execute("BEGIN")
            issued = False
            db.execute("SELECT set_config('teruisi.ai_epoch',%s,true),"
                "set_config('teruisi.ai_cutover',%s,true)",
                [str(authority.authority_epoch), authority.cutover_id])
            try:
                db.execute("SELECT " + link.ISSUE.split("(",1)[0] +
                    "(%s,%s,%s,%s,%s,%s)", [report_id, parent.id,
                    self.admin.email, self.parent.id,
                    self.parent.state_json and json.loads(
                        self.parent.state_json)["sealedDigest"],
                    seal.body_digest])
                issued = True
                if drift_authority:
                    db.execute("SELECT set_config('teruisi.ai_cutover',"
                        "'unexpected-cutover',true)")
                db.execute("SELECT " + link.CREATE_REPORT.split("(",1)[0] +
                    "(%s,%s,%s,%s,%s,%s)",
                    [report_id,self.admin.email,report_id,
                     digest(snapshot),flow.id,canonical(snapshot)])
                db.execute("COMMIT")
            except Exception as error:
                db.execute("ROLLBACK")
                if expected_success:
                    raise
                if not issued or expected_error not in str(error):
                    raise AssertionError("0071 negative did not reach the "
                        "expected authority or identity guard") from error
                return False
        if not expected_success:
            self.fail("wrong shop/date was linked")
        return True

    def test_same_transaction_link_and_cross_shop_period_refuse(self):
        """Synthetic SQL identity vector; not a true signed-v4 source test."""
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL 0071")
        request = self.three_window_v2()
        parent, seal = self.synthetic_v4_identity(request, "same")
        bundle = self.bundle()
        bundle[0]["scope"] = {"platform": "京东",
            "shop": self.query["shop"],
            "startDate": self.query["startDate"],
            "endDate": self.query["endDate"]}
        flow = self.workflow_for(bundle)
        self.assertTrue(self.insert_with_intent(bundle, flow, parent, seal,
            expected_success=True))
        report_id = bundle[0]["reportId"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM " + link.INTENTS)
            self.assertEqual(cursor.fetchone(), (1,))
            cursor.execute("SELECT count(*) FROM " + link.LINKS)
            self.assertEqual(cursor.fetchone(), (1,))
        actor = AppUser.objects.get(email=self.admin.email)
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
            value = db.execute("SELECT " + link.READ.split("(",1)[0] +
                "(%s,%s,%s)", [report_id,self.admin.email,
                actor.version]).fetchone()[0]
        self.assertEqual(value["reportId"], report_id)
        self.assertTrue(value["creationTimeLinkPersisted"])
        for key in ("appHmacVerified", "authorityVerified",
                    "reportGenerationSupported", "agentDispatchSupported",
                    "rendererRegistered", "downloadSupported"):
            self.assertIs(value[key], False)
        self.assertEqual(value["sourceBindings"]["shop"],
            self.query["shop"])
        self.assertEqual(len(value["sourceBindings"]["sources"]), 4)
        for suffix, options in (("other-shop", {"shop": "另一店"}),
                                ("other-date", {"start_date": "2026-08-02",
                                    "end_date": "2026-08-02"})):
            bad_parent, bad_seal = self.synthetic_v4_identity(request,
                suffix, **options)
            bad_bundle = self.bundle()
            bad_bundle[0]["scope"] = bundle[0]["scope"]
            bad_flow = self.workflow_for(bad_bundle)
            with self.subTest(suffix=suffix):
                self.assertFalse(self.insert_with_intent(bad_bundle,
                    bad_flow,bad_parent,bad_seal,expected_success=False))
                self.assertFalse(m.AiReportRun.objects.filter(
                    pk=bad_bundle[0]["reportId"]).exists())
        drift_parent, drift_seal = self.synthetic_v4_identity(request,
            "authority-drift")
        drift_bundle = self.bundle()
        drift_bundle[0]["scope"] = bundle[0]["scope"]
        drift_flow = self.workflow_for(drift_bundle)
        self.assertFalse(self.insert_with_intent(drift_bundle, drift_flow,
            drift_parent, drift_seal, expected_success=False,
            drift_authority=True,
            expected_error="ai_v4_report_link_create_authority_invalid"))
        with connection.cursor() as cursor:
            for table in (link.INTENTS, link.LINKS):
                cursor.execute("SELECT count(*) FROM " + table)
                self.assertEqual(cursor.fetchone(), (1,))

    def test_old_report_cannot_be_backfilled_and_reader_has_no_direct_rows(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL 0071")
        migration = import_module(
            "ai_assistant.migrations.0071_business_v4_report_source_link")
        with connection.cursor() as cursor:
            migration.verify_catalog(cursor)
        report, _ = self.seed()
        with connection.cursor() as cursor:
            for table in (link.INTENTS, link.LINKS):
                cursor.execute("SELECT count(*) FROM " + table)
                self.assertEqual(cursor.fetchone(), (0,))
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_writer")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("INSERT INTO public.ai_report_runs (id) "
                    "VALUES ('forbidden-direct-report')")
            with self.assertRaises(psycopg.Error):
                db.execute("SELECT " + link.ISSUE.split("(",1)[0] +
                    "(%s,%s,%s,%s,%s,%s)", [report.id, "missing-v4",
                    self.admin.email, self.parent.id,
                    "a" * 64, "b" * 64])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM " + link.INTENTS)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("INSERT INTO " + link.LINKS +
                    " (report_id) VALUES ('forbidden')")
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
            with self.assertRaises(psycopg.Error):
                db.execute("SELECT " + link.READ.split("(",1)[0] +
                    "(%s,%s,%s)",
                    [report.id, self.admin.email, 1])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM " + link.LINKS)
        with connection.cursor() as cursor:
            for table in (link.INTENTS, link.LINKS):
                cursor.execute("SELECT count(*) FROM " + table)
                self.assertEqual(cursor.fetchone(), (0,))
            migration.verify_catalog(cursor)
        self.assertEqual(m.AiReportRun.objects.get(pk=report.id).snapshot_json,
            report.snapshot_json)
