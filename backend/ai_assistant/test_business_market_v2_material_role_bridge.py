"""Real session_user probes for narrow market v2 material metadata."""
from contextlib import contextmanager
import json
import secrets
from uuid import uuid4

from django import test as djtest
from django.db import DatabaseError, connection
from django.utils import timezone

from access_control.models import AppUser
from . import business_market_v2_admitted_paused as admitted
from . import business_market_v2_material_admission as material_owner
from . import models as m
from . import test_business_market_v2_material_admission as fixture
from .control_models import AiWriteAuthority
from .database_contract import provision


FUNCTION = "public.ai_market_v2_admitted_material_metadata"


@contextmanager
def session_role(role):
    if role not in {"teruisi_ai_reader", "teruisi_ai_writer"}:
        raise ValueError("Only exact isolated application roles are supported")
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION AUTHORIZATION " + role)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2MaterialRoleBridgeTests(djtest.TransactionTestCase):
    user = fixture.MarketV2MaterialAdmissionTests.user
    request_body = fixture.MarketV2MaterialAdmissionTests.request_body
    current_catalog = fixture.MarketV2MaterialAdmissionTests.current_catalog
    create_fixed_report = fixture.MarketV2MaterialAdmissionTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2MaterialAdmissionTests.planned_evidence_body
    selector = fixture.MarketV2MaterialAdmissionTests.selector
    parked_id = fixture.MarketV2MaterialAdmissionTests.parked_id
    _attest_as_role = staticmethod(fixture.MarketV2MaterialAdmissionTests._attest_as_role)

    def admitted(self, *, writer=False):
        parked_id = self.parked_id()
        prepared = material_owner.prepare_candidate(parked_id, self.admin)
        self._attest_as_role(parked_id, prepared)
        body = {"schemaVersion": admitted.REQUEST_SCHEMA,
            "clientRequestId": "role-bridge-admitted",
            "parkedReportId": parked_id}
        if writer:
            AiWriteAuthority.objects.filter(id=1).update(status="postgres",
                authority_epoch=uuid4(), cutover_id="market-role-bridge-isolated",
                migration_verify_run_id="market-role-bridge-isolated",
                activated_at=timezone.now())
            authority = AiWriteAuthority.objects.get(id=1)
            with djtest.override_settings(DJANGO_PROCESS_ROLE="ai_writer",
                    AI_WRITE_AUTHORITY_EPOCH=str(authority.authority_epoch),
                    AI_WRITE_CUTOVER_ID=authority.cutover_id), session_role(
                        "teruisi_ai_writer"):
                created = admitted.create(body, self.admin)
        else:
            created = admitted.create(body, self.admin)
        snapshot = json.loads(m.AiReportRun.objects.get(pk=created["reportId"]).snapshot_json)
        return parked_id, created, snapshot, prepared

    def setUp(self):
        fixture.MarketV2MaterialAdmissionTests.setUp(self)
        connection.ensure_connection()
        provision(connection.connection, secrets.token_hex(32), secrets.token_hex(32))

    def call(self, report_id, parked_id, snapshot, actor=None, version=None):
        actor = actor or self.admin.email.lower()
        if version is None:
            version = AppUser.objects.get(email=actor).version
        claim = snapshot["marketAdmission"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM " + FUNCTION + "(%s,%s,%s,%s,%s,%s)",
                [report_id, actor, version, parked_id,
                 claim["selectorDigest"], claim["manifestDigest"]])
            return cursor.fetchone()

    def test_real_writer_can_create_without_sidecar_select(self):
        parked_id, created, snapshot, prepared = self.admitted(writer=True)
        self.assertEqual(snapshot["marketAdmission"]["parkedReportId"], parked_id)
        self.assertEqual(snapshot["marketAdmission"]["manifestDigest"],
            prepared["candidate"]["manifestDigest"])
        with session_role("teruisi_ai_writer"):
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT report_id FROM "
                        "public.ai_business_market_v2_materials LIMIT 1")
            with self.assertRaises(DatabaseError):
                self.call(created["reportId"], parked_id, snapshot)
        self.assertEqual(m.AiBusinessMarketV2Material.objects.count(), 1)
        self.assertFalse(m.AiAgentJobs.objects.filter(
            workflow_run_id=created["workflowId"]).exists())

    def test_real_reader_gets_only_bound_metadata_and_no_table_select(self):
        parked_id, created, snapshot, prepared = self.admitted()
        outsider = self.user("market-role-bridge-outsider@example.invalid",
            "admin", None)
        version = AppUser.objects.get(email=self.admin.email).version
        with session_role("teruisi_ai_reader"):
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT manifest_json FROM "
                        "public.ai_business_market_v2_materials LIMIT 1")
            row = self.call(created["reportId"], parked_id, snapshot,
                version=version)
            self.assertEqual(len(row), 6)
            self.assertEqual(row[0], self.report.id)
            self.assertEqual(row[3], prepared["candidate"]["manifestDigest"])
            coverage = json.loads(row[4]) if type(row[4]) is str else row[4]
            self.assertEqual(coverage, {"currentDatePresent": True,
                "baselineDatePresent": True, "bothDatesPresent": True})
            for report_id, actor, actor_version, selector_digest in (
                    ("other-report", self.admin.email.lower(), version,
                        snapshot["marketAdmission"]["selectorDigest"]),
                    (created["reportId"], outsider.email.lower(),
                        AppUser.objects.get(email=outsider.email).version,
                        snapshot["marketAdmission"]["selectorDigest"]),
                    (created["reportId"], self.admin.email.lower(), version,
                        "0"*64)):
                with self.assertRaises(DatabaseError):
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT * FROM " + FUNCTION +
                            "(%s,%s,%s,%s,%s,%s)", [report_id, actor,
                            actor_version, parked_id, selector_digest,
                            snapshot["marketAdmission"]["manifestDigest"]])

    def test_acl_and_definer_properties_remain_narrow(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
                "has_function_privilege(%s,%s,'EXECUTE'),"
                "has_table_privilege(%s,%s,'SELECT'),"
                "has_table_privilege(%s,%s,'SELECT')", [
                    "teruisi_ai_reader", FUNCTION + "(text,text,bigint,text,text,text)",
                    "teruisi_ai_writer", FUNCTION + "(text,text,bigint,text,text,text)",
                    "teruisi_ai_reader", "public.ai_business_market_v2_materials",
                    "teruisi_ai_writer", "public.ai_business_market_v2_materials"])
            self.assertEqual(cursor.fetchone(), (True, False, False, False))
            cursor.execute("SELECT p.prosecdef,p.proconfig FROM pg_catalog.pg_proc p "
                "WHERE p.oid=to_regprocedure(%s)",
                [FUNCTION + "(text,text,bigint,text,text,text)"])
            protected, config = cursor.fetchone()
            self.assertTrue(protected)
            self.assertEqual({entry.replace(" ", "") for entry in (config or [])},
                {"search_path=pg_catalog,public"})
