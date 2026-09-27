"""Isolated real non-superuser 0070 signer and bounded owning reader.

The candidate receipt remains unpublished: 0068 MAC does not bind the 0070
ticket/claim and 0073 proof is a separate, deliberately unbridged table.
"""
from contextlib import contextmanager
from importlib import import_module
import os
import secrets
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest
from django.apps import apps
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
import psycopg
from psycopg import sql

from business_analysis import promotion_budget_limited_signer_v11 as limited
from . import business_promotion_budget_v11_attest_step as attest_step
from . import business_promotion_budget_v11_durable_stage as stage
from . import business_promotion_budget_v11_preflight as preflight
from . import business_volume_files
from . import configuration
from . import test_business_promotion_budget_v11_durable_stage as fixture
from . import test_business_promotion_approved_content as approved_fixture
from .database_contract import provision
from .policy import AiError


identity = import_module(
    "ai_assistant.migrations.0070_business_promotion_budget_v11_limited_identity")
attestation = import_module(
    "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")


class SyntheticKey:
    def __init__(self, key): self.key = key
    def get_key(self, key_id):
        if key_id != "synthetic-limited-bridge":
            raise limited.SignerBlocked("synthetic key identity changed")
        return self.key


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test",
    AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED=True)
class BudgetV11LimitedSignerRoleTests(djtest.TransactionTestCase):
    user = fixture.BudgetV11DurableStageTests.user
    call = fixture.BudgetV11DurableStageTests.call
    collect_body = fixture.BudgetV11DurableStageTests.collect_body
    bundle = fixture.BudgetV11DurableStageTests.bundle
    input_for = fixture.BudgetV11DurableStageTests.input_for
    insert = fixture.BudgetV11DurableStageTests.insert
    seed = fixture.BudgetV11DurableStageTests.seed
    setUp = fixture.BudgetV11DurableStageTests.setUp
    request_body = fixture.BudgetV11DurableStageTests.request_body
    current_catalog = fixture.BudgetV11DurableStageTests.current_catalog
    create_fixed_report = fixture.BudgetV11DurableStageTests.create_fixed_report
    base = fixture.BudgetV11DurableStageTests.base
    read = fixture.BudgetV11DurableStageTests.read
    append = fixture.BudgetV11DurableStageTests.append
    package = fixture.BudgetV11DurableStageTests.package
    promotion = fixture.BudgetV11DurableStageTests.promotion
    complete = fixture.BudgetV11DurableStageTests.complete
    running_job = fixture.BudgetV11DurableStageTests.running_job
    five_completed = fixture.BudgetV11DurableStageTests.five_completed
    approved = fixture.BudgetV11DurableStageTests.approved
    _complete_budget_report = fixture.BudgetV11DurableStageTests._complete_budget_report
    _writer = fixture.BudgetV11DurableStageTests._writer
    _stage = fixture.BudgetV11DurableStageTests._stage
    complete_flow = approved_fixture.PromotionApprovedContentTests.complete_flow
    databases = {"default"}
    _installed_candidate = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.ensure_connection()
        cls.reader_password = secrets.token_hex(32)
        provision(connection.connection, cls.reader_password,
            secrets.token_hex(32))
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure(%s)",
                [fixture.sql.STAGE_SIGNATURE])
            installed = cursor.fetchone()[0] is not None
        if not installed:
            fixture.sql.install(apps, SimpleNamespace(connection=connection))
            cls._installed_candidate = True

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._installed_candidate:
                fixture.sql.uninstall(apps,
                    SimpleNamespace(connection=connection))
        finally:
            super().tearDownClass()

    @staticmethod
    def _service(role, password):
        db = settings.DATABASES["default"]
        if (settings.DJANGO_ENVIRONMENT != "test" or
                db["NAME"] != "test_teruisi_ai_rehearsal" or
                db["HOST"] != "127.0.0.1" or
                not 55440 <= int(db["PORT"]) <= 55999 or
                str(db["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")):
            raise AssertionError("limited signer requires isolated PG")
        return psycopg.connect(host=db["HOST"],port=db["PORT"],
            dbname=db["NAME"],user=role,password=password,autocommit=True)

    @contextmanager
    def _owning_reader(self):
        db = connection.settings_dict
        before = (db["USER"],db["PASSWORD"])
        connection.close()
        try:
            db["USER"] = "teruisi_ai_reader"
            db["PASSWORD"] = self.reader_password
            connection.ensure_connection()
            with self.settings(DJANGO_PROCESS_ROLE="ai_reader",
                    DJANGO_EXPECT_READ_ONLY=True):
                yield
        finally:
            connection.close()
            db["USER"],db["PASSWORD"] = before
            connection.ensure_connection()

    def test_ticket_claim_stops_at_closed_reader_without_mac(self):
        # Synthetic same-process credential switch only; production needs a
        # separately launched reader process and credential custody.
        with self._owning_reader():
            self.assertEqual((settings.DJANGO_PROCESS_ROLE,
                settings.DJANGO_EXPECT_READ_ONLY),("ai_reader",True))
            with connection.cursor() as cursor:
                cursor.execute("SELECT session_user,current_user,"
                    "(SELECT rolsuper FROM pg_catalog.pg_roles WHERE "
                    "rolname=session_user),current_database(),"
                    "COALESCE(inet_server_addr()::text,''),"
                    "inet_server_port()")
                limited._isolated_identity(cursor.fetchone(),
                    "teruisi_ai_reader",
                    int(settings.DATABASES["default"]["PORT"]))
                cursor.execute("SELECT has_table_privilege(current_user,"
                    "'public.ai_models','SELECT'),"
                    "has_column_privilege(current_user,'public.ai_models',"
                    "'generation_options_json','SELECT'),"
                    "has_column_privilege(current_user,'public.ai_models',"
                    "'api_key_encrypted','SELECT')")
                self.assertEqual(cursor.fetchone(),(False,True,False))
                for table in ("ai_agent_provider_dispatches",
                        "ai_agent_provider_results",
                        "ai_agent_tool_dispatches",
                        "ai_agent_tool_results"):
                    cursor.execute("SELECT has_table_privilege(current_user,%s,"
                        "'SELECT')",["public."+table])
                    self.assertEqual(cursor.fetchone(),(False,))
            with CaptureQueriesContext(connection) as queries:
                configuration.resolve_model()
            model_queries=[item["sql"] for item in queries
                if 'FROM "ai_models"' in item["sql"]]
            self.assertEqual(len(model_queries),1)
            self.assertNotIn('"api_key_encrypted"',model_queries[0])
            self.assertNotIn('"api_key_suffix"',model_queries[0])
            self.assertNotIn('"last_test_result"',model_queries[0])
        report = self._complete_budget_report()
        self.complete_flow(report)
        row = self._stage(report)
        with self._service(settings.DATABASES["default"]["USER"],
                settings.DATABASES["default"]["PASSWORD"]) as admin:
            admin.execute("SET SESSION AUTHORIZATION " +
                "teruisi_ai_budget_v11_attestor")
            try:
                with patch.object(stage.approved_content.runtime.transport,
                        "catalog", side_effect=self.current_catalog):
                    attested = attest_step.attest_staged(admin,row.id,self.admin,
                        enabled=True)
            finally:
                admin.execute("RESET SESSION AUTHORIZATION")
        self.assertEqual(attested["status"],"staged_attested_unpublished")
        key = secrets.token_bytes(32)
        passwords = {role:secrets.token_urlsafe(32) for role in
            (identity.v2.ATTEST,identity.v2.SIGN)}
        with self._service(settings.DATABASES["default"]["USER"],
                settings.DATABASES["default"]["PASSWORD"]) as admin:
            for role,password in passwords.items():
                admin.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role),sql.Literal(password)))
        connection.close()
        try:
            with self._service(identity.v2.ATTEST,
                    passwords[identity.v2.ATTEST]) as db:
                issued = db.execute("SELECT public.ai_budget_v11_issue_proof_ticket_v2("
                    "%s,%s,%s)",[row.id,row.attempt,
                    attested["attestationSha256"]]).fetchone()[0]
            with self._service(identity.v2.SIGN,
                    passwords[identity.v2.SIGN]) as signer:
                observed = signer.execute("SELECT session_user,current_user,"
                    "(SELECT rolsuper FROM pg_catalog.pg_roles WHERE "
                    "rolname=session_user),current_database(),"
                    "COALESCE(inet_server_addr()::text,''),"
                    "inet_server_port()").fetchone()
                print("isolated_signer_address=" + str(observed[4]) +
                    "; addressType=" + type(observed[4]).__name__)
                limited._isolated_identity(observed, identity.v2.SIGN,
                    int(settings.DATABASES["default"]["PORT"]))
                reader = limited.BoundedRunReader(self.admin,
                    expected_port=int(settings.DATABASES["default"]["PORT"]))
                with self._owning_reader(), patch.object(
                        stage.approved_content.runtime.transport,
                        "catalog",side_effect=self.current_catalog):
                    signed = limited.sign_once(signer,reader,
                        ticket_id=issued["ticketId"],run_id=row.id,
                        attempt=row.attempt,
                        attestation_sha256=attested["attestationSha256"],
                        key_id="synthetic-limited-bridge",
                        key_provider=SyntheticKey(key),
                        isolated_candidate=True)
            self.assertEqual(signed["status"],"unknown_after_ticket_claim")
            self.assertEqual(signed["reasonCode"],"owning_reader_acl_denied")
            self.assertFalse(signed["retryAllowed"])
            self.assertFalse(signed["ticketBoundInMac"])
            self.assertFalse(signed["releaseAllowed"])
            self.assertNotIn("receiptText",signed)
            self.assertNotIn("receiptMac",signed)
        finally:
            with self._service(settings.DATABASES["default"]["USER"],
                    settings.DATABASES["default"]["PASSWORD"]) as admin:
                for role in passwords:
                    admin.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                        sql.Identifier(role)))
                self.assertEqual(admin.execute("SELECT count(*) FROM public."
                    "protected_business_budget_v11_proof_tickets").fetchone(),
                    (1,))
                self.assertEqual(admin.execute("SELECT count(*) FROM public."
                    "protected_business_budget_v11_proof_ticket_claims").fetchone(),
                    (1,))
                self.assertEqual(admin.execute("SELECT count(*) FROM public."
                    "protected_business_budget_v11_verifier_keys").fetchone(),
                    (0,))
        row.refresh_from_db()
        self.assertEqual((row.status,row.error_code),
            ("paused","renderer_unpublished"))
        with self.assertRaises(AiError):
            business_volume_files.chunk(row.id,1,"html",
                {"sequence":"1"},self.admin)
        with connection.cursor() as cursor:
            attestation.verify_catalog(cursor)
            identity.verify_catalog(cursor)
