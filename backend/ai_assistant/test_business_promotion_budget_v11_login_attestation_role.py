"""0073 isolated real-LOGIN checks; never activate a formal database role."""

from importlib import import_module
import hashlib
from types import SimpleNamespace
import json
import os
from pathlib import Path
import secrets
from unittest.mock import patch

from django import test as djtest
from django.apps import apps
from django.db import DatabaseError, connection, transaction
import psycopg
from psycopg import sql

from . import business_promotion_budget_v11_preflight as preflight
from . import business_promotion_budget_v11_durable_stage as stage
from . import business_volume_files
from . import test_business_promotion_budget_v11_durable_stage as durable_fixture
from . import test_business_promotion_approved_content as approved_fixture
from . import test_business_promotion_budget_v11_attestation_role as fixture
from .database_contract import provision
from .policy import AiError


MIGRATION = import_module(
    "ai_assistant.migrations.0073_business_promotion_budget_v11_login_attestation")
v2 = MIGRATION.v2


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test",
    AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED=True)
class BudgetV11LoginAttestationRoleTests(djtest.TransactionTestCase):
    user = durable_fixture.BudgetV11DurableStageTests.user
    call = durable_fixture.BudgetV11DurableStageTests.call
    collect_body = durable_fixture.BudgetV11DurableStageTests.collect_body
    bundle = durable_fixture.BudgetV11DurableStageTests.bundle
    input_for = durable_fixture.BudgetV11DurableStageTests.input_for
    insert = durable_fixture.BudgetV11DurableStageTests.insert
    seed = durable_fixture.BudgetV11DurableStageTests.seed
    setUp = durable_fixture.BudgetV11DurableStageTests.setUp
    request_body = durable_fixture.BudgetV11DurableStageTests.request_body
    current_catalog = durable_fixture.BudgetV11DurableStageTests.current_catalog
    create_fixed_report = durable_fixture.BudgetV11DurableStageTests.create_fixed_report
    base = durable_fixture.BudgetV11DurableStageTests.base
    read = durable_fixture.BudgetV11DurableStageTests.read
    append = durable_fixture.BudgetV11DurableStageTests.append
    package = durable_fixture.BudgetV11DurableStageTests.package
    promotion = durable_fixture.BudgetV11DurableStageTests.promotion
    complete = durable_fixture.BudgetV11DurableStageTests.complete
    running_job = durable_fixture.BudgetV11DurableStageTests.running_job
    five_completed = durable_fixture.BudgetV11DurableStageTests.five_completed
    approved = durable_fixture.BudgetV11DurableStageTests.approved
    _complete_budget_report = durable_fixture.BudgetV11DurableStageTests._complete_budget_report
    _writer = durable_fixture.BudgetV11DurableStageTests._writer
    _stage = durable_fixture.BudgetV11DurableStageTests._stage
    _database = staticmethod(fixture.BudgetV11AttestationRoleTests._database)
    complete_flow = approved_fixture.PromotionApprovedContentTests.complete_flow
    databases = {"default"}
    _installed_candidate = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.ensure_connection()
        provision(connection.connection, secrets.token_hex(32), secrets.token_hex(32))
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure(%s)",
                [durable_fixture.sql.STAGE_SIGNATURE])
            installed = cursor.fetchone()[0] is not None
        if not installed:
            durable_fixture.sql.install(apps,
                SimpleNamespace(connection=connection))
            cls._installed_candidate = True

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._installed_candidate:
                durable_fixture.sql.uninstall(apps,
                    SimpleNamespace(connection=connection))
        finally:
            super().tearDownClass()

    @staticmethod
    def _login(password):
        from django.conf import settings
        database = settings.DATABASES["default"]
        return psycopg.connect(host=database["HOST"], port=database["PORT"],
            dbname=database["NAME"], user=v2.ROLE, password=password,
            autocommit=True)

    def test_0073_non_superuser_login_attests_but_never_readies_or_downloads(self):
        with self._database() as admin:
            with admin.cursor() as cursor:
                MIGRATION.verify_catalog(cursor)
            self.assertEqual(admin.execute("SELECT rolcanlogin,rolpassword IS NULL "
                "FROM pg_catalog.pg_authid WHERE rolname=%s",
                [v2.ROLE]).fetchone(), (False, True))
        report = self._complete_budget_report()
        self.complete_flow(report)
        row = self._stage(report)
        other_report = self._complete_budget_report()
        self.complete_flow(other_report)
        other_row = self._stage(other_report)
        self.assertNotEqual((row.id,row.report_id),
            (other_row.id,other_row.report_id))
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            prepared = preflight.prepare(row.id, self.admin, enabled=True)
        proof_sha = prepared["attestationSha256"]
        with self._database() as admin:
            admin.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(
                sql.Identifier(v2.ROLE)))
            try:
                with self.assertRaises(psycopg.Error):
                    admin.execute("SELECT public.ai_budget_v11_attest_staged_login_v2("
                        "%s,%s,%s)", [row.id, row.attempt,
                        prepared["attestationText"]])
            finally:
                admin.execute("RESET SESSION AUTHORIZATION")
        password = secrets.token_urlsafe(32)
        with self._database() as admin:
            admin.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(v2.ROLE), sql.Literal(password)))
        connection.close()
        try:
            with self._login(password) as role:
                self.assertEqual(role.execute("SELECT session_user,current_user,"
                    "(SELECT rolsuper FROM pg_catalog.pg_roles WHERE "
                    "rolname=session_user)").fetchone(),
                    (v2.ROLE, v2.ROLE, False))
                absent = role.execute("SELECT public."
                    "ai_budget_v11_attest_outcome_login_v2(%s,%s,%s)",
                    [row.id,row.attempt,proof_sha]).fetchone()[0]
                self.assertEqual((absent["status"],absent["retryAllowed"]),
                    ("absent_observed",False))
                receipt_id = role.execute("SELECT public."
                    "ai_budget_v11_attest_staged_login_v2(%s,%s,%s)",
                    [row.id,row.attempt,
                     prepared["attestationText"]]).fetchone()[0]
                committed = role.execute("SELECT public."
                    "ai_budget_v11_attest_outcome_login_v2(%s,%s,%s)",
                    [row.id,row.attempt,proof_sha]).fetchone()[0]
                self.assertEqual((committed["status"],committed["receiptId"],
                    committed["retryAllowed"]),("committed",receipt_id,False))
                conflict = role.execute("SELECT public."
                    "ai_budget_v11_attest_outcome_login_v2(%s,%s,%s)",
                    [row.id,row.attempt,"0"*64]).fetchone()[0]
                self.assertEqual((conflict["status"],conflict["receiptId"],
                    conflict["retryAllowed"]),("conflict",None,False))
                with self.assertRaises(psycopg.Error):
                    role.execute("SELECT public.ai_budget_v11_attest_staged_login_v2("
                        "%s,%s,%s)", [other_row.id,other_row.attempt,
                        prepared["attestationText"]])
                with self.assertRaises(psycopg.Error):
                    role.execute("SELECT public.ai_budget_v11_attest_staged_login_v2("
                        "%s,%s,%s)", [row.id,row.attempt+1,
                        prepared["attestationText"]])
                for statement in ("SELECT * FROM " + v2.TABLE,
                        "INSERT INTO " + v2.TABLE + " (id) VALUES ('forged')",
                        "UPDATE " + v2.TABLE + " SET owner_email='forged'",
                        "DELETE FROM " + v2.TABLE,
                        "TRUNCATE " + v2.TABLE,
                        "SELECT * FROM public.ai_business_promotion_budget_v11_attestations",
                        "SELECT * FROM public.protected_business_budget_v11_verifier_keys",
                        "SELECT * FROM public.protected_business_budget_v11_proof_tickets",
                        "SELECT public.ai_budget_v11_attest_staged(%s,%s,%s)",
                        "SELECT public.ai_budget_v11_private_mac_valid(%s,%s,%s)"):
                    args = ([row.id,row.attempt,prepared["attestationText"]]
                        if statement.endswith("(%s,%s,%s)") else None)
                    with self.assertRaises(psycopg.Error):
                        role.execute(statement, args)
                with self.assertRaises(psycopg.Error):
                    role.execute("SET ROLE teruisi_ai_budget_v11_sign_login")
                with self.assertRaises(psycopg.Error):
                    role.execute("CREATE TABLE public.synthetic_0073_escalation (id integer)")
                with self.assertRaises(psycopg.Error):
                    role.execute("SELECT public."
                        "ai_budget_v11_login_attestation_requirements(%s,%s,%s,%s)",
                        [row.id,row.attempt,row.binding_digest,row.manifest_json])
                with self.assertRaises(psycopg.Error):
                    role.execute("SELECT public.ai_business_volume_complete_guard()")
                for protected_file_table in ("ai_business_file_runs",
                        "ai_business_file_chunks","ai_business_volume_chunks"):
                    with self.assertRaises(psycopg.Error):
                        role.execute("SELECT * FROM public." + protected_file_table)
                    with self.assertRaises(psycopg.Error):
                        role.execute("UPDATE public." + protected_file_table +
                            " SET id='forged' WHERE false")
            with self._database() as admin:
                with admin.cursor() as cursor:
                    with self.assertRaises(RuntimeError):
                        MIGRATION.verify_catalog(cursor)
                    MIGRATION.verify_catalog(cursor, allow_test_login=True)
                self.assertEqual(admin.execute("SELECT count(*) FROM " +
                    v2.TABLE).fetchone(), (1,))
                surface = [row[0] for row in admin.execute(
                    "SELECT p.oid::regprocedure::text FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND p.prosecdef AND "
                    "has_function_privilege(%s,p.oid,'EXECUTE') ORDER BY 1",
                    [v2.ROLE]).fetchall()]
                self.assertIn(v2.ATTEST_SIGNATURE.removeprefix("public."),
                    surface)
                self.assertIn(v2.OUTCOME_SIGNATURE.removeprefix("public."),
                    surface)
                self.assertEqual(set(surface), {
                    v2.ATTEST_SIGNATURE.removeprefix("public."),
                    v2.OUTCOME_SIGNATURE.removeprefix("public."),
                    "ai_business_volume_complete_guard()"})
                surface_digest = hashlib.sha256(json.dumps(surface,
                    ensure_ascii=True,separators=(",", ":")).encode(
                    "ascii")).hexdigest()
                project_root = Path(__file__).resolve().parents[2]
                evidence_root = Path(os.environ.get(
                    "TERUISI_AI_REHEARSAL_RUN_ROOT", "")).resolve()
                if (evidence_root.parent != (project_root / ".runtime").resolve()
                        or not evidence_root.is_dir()):
                    raise AssertionError("0073 SECDEF inventory requires isolated run root")
                (evidence_root / "0073-login-secdef-surface.json").write_text(
                    json.dumps({"schemaVersion":"0073-synthetic-secdef-surface-v1",
                        "role":v2.ROLE,"count":len(surface),
                        "functions":surface,"sha256":surface_digest,
                        "productionRoleActivated":False},
                        ensure_ascii=True,sort_keys=True,indent=2),encoding="utf-8")
                print("0073_SYNTHETIC_SECDEF_SURFACE=" + json.dumps({
                    "role":v2.ROLE,"count":len(surface),
                    "digest":surface_digest,"newFunctions":sorted((
                        v2.ATTEST_SIGNATURE.removeprefix("public."),
                        v2.OUTCOME_SIGNATURE.removeprefix("public.")))},
                    ensure_ascii=True,sort_keys=True))
        finally:
            with self._database() as admin:
                admin.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                    sql.Identifier(v2.ROLE)))
                with admin.cursor() as cursor:
                    MIGRATION.verify_catalog(cursor)
        with self.assertRaises(DatabaseError), transaction.atomic():
            type(row).objects.filter(pk=row.pk).update(status="ready",
                error_code="",progress_json='{"stage":"ready"}',
                version=row.version+1)
        row.refresh_from_db()
        self.assertEqual((row.status,row.error_code),
            ("paused","renderer_unpublished"))
        with self.assertRaises(AiError):
            business_volume_files.chunk(row.id,"1","html",
                {"sequence":"1"},self.admin)

    def test_0073_catalog_rejects_password_grants_and_trigger_drift(self):
        with self._database() as admin:
            with admin.cursor() as cursor:
                MIGRATION.verify_catalog(cursor)
            admin.execute("BEGIN")
            try:
                mutations = (
                    ("preexisting_password",
                     sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD 'synthetic_only'").format(
                         sql.Identifier(v2.ROLE)),
                     lambda cursor: MIGRATION._role(cursor)),
                    ("wrong_trigger_event",
                     sql.SQL("DROP TRIGGER {} ON {}") .format(
                         sql.Identifier(MIGRATION.GUARD_TRIGGER),
                         sql.SQL(v2.TABLE)),
                     lambda cursor: MIGRATION.verify_catalog(cursor)),
                    ("signer_table_grant",
                     sql.SQL("GRANT SELECT ON {} TO teruisi_ai_budget_v11_sign_login"
                         ).format(sql.SQL(v2.TABLE)),
                     lambda cursor: MIGRATION.verify_catalog(cursor)),
                    ("signer_function_grant",
                     sql.SQL("GRANT EXECUTE ON FUNCTION {} TO "
                         "teruisi_ai_budget_v11_sign_login").format(
                         sql.SQL(v2.ATTEST_SIGNATURE)),
                     lambda cursor: MIGRATION.verify_catalog(cursor)),
                )
                for label, statement, check in mutations:
                    admin.execute("SAVEPOINT " + label)
                    admin.execute(statement)
                    with admin.cursor() as cursor:
                        with self.assertRaises(RuntimeError, msg=label):
                            check(cursor)
                    admin.execute("ROLLBACK TO SAVEPOINT " + label)
                admin.execute("SAVEPOINT owner_drift")
                admin.execute("CREATE ROLE teruisi_ai_budget_v11_owner_probe NOLOGIN")
                admin.execute("ALTER TABLE " + v2.TABLE +
                    " OWNER TO teruisi_ai_budget_v11_owner_probe")
                for signature in v2.SIGNATURES:
                    admin.execute("ALTER FUNCTION " + signature +
                        " OWNER TO teruisi_ai_budget_v11_owner_probe")
                with admin.cursor() as cursor:
                    with self.assertRaisesRegex(RuntimeError, "frozen 0067 proof owner"):
                        MIGRATION.verify_catalog(cursor)
                admin.execute("ROLLBACK TO SAVEPOINT owner_drift")
            finally:
                admin.execute("ROLLBACK")
            with admin.cursor() as cursor:
                MIGRATION.verify_catalog(cursor)
