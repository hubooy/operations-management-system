"""Disposable 0078 catalog and narrow-role probes; no production release."""
from importlib import import_module
import base64
import hashlib
import json
import os
import secrets
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest
from django.apps import apps
from django.conf import settings
from django.db import DatabaseError, connection, transaction
import psycopg
from psycopg import sql

from business_analysis import promotion_budget_ticket_signer_v11 as signer
from business_analysis import promotion_budget_v11_publication_request as publication
from . import business_promotion_budget_v11_publication_sql as candidate
from . import business_promotion_budget_v11_attest_step as old_attest
from . import business_promotion_budget_v11_durable_stage as stage
from . import business_promotion_budget_v11_preflight as preflight
from . import test_business_promotion_budget_v11_durable_stage as stage_fixture
from . import test_business_promotion_budget_v11_ticket_signer_role as sign_fixture
from . import test_business_market_v6_paused_topology_role as market_fixture
from . import business_market_v6_paused_topology_persistence as market_writer
from . import models as m
from .database_contract import provision


MIGRATION=import_module(
    "ai_assistant.migrations.0078_business_promotion_budget_v11_signed_publication")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class BudgetV11SignedPublicationRoleTests(djtest.TransactionTestCase):
    databases={"default"}

    @staticmethod
    def _coordinates():
        db=settings.DATABASES["default"]
        if (db["NAME"]!="test_teruisi_ai_rehearsal" or
                db["HOST"]!="127.0.0.1" or
                not 55440<=int(db["PORT"])<=55999 or
                str(db["PORT"])!=os.getenv("TERUISI_AI_REHEARSAL_PORT")):
            raise AssertionError("0078 role test requires disposable PostgreSQL")
        return db

    def _rollback_probe(self,operation):
        class RevertProbe(Exception): pass
        with self.assertRaises(RevertProbe):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    operation(cursor)
                    with self.assertRaisesRegex(RuntimeError,"0078 .* drift"):
                        MIGRATION.verify_catalog(cursor)
                raise RevertProbe()
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)

    def test_0078_catalog_exact_constraints_trigger_and_acl(self):
        self._coordinates()
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
            cursor.execute("SELECT count(*) FROM "+candidate.TABLE)
            self.assertEqual(cursor.fetchone(),(0,))

        self._rollback_probe(lambda cursor:cursor.execute(
            "GRANT SELECT ON "+candidate.TABLE+" TO "+candidate.READ_ROLE))
        self._rollback_probe(lambda cursor:cursor.execute(
            "GRANT EXECUTE ON FUNCTION "+candidate.CHUNK+
            " TO teruisi_ai_reader"))
        self._rollback_probe(lambda cursor:cursor.execute(
            "ALTER TABLE "+candidate.TABLE+" DISABLE TRIGGER "+
            MIGRATION.GUARD_TRIGGER))

        def loosen_attempt(cursor):
            cursor.execute("SELECT conname FROM pg_catalog.pg_constraint "
                "WHERE conrelid=%s::regclass AND contype='c' AND "
                "pg_catalog.pg_get_constraintdef(oid) LIKE %s",
                [candidate.TABLE,"%attempt >= 1%"])
            name=cursor.fetchone()[0]
            cursor.execute("ALTER TABLE "+candidate.TABLE+" DROP CONSTRAINT "+
                connection.ops.quote_name(name))
            cursor.execute("ALTER TABLE "+candidate.TABLE+" ADD CONSTRAINT "+
                connection.ops.quote_name(name)+
                " CHECK ((attempt BETWEEN 1 AND 5) OR attempt=0)")
        self._rollback_probe(loosen_attempt)

        def skip_guard(cursor):
            cursor.execute("DROP TRIGGER "+MIGRATION.GUARD_TRIGGER+
                " ON "+candidate.TABLE)
            cursor.execute("CREATE TRIGGER "+MIGRATION.GUARD_TRIGGER+
                " BEFORE INSERT OR UPDATE OR DELETE ON "+candidate.TABLE+
                " FOR EACH ROW WHEN (false) EXECUTE FUNCTION "+candidate.GUARD)
        self._rollback_probe(skip_guard)

    def test_0078_real_login_narrow_access_and_read_only_required(self):
        db=self._coordinates()
        roles=(candidate.PUBLISH_ROLE,candidate.READ_ROLE)
        passwords={role:secrets.token_urlsafe(32) for role in roles}
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
            for role in roles:
                cursor.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role),sql.Literal(passwords[role])))
        connection.close()
        try:
            with connection.cursor() as cursor:
                MIGRATION.verify_catalog(cursor,allow_test_login=True)
            def connect(role):
                return psycopg.connect(host=db["HOST"],port=db["PORT"],
                    dbname=db["NAME"],user=role,password=passwords[role],
                    autocommit=True)
            with connect(candidate.PUBLISH_ROLE) as publisher:
                self.assertEqual(publisher.execute(
                    "SELECT session_user,current_user").fetchone(),
                    (candidate.PUBLISH_ROLE,)*2)
                absent=publisher.execute("SELECT public."
                    "ai_budget_v11_publish_outcome_v2(%s,%s,%s,%s)",
                    ["missing",1,"0"*64,"0"*64]).fetchone()[0]
                self.assertEqual(absent["status"],"absent_observed")
                self.assertFalse(absent["retryAllowed"])
                with self.assertRaises(psycopg.Error) as denied:
                    publisher.execute("SELECT * FROM "+candidate.TABLE)
                self.assertEqual(denied.exception.sqlstate,"42501")
                with self.assertRaises(psycopg.Error) as denied:
                    publisher.execute("SELECT secret FROM public."
                        "protected_business_budget_v11_verifier_keys")
                self.assertEqual(denied.exception.sqlstate,"42501")
                with self.assertRaises(psycopg.Error) as denied:
                    publisher.execute("SELECT public."
                        "ai_budget_v11_read_published_chunk_v2(%s,%s,%s,%s,%s,%s)",
                        ["missing",1,1,"html",1,"0"*64])
                self.assertEqual(denied.exception.sqlstate,"42501")
            with connect(candidate.READ_ROLE) as reader:
                self.assertEqual(reader.execute(
                    "SELECT session_user,current_user").fetchone(),
                    (candidate.READ_ROLE,)*2)
                with self.assertRaises(psycopg.Error) as no_read_only:
                    reader.execute("SELECT public."
                        "ai_budget_v11_read_published_chunk_v2(%s,%s,%s,%s,%s,%s)",
                        ["missing",1,1,"html",1,"0"*64])
                self.assertIn("ai_budget_v11_download_v2_unavailable",
                    str(no_read_only.exception))
                reader.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                try:
                    with self.assertRaises(psycopg.Error) as missing:
                        reader.execute("SELECT public."
                            "ai_budget_v11_read_published_chunk_v2("
                            "%s,%s,%s,%s,%s,%s)",
                            ["missing",1,1,"html",1,"0"*64])
                    self.assertIn("ai_budget_v11_download_v2_publication_drift",
                        str(missing.exception))
                finally:
                    reader.execute("ROLLBACK")
                with self.assertRaises(psycopg.Error) as denied:
                    reader.execute("SELECT * FROM "+candidate.TABLE)
                self.assertEqual(denied.exception.sqlstate,"42501")
                with self.assertRaises(psycopg.Error) as denied:
                    reader.execute("SELECT public."
                        "ai_budget_v11_publish_outcome_v2(%s,%s,%s,%s)",
                        ["missing",1,"0"*64,"0"*64])
                self.assertEqual(denied.exception.sqlstate,"42501")
        finally:
            connection.close()
            with connection.cursor() as cursor:
                for role in roles:
                    cursor.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                        sql.Identifier(role)))
                MIGRATION.verify_catalog(cursor)


LOGIN=import_module(
    "ai_assistant.migrations.0073_business_promotion_budget_v11_login_attestation")
IDENTITY=import_module(
    "ai_assistant.migrations.0070_business_promotion_budget_v11_limited_identity")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test",
    AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED=True)
class BudgetV11SignedPublicationEndToEndTests(djtest.TransactionTestCase):
    """One real synthetic signed HTML/XLSX path; all release flags stay false."""
    fixture=sign_fixture.BudgetV11TicketSignerRoleTests
    user=fixture.user
    call=fixture.call
    collect_body=fixture.collect_body
    bundle=fixture.bundle
    input_for=fixture.input_for
    insert=fixture.insert
    seed=fixture.seed
    setUp=fixture.setUp
    request_body=fixture.request_body
    current_catalog=fixture.current_catalog
    create_fixed_report=fixture.create_fixed_report
    base=fixture.base
    read=fixture.read
    append=fixture.append
    package=fixture.package
    promotion=fixture.promotion
    complete=fixture.complete
    running_job=fixture.running_job
    five_completed=fixture.five_completed
    approved=fixture.approved
    _complete_budget_report=fixture._complete_budget_report
    _writer=fixture._writer
    _stage=fixture._stage
    complete_flow=fixture.complete_flow
    _service=staticmethod(fixture._service)
    _owning_writer=fixture._owning_writer
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
                [stage_fixture.sql.STAGE_SIGNATURE])
            installed=cursor.fetchone()[0] is not None
        if not installed:
            stage_fixture.sql.install(apps,SimpleNamespace(connection=connection))
            cls._installed_stage=True

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._installed_stage:
                stage_fixture.sql.uninstall(apps,
                    SimpleNamespace(connection=connection))
        finally:
            super().tearDownClass()

    def _read_file(self,reader,row,descriptor,request_sha):
        blocks=[]
        for sequence in range(1,descriptor["chunkCount"]+1):
            reader.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                response=reader.execute("SELECT public."
                    "ai_budget_v11_read_published_chunk_v2(%s,%s,%s,%s,%s,%s)",
                    [row.id,row.attempt,descriptor["volumeIndex"],
                     descriptor["format"],sequence,request_sha]).fetchone()[0]
                data=base64.b64decode(response["contentBase64"],validate=True)
                self.assertNotIn("\n",response["contentBase64"])
                self.assertEqual(len(data),response["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(),
                    response["sha256"])
                self.assertEqual(response["fileSha256"],descriptor["sha256"])
                self.assertFalse(response["readyAuthorized"])
                self.assertFalse(response["releaseAllowed"])
                with connection.cursor() as admin:
                    admin.execute("SELECT content FROM public."
                        "ai_business_volume_chunks WHERE run_id=%s AND "
                        "attempt=%s AND volume_index=%s AND format=%s "
                        "AND sequence=%s",[row.id,row.attempt,
                         descriptor["volumeIndex"],descriptor["format"],
                         sequence])
                    self.assertEqual(data,bytes(admin.fetchone()[0]))
                blocks.append(data)
            finally:
                reader.execute("COMMIT")
        full=b"".join(blocks)
        self.assertEqual(len(full),descriptor["bytes"])
        self.assertEqual(hashlib.sha256(full).hexdigest(),descriptor["sha256"])
        return full

    def test_signed_publication_and_read_only_full_bytes(self):
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        report=self._complete_budget_report()
        self.complete_flow(report)
        row=self._stage(report)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog",side_effect=self.current_catalog):
            prepared=preflight.prepare(row.id,self.admin,enabled=True)
        db=settings.DATABASES["default"]
        with self._service(db["USER"],db["PASSWORD"]) as admin:
            admin.execute("SET SESSION AUTHORIZATION "
                "teruisi_ai_budget_v11_attestor")
            try:
                with patch.object(stage.approved_content.runtime.transport,
                        "catalog",side_effect=self.current_catalog):
                    prior=old_attest.attest_staged(admin,row.id,self.admin,
                        enabled=True)
            finally:
                admin.execute("RESET SESSION AUTHORIZATION")
        self.assertEqual(prior["attestationSha256"],
            prepared["attestationSha256"])
        key=secrets.token_bytes(32)
        key_id="synthetic-ticket-v3"
        roles=(LOGIN.ROLE,IDENTITY.v2.ATTEST,IDENTITY.v2.SIGN,
            candidate.PUBLISH_ROLE,candidate.READ_ROLE)
        passwords={role:secrets.token_urlsafe(32) for role in roles}
        with self._service(db["USER"],db["PASSWORD"]) as admin:
            admin.execute("SET SESSION AUTHORIZATION "
                "teruisi_ai_budget_v11_key_owner")
            try:
                admin.execute("INSERT INTO public."
                    "protected_business_budget_v11_verifier_keys "
                    "(key_id,secret,status,created_at) VALUES "
                    "(%s,%s,'active',clock_timestamp())",[key_id,key])
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
                    passwords[IDENTITY.v2.SIGN]) as sign_db:
                sign_db.execute("SET statement_timeout='600s'")
                with self._owning_writer(),patch.object(
                        stage.approved_content.runtime.transport,
                        "catalog",side_effect=self.current_catalog):
                    signed=signer.sign_once(sign_db,self.admin,
                        ticket_id=ticket["ticketId"],run_id=row.id,
                        attempt=row.attempt,
                        attestation_sha256=prepared["attestationSha256"],
                        key_id=key_id,port=int(db["PORT"]),
                        key_provider=sign_fixture.SyntheticKey(key),
                        isolated_candidate=True)
            self.assertEqual(signed["status"],"signed_candidate_unpublished",
                signed.get("phase"))
            self.assertTrue(signed["ticketBoundInMac"])
            row.refresh_from_db()
            request=publication.request(run_id=row.id,attempt=row.attempt,
                expected_version=row.version,
                signed_receipt_id=signed["receiptId"],
                signed_receipt_sha256=signed["receiptSha256"],
                ticket_id=ticket["ticketId"],claim_id=signed["claimId"],
                report_id=row.report_id,owner_email=row.owner_email,
                binding_digest=row.binding_digest,
                ledger_root=signed["ledgerRoot"],
                file_page_root=signed["filePageRoot"],key_id=key_id)
            with self._service(candidate.PUBLISH_ROLE,
                    passwords[candidate.PUBLISH_ROLE]) as publisher:
                publisher.execute("SET statement_timeout='600s'")
                # Simulate a lost reply: execute once, discard its response,
                # and only observe the write via the read-only outcome.
                publisher.execute("SELECT public."
                    "ai_budget_v11_publish_signed_v2(%s,%s,%s,%s,%s,%s)",
                    [row.id,row.attempt,row.version,signed["receiptId"],
                     signed["receiptSha256"],request["requestDigest"]])
                observed=publisher.execute("SELECT public."
                    "ai_budget_v11_publish_outcome_v2(%s,%s,%s,%s)",
                    [row.id,row.attempt,request["requestDigest"],
                     signed["receiptSha256"]]).fetchone()[0]
                self.assertEqual(observed["status"],"committed")
                self.assertFalse(publication.outcome(observed,
                    request_digest=request["requestDigest"])["releaseAllowed"])
                conflict=publisher.execute("SELECT public."
                    "ai_budget_v11_publish_outcome_v2(%s,%s,%s,%s)",
                    [row.id,row.attempt,"0"*64,
                     signed["receiptSha256"]]).fetchone()[0]
                self.assertEqual(conflict["status"],"conflict")
                self.assertIsNone(conflict["publicationId"])
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM "+candidate.TABLE)
                self.assertEqual(cursor.fetchone(),(1,))
            # This is a real signed publication row, not a forged orphan.
            with self.assertRaisesRegex(RuntimeError,
                    "0078 cannot discard signed publications"):
                MIGRATION.uninstall(apps,SimpleNamespace(connection=connection))
            manifest=json.loads(row.manifest_json)
            descriptors=[manifest["manifestFile"],*manifest["files"]]
            with self._service(candidate.READ_ROLE,
                    passwords[candidate.READ_ROLE]) as reader:
                reader.execute("SET statement_timeout='600s'")
                contents={(item["volumeIndex"],item["format"]):
                    self._read_file(reader,row,item,request["requestDigest"])
                    for item in descriptors}
                self.assertTrue(contents[(0,"json")])
                self.assertTrue(contents[(1,"html")])
                self.assertTrue(contents[(1,"xlsx")])
                with self._service(db["USER"],db["PASSWORD"]) as admin:
                    admin.execute("SET SESSION AUTHORIZATION "
                        "teruisi_ai_budget_v11_key_owner")
                    try:
                        admin.execute("UPDATE public."
                            "protected_business_budget_v11_verifier_keys "
                            "SET status='revoked',revoked_at=clock_timestamp() "
                            "WHERE key_id=%s",[key_id])
                    finally:
                        admin.execute("RESET SESSION AUTHORIZATION")
                reader.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                try:
                    with self.assertRaises(psycopg.Error) as revoked:
                        reader.execute("SELECT public."
                            "ai_budget_v11_read_published_chunk_v2("
                            "%s,%s,%s,%s,%s,%s)",
                            [row.id,row.attempt,1,"html",1,
                             request["requestDigest"]])
                    self.assertIn("ai_budget_v11_download_v2_current_root_drift",
                        str(revoked.exception))
                finally:
                    reader.execute("ROLLBACK")
        finally:
            with self._service(db["USER"],db["PASSWORD"]) as admin:
                for role in roles:
                    admin.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                        sql.Identifier(role)))
                admin.execute("TRUNCATE public."
                    "protected_business_budget_v11_verifier_keys")
            with connection.cursor() as cursor:
                MIGRATION.verify_catalog(cursor)
        row.refresh_from_db()
        self.assertEqual((row.status,row.error_code),
            ("paused","renderer_unpublished"))


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class MarketV6PausedPublicationRejectTests(djtest.TransactionTestCase):
    """An actual 0077 paused five-job report cannot become a v11 file."""
    fixture=market_fixture.PausedTopologyRoleTests
    source_fixture=fixture.source_fixture
    completed_fixture=fixture.completed_fixture
    approval_fixture=fixture.approval_fixture
    user=fixture.user
    request_body=fixture.request_body
    current_catalog=fixture.current_catalog
    create_fixed_report=fixture.create_fixed_report
    planned_evidence_body=fixture.planned_evidence_body
    selector=fixture.selector
    parked_id=fixture.parked_id
    _attest_as_role=staticmethod(fixture._attest_as_role)
    admitted=fixture.admitted
    running_job=fixture.running_job
    base=fixture.base
    read=fixture.read
    append=fixture.append
    package=fixture.package
    promotion=fixture.promotion
    complete=fixture.complete
    five_completed=fixture.five_completed
    approved=fixture.approved
    complete_flow=fixture.complete_flow
    body=fixture.body
    create_plan=fixture.create_plan
    attested=fixture.attested
    prepared=fixture.prepared
    plan_and_model=fixture.plan_and_model
    input=fixture.input
    setUp=fixture.setUp
    _isolated=staticmethod(fixture._isolated)
    _login=fixture._login
    _built=fixture._built
    _active_counts=fixture._active_counts
    _service=staticmethod(sign_fixture.BudgetV11TicketSignerRoleTests._service)
    databases={"default"}

    def test_valid_paused_market_report_cannot_publish(self):
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        built=self._built("market-v6-v11-publication-reject")
        with self._login() as topology_db,djtest.override_settings(
                AI_MARKET_V6_PAUSED_TOPOLOGY_RECORD_ENABLED=True):
            committed=market_writer.create_once(topology_db,built,
                port=int(settings.DATABASES["default"]["PORT"]))
        self.assertEqual(committed["status"],"committed_paused")
        report_id=built["snapshot"]["reportId"]
        flow_id=built["snapshot"]["workflowId"]
        self.assertEqual(m.AiWorkflowRuns.objects.filter(pk=flow_id,
            status="paused").count(),1)
        self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=flow_id,
            status="paused",provider_round_count=0,
            tool_call_count=0).count(),5)
        self.assertEqual(m.AiBusinessFileRun.objects.filter(
            report_id=report_id).count(),0)
        with self.assertRaises(DatabaseError) as blocked:
            with transaction.atomic():
                m.AiBusinessFileRun.objects.create(id="v6-pub-forbidden-file",
                    report_id=report_id,
                    owner_email=built["snapshot"]["ownerEmail"],
                    scope_json="null",draft=False,renderer_version=1,
                    binding_digest="b"*64,status="queued")
        self.assertIn("ai_market_v6_topology_effect_closed",
            str(blocked.exception))
        db=self._isolated()
        password=secrets.token_urlsafe(32)
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(candidate.PUBLISH_ROLE),sql.Literal(password)))
        connection.close()
        try:
            with self._service(candidate.PUBLISH_ROLE,password) as publisher:
                with self.assertRaises(psycopg.Error) as denied:
                    publisher.execute("SELECT public."
                        "ai_budget_v11_publish_signed_v2(%s,%s,%s,%s,%s,%s)",
                        [report_id,1,1,"12345678-1234-1234-1234-123456789abc",
                         "0"*64,"0"*64])
                self.assertIn("ai_budget_v11_publish_v2_signed_root_drift",
                    str(denied.exception))
        finally:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                    sql.Identifier(candidate.PUBLISH_ROLE)))
                MIGRATION.verify_catalog(cursor)
        self.assertEqual(m.AiBusinessFileRun.objects.filter(
            report_id=report_id).count(),0)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM "+candidate.TABLE)
            self.assertEqual(cursor.fetchone(),(0,))
