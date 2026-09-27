"""Isolated PostgreSQL checks for the default-closed market material sidecar."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier

from django import test as djtest
from django.db import DatabaseError, connection, connections, transaction

from access_control.models import AppUser

from . import business_market_v2_material_admission as service
from . import business_market_v2_parked_creation as parked
from . import models as m
from . import test_business_promotion_market_admission as fixtures
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2MaterialAdmissionTests(djtest.TransactionTestCase):
    user = fixtures.PromotionMarketAdmissionTests.user
    request_body = fixtures.PromotionMarketAdmissionTests.request_body
    current_catalog = fixtures.PromotionMarketAdmissionTests.current_catalog
    create_fixed_report = fixtures.PromotionMarketAdmissionTests.create_fixed_report
    planned_evidence_body = fixtures.PromotionMarketAdmissionTests.planned_evidence_body
    setUp = fixtures.PromotionMarketAdmissionTests.setUp
    selector = fixtures.PromotionMarketAdmissionTests.selector

    def parked_id(self):
        return parked.create({"schemaVersion": parked.REQUEST_SCHEMA,
            "clientRequestId": "market-v2-material-target",
            "sourceReportId": self.report.id,
            "marketSelector": self.selector()}, self.admin)["item"]["id"]

    def test_owner_replays_three_tables_and_exits_final_fence_without_publication(self):
        report_id = self.parked_id()
        phases = []
        value = service.prepare_candidate(report_id, self.admin,
            checkpoint=phases.append)
        candidate = value["candidate"]
        self.assertEqual(candidate["reportId"], report_id)
        self.assertEqual(candidate["sourceReportId"], self.report.id)
        self.assertEqual(candidate["tableViews"], ["price_band_summary",
            "price_band_members", "rank_entry_exit"])
        self.assertEqual(candidate["tableSpecDigests"],
            [digest(spec) for spec in value["tableSpecJson"]])
        self.assertEqual(candidate["manifestJsonSha256"],
            digest(value["manifestJson"]))
        self.assertEqual(candidate["manifestDigest"],
            digest(value["manifestBodyJson"]))
        self.assertTrue(candidate["materialPrepared"])
        self.assertFalse(candidate["authorityVerified"])
        self.assertFalse(candidate["agentDispatchSupported"])
        self.assertFalse(candidate["renderer8Supported"])
        self.assertIn({"stage": "market_report_material", "phase": "complete"},
            phases)
        self.assertFalse(m.AiBusinessMarketV2Material.objects.exists())
        self.assertEqual(m.AiReportRun.objects.get(pk=report_id).workflow.status,
            "paused")

    def test_ordinary_writer_cannot_submit_or_forge_sidecar(self):
        report_id = self.parked_id()
        with self.assertRaises(AiError):
            service.submit_candidate(report_id, self.admin)
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT public.ai_market_v2_attest_material("+
                    ",".join(["%s"]*8)+")", [report_id, "{}", "{}",
                    "{}", "{}", "{}", "{}", "{}"])
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessMarketV2Material.objects.create(report_id=report_id,
                source_report_id=self.report.id, source_snapshot_digest="0"*64,
                source_workflow_input_digest="0"*64, selector_digest="0"*64,
                algorithms_digest="0"*64, manifest_digest="0"*64,
                manifest_json_sha256="0"*64, summary_digest="0"*64,
                table_spec_digests_json="[]", manifest_json="{}",
                summary_json="{}")
        self.assertFalse(m.AiBusinessMarketV2Material.objects.exists())

    def test_attestor_role_stays_nologin_uninherited_without_direct_table_access(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
                "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname=%s", [service.ATTESTOR])
            self.assertEqual(cursor.fetchone(), (False,) * 7)
            cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT'),"
                "has_table_privilege(%s,%s,'INSERT'),"
                "has_function_privilege(%s,%s,'EXECUTE')", [
                    service.ATTESTOR, "public.ai_business_market_v2_materials",
                    service.ATTESTOR, "public.ai_business_market_v2_materials",
                    service.ATTESTOR, "public.ai_market_v2_attest_material("
                    "text,text,text,text,text,text,text,text)"])
            self.assertEqual(cursor.fetchone(), (False, False, True))
            for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
                cursor.execute("SELECT to_regrole(%s)", [role])
                if cursor.fetchone()[0] is None:
                    continue
                cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT'),"
                    "has_table_privilege(%s,%s,'INSERT'),"
                    "has_function_privilege(%s,%s,'EXECUTE')", [role,
                    "public.ai_business_market_v2_materials", role,
                    "public.ai_business_market_v2_materials", role,
                    "public.ai_market_v2_attest_material("
                    "text,text,text,text,text,text,text,text)"])
                self.assertEqual(cursor.fetchone(), (False, False, False))

    @staticmethod
    def _attest_as_role(report_id, value):
        # Disposable isolated PG is owned by a superuser.  This changes only
        # the test session; the production role remains NOLOGIN.
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION AUTHORIZATION " + service.ATTESTOR)
            try:
                cursor.execute("SELECT public.ai_market_v2_attest_material("+
                    ",".join(["%s"]*8)+")", [report_id, value["manifestJson"],
                    value["manifestBodyJson"], value["summaryJson"],
                    value["summaryBodyJson"], *value["tableSpecJson"]])
            finally:
                cursor.execute("RESET SESSION AUTHORIZATION")

    def test_isolated_true_role_can_insert_once_and_reject_changed_material(self):
        report_id = self.parked_id()
        value = service.prepare_candidate(report_id, self.admin)
        self._attest_as_role(report_id, value)
        self._attest_as_role(report_id, value)
        sidecar = m.AiBusinessMarketV2Material.objects.get(pk=report_id)
        self.assertEqual(sidecar.source_report_id, self.report.id)
        self.assertEqual(sidecar.manifest_digest,
            value["candidate"]["manifestDigest"])
        self.assertEqual(sidecar.manifest_json_sha256,
            value["candidate"]["manifestJsonSha256"])
        self.assertEqual(m.AiBusinessMarketV2Material.objects.count(), 1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessMarketV2Material.objects.filter(pk=report_id).update(
                manifest_digest="0"*64)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessMarketV2Material.objects.filter(pk=report_id).delete()
        forged = {**value, "tableSpecJson": ("{}", *value["tableSpecJson"][1:])}
        with self.assertRaises(DatabaseError):
            self._attest_as_role(report_id, forged)
        forged_digest = {**value, "manifestBodyJson": "{}"}
        with self.assertRaises(DatabaseError):
            self._attest_as_role(report_id, forged_digest)
        self.assertEqual(m.AiBusinessMarketV2Material.objects.count(), 1)
        migration = import_module(
            "ai_assistant.migrations.0045_business_market_v2_material_attestation")
        with connection.schema_editor() as editor:
            with self.assertRaisesRegex(RuntimeError, "cannot discard"):
                migration.uninstall(None, editor)

    def test_isolated_true_role_rejects_cross_selector_and_revoked_actor(self):
        first = self.parked_id()
        value = service.prepare_candidate(first, self.admin)
        changed = self.selector()
        changed["bands"] = deepcopy(changed["bands"])
        changed["bands"][0]["key"] += "_other"
        second = parked.create({"schemaVersion": parked.REQUEST_SCHEMA,
            "clientRequestId": "market-v2-other-selector",
            "sourceReportId": self.report.id,
            "marketSelector": changed}, self.admin)["item"]["id"]
        with self.assertRaises(DatabaseError):
            self._attest_as_role(second, value)
        AppUser.objects.filter(email=self.admin.email).update(status="inactive")
        with self.assertRaises(DatabaseError):
            self._attest_as_role(first, value)
        self.assertFalse(m.AiBusinessMarketV2Material.objects.exists())

    def test_wrong_owner_cannot_prepare_candidate(self):
        report_id = self.parked_id()
        outside = self.user("market-material-outside@example.invalid", "admin", None)
        with self.assertRaises(AiError):
            service.prepare_candidate(report_id, outside)

    def test_concurrent_identical_attestors_converge_to_one_row(self):
        report_id = self.parked_id()
        value = service.prepare_candidate(report_id, self.admin)
        barrier = Barrier(2)

        def submit():
            try:
                barrier.wait(timeout=10)
                self._attest_as_role(report_id, value)
                return True
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(outcomes, [True, True])
        self.assertEqual(m.AiBusinessMarketV2Material.objects.count(), 1)
