"""Disposable 0076 SQL role test; never a production signer or release."""
from contextlib import contextmanager
from importlib import import_module
import hashlib
import hmac
import os
import secrets
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest
from django.apps import apps
from django.conf import settings
from django.db import connection, transaction
import psycopg
from psycopg import sql

from business_analysis import promotion_budget_ticket_signer_v11 as signer
from business_analysis.contracts import canonical
from . import business_promotion_budget_v11_attest_step as old_attest
from . import business_promotion_budget_v11_durable_stage as stage
from . import business_promotion_budget_v11_preflight as preflight
from . import business_volume_files
from . import test_business_promotion_budget_v11_durable_stage as fixture
from . import test_business_promotion_approved_content as approved_fixture
from .control_models import AiWriteAuthority
from .database_contract import provision
from .policy import AiError


MIGRATION=import_module(
    "ai_assistant.migrations.0076_business_promotion_budget_v11_ticket_bound_signer")
LOGIN=import_module(
    "ai_assistant.migrations.0073_business_promotion_budget_v11_login_attestation")
IDENTITY=import_module(
    "ai_assistant.migrations.0070_business_promotion_budget_v11_limited_identity")


class SyntheticKey:
    def __init__(self,value): self.value=value
    def get_key(self,key_id):
        if key_id!="synthetic-ticket-v3": raise AssertionError("wrong synthetic key")
        return self.value


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test",
    AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED=True)
class BudgetV11TicketSignerRoleTests(djtest.TransactionTestCase):
    user=fixture.BudgetV11DurableStageTests.user
    call=fixture.BudgetV11DurableStageTests.call
    collect_body=fixture.BudgetV11DurableStageTests.collect_body
    bundle=fixture.BudgetV11DurableStageTests.bundle
    input_for=fixture.BudgetV11DurableStageTests.input_for
    insert=fixture.BudgetV11DurableStageTests.insert
    seed=fixture.BudgetV11DurableStageTests.seed
    setUp=fixture.BudgetV11DurableStageTests.setUp
    request_body=fixture.BudgetV11DurableStageTests.request_body
    current_catalog=fixture.BudgetV11DurableStageTests.current_catalog
    create_fixed_report=fixture.BudgetV11DurableStageTests.create_fixed_report
    base=fixture.BudgetV11DurableStageTests.base
    read=fixture.BudgetV11DurableStageTests.read
    append=fixture.BudgetV11DurableStageTests.append
    package=fixture.BudgetV11DurableStageTests.package
    promotion=fixture.BudgetV11DurableStageTests.promotion
    complete=fixture.BudgetV11DurableStageTests.complete
    running_job=fixture.BudgetV11DurableStageTests.running_job
    five_completed=fixture.BudgetV11DurableStageTests.five_completed
    approved=fixture.BudgetV11DurableStageTests.approved
    _complete_budget_report=fixture.BudgetV11DurableStageTests._complete_budget_report
    _writer=fixture.BudgetV11DurableStageTests._writer
    _stage=fixture.BudgetV11DurableStageTests._stage
    complete_flow=approved_fixture.PromotionApprovedContentTests.complete_flow
    databases={"default"}
    _installed_stage=False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.ensure_connection()
        cls.reader_password=secrets.token_hex(32)
        cls.writer_password=secrets.token_hex(32)
        provision(connection.connection,cls.reader_password,cls.writer_password)
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure(%s)",
                [fixture.sql.STAGE_SIGNATURE])
            installed=cursor.fetchone()[0] is not None
        if not installed:
            fixture.sql.install(apps,SimpleNamespace(connection=connection))
            cls._installed_stage=True

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._installed_stage:
                fixture.sql.uninstall(apps,SimpleNamespace(connection=connection))
        finally:
            super().tearDownClass()

    @staticmethod
    def _service(role,password):
        db=settings.DATABASES["default"]
        if (settings.DJANGO_ENVIRONMENT!="test" or
                db["NAME"]!="test_teruisi_ai_rehearsal" or
                db["HOST"]!="127.0.0.1" or
                not 55440<=int(db["PORT"])<=55999 or
                str(db["PORT"])!=os.getenv("TERUISI_AI_REHEARSAL_PORT")):
            raise AssertionError("0076 role test requires disposable PostgreSQL")
        return psycopg.connect(host=db["HOST"],port=db["PORT"],
            dbname=db["NAME"],user=role,password=password,autocommit=True)

    @contextmanager
    def _owning_writer(self):
        authority=AiWriteAuthority.objects.get(id=1)
        db=connection.settings_dict
        before=(db["USER"],db["PASSWORD"])
        connection.close()
        try:
            db["USER"]="teruisi_ai_writer"
            db["PASSWORD"]=self.writer_password
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout='600s'")
            with self.settings(DJANGO_PROCESS_ROLE="ai_writer",
                    DJANGO_EXPECT_READ_ONLY=False,
                    AI_WRITE_AUTHORITY_EPOCH=str(authority.authority_epoch),
                    AI_WRITE_CUTOVER_ID=authority.cutover_id):
                yield
        finally:
            connection.close()
            db["USER"],db["PASSWORD"]=before
            connection.ensure_connection()

    def test_v3_non_superuser_ticket_ledger_mac_and_unknown_outcome(self):
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        report=self._complete_budget_report()
        self.complete_flow(report)
        row=self._stage(report)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog",side_effect=self.current_catalog):
            prepared=preflight.prepare(row.id,self.admin,enabled=True)
        with self._service(settings.DATABASES["default"]["USER"],
                settings.DATABASES["default"]["PASSWORD"]) as admin:
            admin.execute("SET SESSION AUTHORIZATION teruisi_ai_budget_v11_attestor")
            try:
                with patch.object(stage.approved_content.runtime.transport,
                        "catalog",side_effect=self.current_catalog):
                    prior=old_attest.attest_staged(admin,row.id,self.admin,enabled=True)
            finally:
                admin.execute("RESET SESSION AUTHORIZATION")
        self.assertEqual(prior["attestationSha256"],prepared["attestationSha256"])
        key=secrets.token_bytes(32)
        roles=(LOGIN.ROLE,IDENTITY.v2.ATTEST,IDENTITY.v2.SIGN)
        passwords={name:secrets.token_urlsafe(32) for name in roles}
        with self._service(settings.DATABASES["default"]["USER"],
                settings.DATABASES["default"]["PASSWORD"]) as admin:
            admin.execute("SET SESSION AUTHORIZATION " +
                "teruisi_ai_budget_v11_key_owner")
            try:
                admin.execute("INSERT INTO public."
                    "protected_business_budget_v11_verifier_keys "
                    "(key_id,secret,status,created_at) VALUES "
                    "('synthetic-ticket-v3',%s,'active',clock_timestamp())",[key])
            finally:
                admin.execute("RESET SESSION AUTHORIZATION")
            for role,password in passwords.items():
                admin.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role),sql.Literal(password)))
        connection.close()
        try:
            with self._service(LOGIN.ROLE,passwords[LOGIN.ROLE]) as attestor:
                new_id=attestor.execute("SELECT public."
                    "ai_budget_v11_attest_staged_login_v2(%s,%s,%s)",
                    [row.id,row.attempt,prepared["attestationText"]]).fetchone()[0]
                self.assertEqual(len(new_id),64)
                with self.assertRaises(psycopg.Error):
                    attestor.execute("SELECT * FROM public."
                        "protected_business_budget_v11_signed_receipts_v3")
            with self._service(IDENTITY.v2.ATTEST,
                    passwords[IDENTITY.v2.ATTEST]) as issuer:
                ticket=issuer.execute("SELECT public."
                    "ai_budget_v11_issue_proof_ticket_v2(%s,%s,%s)",
                    [row.id,row.attempt,prepared["attestationSha256"]]
                    ).fetchone()[0]
            with self._service(IDENTITY.v2.SIGN,
                    passwords[IDENTITY.v2.SIGN]) as sign_db:
                sign_db.execute("SET statement_timeout='600s'")
                with self._owning_writer(), patch.object(
                        stage.approved_content.runtime.transport,
                        "catalog",side_effect=self.current_catalog):
                    signed=signer.sign_once(sign_db,self.admin,
                        ticket_id=ticket["ticketId"],run_id=row.id,
                        attempt=row.attempt,
                        attestation_sha256=prepared["attestationSha256"],
                        key_id="synthetic-ticket-v3",
                        port=int(settings.DATABASES["default"]["PORT"]),
                        key_provider=SyntheticKey(key),isolated_candidate=True)
                self.assertEqual(signed["status"],"signed_candidate_unpublished",
                    signed.get("phase","no_phase"))
                self.assertTrue(signed["ticketBoundInMac"])
                self.assertFalse(signed["releaseAllowed"])
                self.assertEqual(signer.outcome(sign_db,
                    ticket_id=ticket["ticketId"],claim_id=signed["claimId"],
                    receipt_sha256=signed["receiptSha256"],
                    port=int(settings.DATABASES["default"]["PORT"]))["status"],
                    "committed")
                self.assertEqual(signer.outcome(sign_db,
                    ticket_id=ticket["ticketId"],claim_id=signed["claimId"],
                    receipt_sha256="0"*64,
                    port=int(settings.DATABASES["default"]["PORT"]))["status"],
                    "conflict")
                with self.assertRaises(psycopg.Error) as cross_run:
                    sign_db.execute("SELECT public."
                        "ai_budget_v11_sign_ledger_inventory_v3(%s,%s,%s,%s,%s)",
                        [ticket["ticketId"],signed["claimId"],"other_run",
                         row.attempt,prepared["attestationSha256"]])
                self.assertIn("ai_budget_v11_sign_v3_ticket_drift",
                    str(cross_run.exception))
                with self.assertRaises(psycopg.Error) as cross_job:
                    sign_db.execute("SELECT public."
                        "ai_budget_v11_read_agent_ledger_v3("
                        "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [ticket["ticketId"],signed["claimId"],row.id,
                         row.attempt,prepared["attestationSha256"],
                         "other_job","provider",1,signed["ledgerRoot"]])
                self.assertIn("ai_budget_v11_sign_v3_job_drift",
                    str(cross_job.exception))
                with self.assertRaises(psycopg.Error) as private_ledger:
                    sign_db.execute("SELECT * FROM public."
                        "ai_agent_provider_dispatches LIMIT 1")
                self.assertEqual(private_ledger.exception.sqlstate,"42501")
                with self.assertRaises(psycopg.Error) as private_receipt:
                    sign_db.execute("SELECT * FROM public."
                        "protected_business_budget_v11_signed_receipts_v3")
                self.assertEqual(private_receipt.exception.sqlstate,"42501")
                with self.assertRaises(psycopg.Error) as private_key:
                    sign_db.execute("SELECT secret FROM public."
                        "protected_business_budget_v11_verifier_keys")
                self.assertEqual(private_key.exception.sqlstate,"42501")
        finally:
            with self._service(settings.DATABASES["default"]["USER"],
                    settings.DATABASES["default"]["PASSWORD"]) as admin:
                for role in roles:
                    admin.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                        sql.Identifier(role)))
                admin.execute("TRUNCATE public."
                    "protected_business_budget_v11_verifier_keys")
        row.refresh_from_db()
        self.assertEqual((row.status,row.error_code),
            ("paused","renderer_unpublished"))
        with self.assertRaises(AiError):
            business_volume_files.chunk(row.id,1,"html",{"sequence":"1"},self.admin)

    def test_v3_catalog_rejects_when_false_check_replacement_and_third_role(self):
        class RollbackProbe(Exception): pass
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        for mutation in ("when_false","check_replacement","check_or_true",
                "third_role"):
            with self.subTest(mutation=mutation):
                try:
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            if mutation=="when_false":
                                cursor.execute("DROP TRIGGER " +
                                    MIGRATION.GUARD_TRIGGER + " ON " +
                                    MIGRATION.v3.TABLE)
                                cursor.execute("CREATE TRIGGER " +
                                    MIGRATION.GUARD_TRIGGER + " BEFORE INSERT OR "
                                    "UPDATE OR DELETE ON " + MIGRATION.v3.TABLE +
                                    " FOR EACH ROW WHEN (false) EXECUTE FUNCTION " +
                                    MIGRATION.v3.GUARD)
                            elif mutation in ("check_replacement","check_or_true"):
                                column=("attempt" if mutation=="check_replacement"
                                    else "binding_digest")
                                cursor.execute("SELECT c.conname FROM "
                                    "pg_catalog.pg_constraint c WHERE c.conrelid="
                                    "%s::regclass AND c.contype='c' AND "
                                    "pg_catalog.pg_get_constraintdef(c.oid) "
                                    "LIKE %s",[MIGRATION.v3.TABLE,
                                        "%"+column+"%"])
                                name=cursor.fetchone()[0]
                                cursor.execute(sql.SQL("ALTER TABLE {} DROP "
                                    "CONSTRAINT {}").format(
                                    sql.SQL(MIGRATION.v3.TABLE),
                                    sql.Identifier(name)))
                                replacement=("attempt BETWEEN 0 AND 6"
                                    if mutation=="check_replacement" else
                                    "(binding_digest ~ '^[0-9a-f]{64}$') OR true")
                                cursor.execute(sql.SQL("ALTER TABLE {} ADD "
                                    "CONSTRAINT {} CHECK (").format(
                                    sql.SQL(MIGRATION.v3.TABLE),
                                    sql.Identifier(name)) +
                                    sql.SQL(replacement+")"))
                            else:
                                cursor.execute("CREATE ROLE "
                                    "teruisi_ai_v11_acl_probe NOLOGIN")
                                cursor.execute("GRANT EXECUTE ON FUNCTION " +
                                    MIGRATION.v3.READ + " TO " +
                                    "teruisi_ai_v11_acl_probe")
                            with self.assertRaises(RuntimeError):
                                MIGRATION.verify_catalog(cursor)
                        raise RollbackProbe()
                except RollbackProbe:
                    pass
                with connection.cursor() as cursor:
                    MIGRATION.verify_catalog(cursor)

    def test_v3_record_rechecks_current_report_and_private_ledger_after_preflight(self):
        """Corrupt a synthetic current root after full-byte preflight, never sign it."""
        report=self._complete_budget_report()
        self.complete_flow(report)
        row=self._stage(report)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog",side_effect=self.current_catalog):
            prepared=preflight.prepare(row.id,self.admin,enabled=True)
        with self._service(settings.DATABASES["default"]["USER"],
                settings.DATABASES["default"]["PASSWORD"]) as admin:
            admin.execute("SET SESSION AUTHORIZATION teruisi_ai_budget_v11_attestor")
            try:
                with patch.object(stage.approved_content.runtime.transport,
                        "catalog",side_effect=self.current_catalog):
                    old_attest.attest_staged(admin,row.id,self.admin,enabled=True)
            finally:
                admin.execute("RESET SESSION AUTHORIZATION")
        key=secrets.token_bytes(32)
        roles=(LOGIN.ROLE,IDENTITY.v2.ATTEST,IDENTITY.v2.SIGN)
        passwords={name:secrets.token_urlsafe(32) for name in roles}
        with self._service(settings.DATABASES["default"]["USER"],
                settings.DATABASES["default"]["PASSWORD"]) as admin:
            admin.execute("SET SESSION AUTHORIZATION " +
                "teruisi_ai_budget_v11_key_owner")
            try:
                admin.execute("INSERT INTO public."
                    "protected_business_budget_v11_verifier_keys "
                    "(key_id,secret,status,created_at) VALUES "
                    "('synthetic-ticket-v3',%s,'active',clock_timestamp())",[key])
            finally:
                admin.execute("RESET SESSION AUTHORIZATION")
            for role,password in passwords.items():
                admin.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role),sql.Literal(password)))
        connection.close()
        try:
            with self._service(LOGIN.ROLE,passwords[LOGIN.ROLE]) as attestor:
                attestor.execute("SELECT public."
                    "ai_budget_v11_attest_staged_login_v2(%s,%s,%s)",
                    [row.id,row.attempt,prepared["attestationText"]])
            with self._service(IDENTITY.v2.ATTEST,
                    passwords[IDENTITY.v2.ATTEST]) as issuer:
                ticket=issuer.execute("SELECT public."
                    "ai_budget_v11_issue_proof_ticket_v2(%s,%s,%s)",
                    [row.id,row.attempt,prepared["attestationSha256"]]
                    ).fetchone()[0]
            with self._service(IDENTITY.v2.SIGN,
                    passwords[IDENTITY.v2.SIGN]) as sign_db, self._service(
                    settings.DATABASES["default"]["USER"],
                    settings.DATABASES["default"]["PASSWORD"]) as admin:
                sign_db.execute("SET statement_timeout='600s'")
                narrow=sign_db.execute("SELECT public."
                    "ai_budget_v11_read_proof_ticket_v2(%s,%s,%s,%s)",
                    [ticket["ticketId"],row.id,row.attempt,
                     prepared["attestationSha256"]]).fetchone()[0]
                claim=narrow["claimId"]
                args=[ticket["ticketId"],claim,row.id,row.attempt,
                    prepared["attestationSha256"]]
                req=sign_db.execute("SELECT public."
                    "ai_budget_v11_sign_requirements_v3(%s,%s,%s,%s,%s)",
                    args).fetchone()[0]
                inv=sign_db.execute("SELECT public."
                    "ai_budget_v11_sign_ledger_inventory_v3(%s,%s,%s,%s,%s)",
                    args).fetchone()[0]
                body={"schemaVersion":signer.SCHEMA,"purpose":signer.PURPOSE,
                    "keyId":"synthetic-ticket-v3","ticketId":ticket["ticketId"],
                    "claimId":claim,"runId":row.id,"attempt":row.attempt,
                    "runVersion":req["runVersion"],
                    "workflowVersion":req["workflowVersion"],
                    "reportId":req["reportId"],
                    "ownerEmail":req["ownerEmail"],
                    "bindingDigest":req["bindingDigest"],
                    "oldAttestationId":req["oldAttestationId"],
                    "loginAttestationId":req["loginAttestationId"],
                    "attestationSha256":prepared["attestationSha256"],
                    "ledgerRoot":inv["ledgerRoot"],
                    "filePageRoot":req["filePageRoot"]}
                raw=canonical(body)
                mac=hmac.new(key,signer.DOMAIN+raw.encode(),
                    hashlib.sha256).hexdigest()
                record="SELECT public.ai_budget_v11_record_signed_receipt_v3("
                record+="%s,%s,%s,%s,%s,%s,%s)"
                payload=[*args,raw,mac]

                class RollbackProbe(Exception): pass

                def probe(table,trigger,statement,params,expected,
                          readback,readback_params,original):
                    # Existing immutable triggers normally reject these
                    # mutations. This exact disposable DB probe temporarily
                    # disables ONE named trigger inside a rollback-only
                    # transaction so the new 0076 RECORD defense is exercised.
                    state=admin.execute("SELECT tgenabled FROM "
                        "pg_catalog.pg_trigger WHERE tgrelid=%s::regclass "
                        "AND tgname=%s",["public."+table,trigger]).fetchone()
                    self.assertEqual(state,("O",))
                    try:
                        with admin.transaction():
                            admin.execute("ALTER TABLE public."+table+
                                " DISABLE TRIGGER "+trigger)
                            admin.execute(statement,params)
                            admin.execute("SAVEPOINT v11_record_probe")
                            admin.execute("SET SESSION AUTHORIZATION " +
                                IDENTITY.v2.SIGN)
                            try:
                                admin.execute(record,payload)
                            except psycopg.Error as error:
                                admin.execute("ROLLBACK TO SAVEPOINT "
                                    "v11_record_probe")
                                self.assertIn(expected,str(error))
                            else:
                                raise AssertionError("0076 admitted changed root")
                            self.assertEqual(admin.execute("SELECT "
                                "session_user,current_user").fetchone(),
                                (settings.DATABASES["default"]["USER"],)*2)
                            self.assertEqual(admin.execute("SELECT count(*) FROM " +
                                MIGRATION.v3.TABLE).fetchone(),(0,))
                            raise RollbackProbe()
                    except RollbackProbe:
                        pass
                    self.assertEqual(admin.execute("SELECT tgenabled FROM "
                        "pg_catalog.pg_trigger WHERE tgrelid=%s::regclass "
                        "AND tgname=%s",["public."+table,trigger]
                        ).fetchone(),("O",))
                    self.assertEqual(admin.execute(readback,readback_params
                        ).fetchone(),(original,))

                probe("ai_report_runs","ai_immutable_evidence",
                    "UPDATE public.ai_report_runs SET owner_email=%s WHERE id=%s",
                    ["other@example.test",report.id],
                    "ai_budget_v11_sign_v3_proof_drift",
                    "SELECT owner_email FROM public.ai_report_runs WHERE id=%s",
                    [report.id],report.owner_email)

                probe("ai_report_runs","ai_immutable_evidence",
                    "UPDATE public.ai_report_runs SET snapshot_json=%s WHERE id=%s",
                    [report.snapshot_json+" ",report.id],
                    "ai_budget_v11_sign_v3_current_root_drift",
                    "SELECT snapshot_json FROM public.ai_report_runs WHERE id=%s",
                    [report.id],report.snapshot_json)

                provider=admin.execute("SELECT r.dispatch_id,r.response_digest "
                    "FROM public.ai_agent_provider_results r JOIN public."
                    "ai_agent_provider_dispatches p ON p.id=r.dispatch_id "
                    "JOIN public.ai_agent_jobs j ON j.id=p.job_id "
                    "WHERE j.workflow_run_id=%s ORDER BY p.id LIMIT 1",
                    [report.workflow_id]).fetchone()
                self.assertIsNotNone(provider)
                probe("ai_agent_provider_results","ai_immutable_evidence",
                    "UPDATE public.ai_agent_provider_results SET "
                    "response_digest=%s WHERE dispatch_id=%s",
                    ["0"*64,provider[0]],
                    "ai_budget_v11_sign_v3_provider_result",
                    "SELECT response_digest FROM public."
                    "ai_agent_provider_results WHERE dispatch_id=%s",
                    [provider[0]],provider[1])

                tool=admin.execute("SELECT t.id,t.provider_dispatch_id,t.job_id "
                    "FROM public.ai_agent_tool_dispatches t JOIN public."
                    "ai_agent_jobs j ON j.id=t.job_id WHERE "
                    "j.workflow_run_id=%s ORDER BY t.id LIMIT 1",
                    [report.workflow_id]).fetchone()
                other=admin.execute("SELECT p.id FROM public."
                    "ai_agent_provider_dispatches p JOIN public.ai_agent_jobs j "
                    "ON j.id=p.job_id WHERE j.workflow_run_id=%s AND "
                    "p.job_id<>%s ORDER BY p.id LIMIT 1",
                    [report.workflow_id,tool[2]]).fetchone()
                self.assertIsNotNone(other)
                probe("ai_agent_tool_dispatches","ai_immutable_identity",
                    "UPDATE public.ai_agent_tool_dispatches SET "
                    "provider_dispatch_id=%s WHERE id=%s",
                    [other[0],tool[0]],
                    "ai_budget_v11_sign_v3_tool_provider_drift",
                    "SELECT provider_dispatch_id FROM public."
                    "ai_agent_tool_dispatches WHERE id=%s",
                    [tool[0]],tool[1])

                self.assertIsNotNone(sign_db.execute(record,payload).fetchone()[0])
                self.assertEqual(admin.execute("SELECT count(*) FROM " +
                    MIGRATION.v3.TABLE).fetchone(),(1,))
        finally:
            with self._service(settings.DATABASES["default"]["USER"],
                    settings.DATABASES["default"]["PASSWORD"]) as admin:
                for role in roles:
                    admin.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                        sql.Identifier(role)))
                admin.execute("TRUNCATE public."
                    "protected_business_budget_v11_verifier_keys")
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
