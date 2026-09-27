"""Disposable non-superuser v11 identity proof; no production role activation."""
import hashlib
import secrets
from unittest.mock import patch

from django.conf import settings
from django.db import connection
import psycopg
from psycopg import sql

from business_analysis import promotion_budget_verifier_receipt_v11 as receipt
from . import business_promotion_budget_v11_attest_step as attest_step
from . import business_promotion_budget_v11_durable_stage as stage
from . import business_promotion_budget_v11_identity_candidate_sql as candidate
from . import test_business_promotion_approved_content as approved_fixture
from . import test_business_promotion_budget_v11_attestation_role as fixture


class BudgetV11IdentityCandidateRoleTests(fixture.BudgetV11AttestationRoleTests):
    complete_flow = approved_fixture.PromotionApprovedContentTests.complete_flow

    @staticmethod
    def _old_catalog():
        from importlib import import_module
        att = import_module(
            "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")
        verify = import_module(
            "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
        signatures = (att.SIGNATURE,att.REQUIREMENTS_SIGNATURE,
            "public.ai_budget_v11_attestation_guard()",
            verify.MAC_SIGNATURE,verify.VERIFY_SIGNATURE,
            "public.ai_budget_v11_key_guard()",
            "public.ai_business_files_guard()",
            "public.ai_business_volume_complete_guard()")
        with connection.cursor() as cursor:
            att.verify_catalog(cursor)
            verify.verify_catalog(cursor)
            result=[]
            for signature in signatures:
                cursor.execute("SELECT oid,prosrc,proacl::text,proowner,prosecdef "
                    "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
                    [signature])
                result.append((signature,cursor.fetchone()))
        return result

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._frozen = cls._old_catalog()
        from importlib import import_module
        with connection.cursor() as cursor:
            import_module(
                "ai_assistant.migrations.0070_business_promotion_budget_v11_limited_identity"
            ).verify_catalog(cursor)
        if cls._old_catalog() != cls._frozen:
            raise AssertionError("0070 changed 0067/0068 or v11 ready guards")

    @staticmethod
    def _service(role, password):
        value = settings.DATABASES["default"]
        return psycopg.connect(host=value["HOST"],port=value["PORT"],
            dbname=value["NAME"],user=role,password=password,autocommit=True)

    def test_non_superuser_exact_ticket_read_and_receipt_verify_only(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolname,rolcanlogin,rolsuper,rolinherit "
                "FROM pg_catalog.pg_roles WHERE rolname=ANY(%s) ORDER BY rolname",
                [list(candidate.ROLES)])
            self.assertEqual({(name,can,superuser,inherit) for
                name,can,superuser,inherit in cursor.fetchall()},
                {(role,False,False,False) for role in candidate.ROLES})
            cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members "
                "WHERE roleid=ANY(ARRAY(SELECT oid FROM pg_catalog.pg_roles "
                "WHERE rolname=ANY(%s))) OR member=ANY(ARRAY(SELECT oid "
                "FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)))",
                [list(candidate.ROLES),list(candidate.ROLES)])
            self.assertEqual(cursor.fetchone(), (0,))
            for role in (*candidate.ROLES,"teruisi_ai_reader","teruisi_ai_writer"):
                for table in (candidate.TABLE,candidate.CLAIMS):
                    cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT'),"
                        "has_table_privilege(%s,%s,'INSERT')",
                        [role,table,role,table])
                    self.assertEqual(cursor.fetchone(),(False,False))
                cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
                    "has_function_privilege(%s,%s,'EXECUTE'),"
                    "has_function_privilege(%s,%s,'EXECUTE')",
                    [role,candidate.ISSUE,role,candidate.READ,role,candidate.VERIFY])
                self.assertEqual(cursor.fetchone(),(role==candidate.ATTEST,
                    role==candidate.SIGN,role==candidate.PUBLISH))
            for role in candidate.ROLES:
                cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT'),"
                    "has_function_privilege(%s,%s,'EXECUTE'),"
                    "has_function_privilege(%s,%s,'EXECUTE'),"
                    "has_function_privilege(%s,%s,'EXECUTE')",
                    [role,"public.protected_business_budget_v11_verifier_keys",
                     role,"public.ai_budget_v11_attest_staged(text,integer,text)",
                     role,"public.ai_budget_v11_private_mac_valid(text,text,text)",
                     role,"public.ai_budget_v11_verify_protected_receipt("+
                        "text,integer,text,text)"])
                self.assertEqual(cursor.fetchone(),(False,False,False,False))
        report = self._complete_budget_report()
        self.complete_flow(report)
        row = self._stage(report)
        with self._database() as admin:
            admin.execute("SET SESSION AUTHORIZATION teruisi_ai_budget_v11_attestor")
            try:
                with patch.object(stage.approved_content.runtime.transport,
                        "catalog", side_effect=self.current_catalog):
                    attested = attest_step.attest_staged(admin,row.id,self.admin,
                        enabled=True)
                self.assertEqual(attested["status"],"staged_attested_unpublished")
            finally:
                admin.execute("RESET SESSION AUTHORIZATION")
        synthetic_key = secrets.token_bytes(32)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            signed = receipt.sign_after_preflight(row.id,self.admin,enabled=True,
                key_id="synthetic-limited-v2",secret=synthetic_key)
        passwords = {role: secrets.token_urlsafe(32) for role in candidate.ROLES}
        with self._database() as admin:
            admin.execute("SET SESSION AUTHORIZATION " +
                "teruisi_ai_budget_v11_key_owner")
            try:
                admin.execute("INSERT INTO " +
                    "public.protected_business_budget_v11_verifier_keys "
                    "(key_id,secret,status,created_at) VALUES (%s,%s,'active',"
                    "clock_timestamp())",["synthetic-limited-v2",synthetic_key])
            finally:
                admin.execute("RESET SESSION AUTHORIZATION")
            for role,password in passwords.items():
                admin.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role),sql.Literal(password)))
        connection.close()  # business probes use only direct non-superuser logins
        try:
            with self._service(candidate.ATTEST,passwords[candidate.ATTEST]) as db:
                self.assertEqual(db.execute("SELECT session_user,current_user,"
                    "(SELECT rolsuper FROM pg_catalog.pg_roles WHERE "
                    "rolname=session_user)").fetchone(),
                    (candidate.ATTEST,candidate.ATTEST,False))
                issued = db.execute("SELECT public.ai_budget_v11_issue_proof_ticket_v2("
                    "%s,%s,%s)",[row.id,row.attempt,
                    attested["attestationSha256"]]).fetchone()[0]
                self.assertEqual(issued["runId"],row.id)
                self.assertFalse(issued["readyAuthorized"])
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT public.ai_budget_v11_issue_proof_ticket_v2("
                        "%s,%s,%s)",[row.id,row.attempt,
                        attested["attestationSha256"]])
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT * FROM " + candidate.TABLE)
                with self.assertRaises(psycopg.Error):
                    db.execute("SET ROLE " + candidate.SIGN)
            with self._service(candidate.SIGN,passwords[candidate.SIGN]) as db:
                self.assertEqual(db.execute("SELECT session_user,current_user").fetchone(),
                    (candidate.SIGN,candidate.SIGN))
                args=[issued["ticketId"],row.id,row.attempt,
                    attested["attestationSha256"]]
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT public.ai_budget_v11_read_proof_ticket_v2("
                        "%s,%s,%s,%s)",[issued["ticketId"],row.id,2,
                        attested["attestationSha256"]])
                narrow = db.execute("SELECT public.ai_budget_v11_read_proof_ticket_v2("
                    "%s,%s,%s,%s)",args).fetchone()[0]
                self.assertEqual((narrow["runId"],narrow["attempt"],
                    narrow["attestationSha256"]),(row.id,row.attempt,
                    attested["attestationSha256"]))
                self.assertEqual(hashlib.sha256(narrow["attestationText"].encode(
                    "utf-8")).hexdigest(),attested["attestationSha256"])
                self.assertRegex(narrow["claimId"],
                    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT public.ai_budget_v11_read_proof_ticket_v2("
                        "%s,%s,%s,%s)",args)
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT * FROM " +
                        "public.ai_business_promotion_budget_v11_attestations")
            with self._service(candidate.PUBLISH,passwords[candidate.PUBLISH]) as db:
                self.assertEqual(db.execute("SELECT session_user,current_user").fetchone(),
                    (candidate.PUBLISH,candidate.PUBLISH))
                self.assertEqual(db.execute("SELECT public."
                    "ai_budget_v11_verify_protected_receipt_v2(%s,%s,%s,%s)",
                    [row.id,row.attempt,signed["receiptText"],
                    signed["receiptMac"]]).fetchone(),(True,))
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT public."
                        "ai_budget_v11_verify_protected_receipt_v2(%s,%s,%s,%s)",
                        [row.id,row.attempt,signed["receiptText"],"0"*64])
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT secret FROM " +
                        "public.protected_business_budget_v11_verifier_keys")
            with self._database() as admin:
                self.assertEqual(admin.execute("SELECT count(*) FROM " +
                    candidate.TABLE).fetchone(),(1,))
                self.assertEqual(admin.execute("SELECT count(*) FROM " +
                    candidate.CLAIMS).fetchone(),(1,))
                with self.assertRaises(psycopg.Error):
                    admin.execute("UPDATE " + candidate.TABLE +
                        " SET expires_at=clock_timestamp()")
                with self.assertRaises(psycopg.Error):
                    admin.execute("DELETE FROM " + candidate.CLAIMS)
        finally:
            with self._database() as admin:
                for role in candidate.ROLES:
                    admin.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                        sql.Identifier(role)))
                admin.execute("TRUNCATE " +
                    "public.protected_business_budget_v11_verifier_keys")
        row.refresh_from_db()
        self.assertEqual((row.status,row.error_code),
            ("paused","renderer_unpublished"))
