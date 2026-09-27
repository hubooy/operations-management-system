"""Pure source boundary checks for the isolated 0078 sidecar."""
import hashlib
import importlib
from pathlib import Path
import sys
import unittest


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from ai_assistant import business_promotion_budget_v11_publication_sql as sql


class PublicationStaticTests(unittest.TestCase):
    def test_login_activation_requires_explicit_isolated_catalog_context(self):
        migration=importlib.import_module(
            "ai_assistant.migrations.0078_business_promotion_budget_v11_signed_publication")
        class Cursor:
            def __init__(self,flags,coordinate=("test_teruisi_ai_rehearsal",
                    55840,"127.0.0.1")):
                self.flags=flags
                self.coordinate=coordinate
                self.last=""
            def execute(self,query,params=None): self.last=query
            def fetchone(self):
                if "to_regrole" in self.last: return ("synthetic_role",)
                if "FROM pg_catalog.pg_authid" in self.last:
                    return self.flags
                if "current_database(),inet_server_port()" in self.last:
                    return self.coordinate
                if "count(*) FROM pg_catalog.pg_auth_members" in self.last:
                    return (0,)
                raise AssertionError("unexpected role query")
        role=sql.READ_ROLE
        migration._role(Cursor((False,)*7+(True,)),role)
        active=(True,)+(False,)*7
        with self.assertRaises(RuntimeError):
            migration._role(Cursor(active),role)
        migration._role(Cursor(active),role,allow_test_login=True)
        with self.assertRaises(RuntimeError):
            migration._role(Cursor(active,("teruisi_sales",5432,
                "127.0.0.1")),role,allow_test_login=True)
        with self.assertRaises(RuntimeError):
            migration._role(Cursor((False,)*8),role)

    @staticmethod
    def expected_constraints():
        row=lambda kind,names,target,definition:(kind,names,target,True,False,
            definition)
        rows=[row("p",["id"],None,"PRIMARY KEY (id)")]
        rows += [row("u",list(names),None,definition) for names,definition in (
            (("run_id",),"UNIQUE (run_id)"),
            (("signed_receipt_id",),"UNIQUE (signed_receipt_id)"),
            (("request_digest",),"UNIQUE (request_digest)"),
            (("run_id","attempt"),"UNIQUE (run_id, attempt)"))]
        rows += [row("f",[name],target,"FOREIGN KEY ("+name+
            ") REFERENCES public."+target+"(id) ON DELETE RESTRICT")
            for name,target in (("run_id","ai_business_file_runs"),
                ("report_id","ai_report_runs"),
                ("signed_receipt_id",
                    "protected_business_budget_v11_signed_receipts_v3"))]
        rows += [row("c",[name],None,definition) for name,definition in (
            ("attempt","CHECK (((attempt >= 1) AND (attempt <= 5)))"),
            ("run_version","CHECK ((run_version >= 1))"))]
        rows += [row("c",[name],None,"CHECK ((("+name+
            ")::text ~ '^[0-9a-f]{64}$'::text))") for name in (
            "signed_receipt_sha256","binding_digest","ledger_root",
            "file_page_root","request_digest")]
        return rows

    def test_exact_catalog_constraint_mutations_fail_closed(self):
        rows=self.expected_constraints()
        sql.validate_constraints(rows)
        mutations=(
            lambda r:r[:-1],
            lambda r:[(*x[:5],x[5].replace("attempt <= 5",
                "attempt <= 5) OR (attempt = 0")) if x[1]==["attempt"]
                else x for x in r],
            lambda r:[(*x[:5],x[5].replace("'^[0-9a-f]{64}$'::text",
                "'^[0-9a-f]{64}$'::text OR true")) if
                x[1]==["ledger_root"] else x for x in r],
            lambda r:[(x[0],x[1],"wrong_parent",*x[3:]) if
                x[1]==["signed_receipt_id"] and x[0]=="f" else x for x in r],
            lambda r:[(*x[:3],False,*x[4:]) if x[1]==["run_id"]
                and x[0]=="f" else x for x in r],
            lambda r:r+[r[-1]],
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                with self.assertRaises(ValueError):
                    sql.validate_constraints(mutate(rows))

    def test_frozen_v11_guards_and_0076_signer_bytes(self):
        expected={
            "backend/ai_assistant/business_promotion_budget_v11_stage_sql.py":
                "eee5be413fe183837fcf13ab7460e5e37e4c677c2b1d11a5588c524ca2233c7d",
            "backend/ai_assistant/business_promotion_budget_v11_ticket_sign_sql.py":
                "d77b2d6a8e2b752a0c018ddd20ded900bc674077761401a7bf265c10ae2b9921",
            "backend/ai_assistant/business_volume_files.py":
                "94078f46d2dcd715a0ada5b078bccf45315b02252489bdc37261cd1d2f42fb3a"}
        for name,sha in expected.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),sha)

    def test_ro_chunk_does_not_call_any_for_share_function(self):
        self.assertEqual(len(sql.SIGNATURES),11)
        for source in (sql.READ_REQUIREMENTS_SQL,sql.READ_PAGE_SQL,
                sql.READ_ROOT_SQL,sql.ro_mac_sql(),sql.CHUNK_SQL):
            self.assertNotIn(" FOR SHARE",source)
        self.assertIn(" FOR SHARE",sql.PUBLISH_REQUIREMENTS_SQL)
        self.assertIn("current_setting('transaction_read_only')<>'on'",
            sql.CHUNK_SQL)
        self.assertIn("current_setting('transaction_isolation') NOT IN",
            sql.CHUNK_SQL)
        self.assertIn("('repeatable read','serializable')",sql.CHUNK_SQL)
        self.assertIn("ai_budget_v11_download_ledger_root_v2",sql.CHUNK_SQL)
        self.assertIn("signed.ticket_id,\n    signed.claim_id,parent.id,parent.attempt",
            sql.CHUNK_SQL)
        self.assertIn("published.ledger_root",sql.CHUNK_SQL)
        self.assertIn("published.file_page_root",sql.CHUNK_SQL)
        self.assertIn("ai_budget_v11_private_mac_valid_v4_ro",sql.CHUNK_SQL)
        self.assertIn("replace(encode(part.content,'base64'),chr(10),'')",
            sql.CHUNK_SQL)
        self.assertIn("item.status='active'",sql.ro_mac_sql())
        self.assertIn("SELECT * INTO part FROM public.ai_business_volume_chunks",
            sql.CHUNK_SQL)
        self.assertIn("encode(sha256(part.content),'hex') IS DISTINCT FROM part.content_digest",
            sql.CHUNK_SQL)
        self.assertIn("octet_length(part.content)>524288",sql.CHUNK_SQL)
        self.assertIn("published.request_digest IS DISTINCT FROM selected_request_sha",
            sql.CHUNK_SQL)
        self.assertIn("parent.status<>'paused'",sql.CHUNK_SQL)
        self.assertIn("flow.status<>'completed'",sql.READ_REQUIREMENTS_SQL)
        self.assertIn("jobs.status<>'completed'",sql.READ_ROOT_SQL)
        self.assertIn("p_count NOT BETWEEN 1 AND 20",sql.READ_ROOT_SQL)

    def test_publish_is_append_only_and_never_updates_old_ready_state(self):
        self.assertIn("INSERT INTO public.protected_business_budget_v11_publications_v2",
            sql.PUBLISH_SQL)
        self.assertNotIn("UPDATE public.ai_business_file_runs",sql.PUBLISH_SQL)
        self.assertIn("parent.status<>'paused'",sql.PUBLISH_SQL)
        self.assertIn("readyAuthorized',false",sql.PUBLISH_SQL)
        self.assertIn("retryAllowed',false",sql.OUTCOME_SQL)
        self.assertIn("ai_budget_v11_publish_v2_existing_use_outcome",
            sql.PUBLISH_SQL)

    def test_formal_backup_restore_and_migration_remain_closed(self):
        backup=(ROOT/"tools/postgres-consistent-backup.py").read_text(
            encoding="utf-8")
        operator=(ROOT/"tools/django-postgres-maintenance.ps1").read_text(
            encoding="utf-8")
        installer=(ROOT/"tools/django-local-service.ps1").read_text(
            encoding="utf-8")
        audit=(ROOT/"tools/protected-ai-restore-static-audit.py").read_text(
            encoding="utf-8")
        for content, marker in ((backup,
                "0078_business_promotion_budget_v11_signed_publication"),
                (backup,"protected_business_budget_v11_publications_v2"),
                (operator,"|77|78"),
                (installer,"0078_business_promotion_budget_v11_signed_publication.py"),
                (audit,"formal restore gate omits protected 0078 receipt")):
            self.assertIn(marker,content)
        self.assertIn("protected AI daily backup is not admitted",backup)
        self.assertIn("protected AI archive restore is not admitted",backup)

    def test_0077_to_0078_focused_upgrade_is_isolated_and_frozen(self):
        script=(ROOT/"tools/business-v11-publication-upgrade-rehearsal.py"
            ).read_text(encoding="utf-8")
        runner=(ROOT/"tools/ai-postgres-rehearsal.py").read_text(
            encoding="utf-8")
        for marker in ("0077_business_market_v6_paused_topology",
                "0078_business_promotion_budget_v11_signed_publication",
                "old73._frozen(cursor)","old77._old_functions(cursor)",
                "old76.v3.SIGNATURES","old77.v6.SIGNATURES",
                "oldFileByteDigests","beforeBackupRestored",
                "afterBackupRestored","formalBackupRejectedBeforeArchive",
                "formalRestoreRejectedBeforeDatabase",
                "formalPrepareDeployRejected",
                "emptyReversePreservedNoLoginReaderRole"):
            self.assertIn(marker,script)
        self.assertIn("business_v11_publication_focused_upgrade",runner)
        self.assertIn("0078 focused upgrade is one isolated standalone run",
            runner)
        self.assertIn("arguments.preprovision_ai_runtime_roles = True",runner)


if __name__=="__main__": unittest.main()
