from __future__ import annotations

import argparse
import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "postgres-consistent-backup.py"
SPEC = importlib.util.spec_from_file_location("postgres_consistent_backup", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
sys.path.insert(0, str(ROOT / "backend"))
from ai_assistant.table_manifest import AI_TABLES as CURRENT_AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_V4_REPLAY_PROGRESS as PRE_REPLAY_AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_MARKET_V2_MATERIALS as PRE_MARKET_AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_V4_CONSUMPTIONS as PRE_CONSUMPTION_AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_V4_TICKETS as PRE_TICKET_AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_V4_SEALS as PRE_SEAL_AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_V4_VALIDATION as PRE_VALIDATION_AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_V4_LEDGER as PRE_V4_AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_V3_REPORT_INTENTS as PRE_INTENT_AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS as AI_TABLES
SCREENING_TABLES = {"ai_business_screening_runs", "ai_business_screening_pages"}
PRE_EVIDENCE_TABLES = set(AI_TABLES) - {"ai_business_evidence_runs", "ai_business_evidence_chunks", "ai_business_file_runs", "ai_business_file_chunks", "ai_business_evidence_sources", "ai_business_volume_chunks", "ai_business_budget_plans"} - SCREENING_TABLES


def _finance_guard():
    return {"columns": [("transaction_id", True, "bigint"),
                ("baseline_revision", True, "bigint"),
                ("baseline_digest", True, "character varying(64)")],
        "primaryKey": [("PRIMARY KEY (transaction_id)",)],
        "triggers": list(MODULE.FINANCE_MARKER_TRIGGERS),
        "functions": [(name, True, ["search_path=pg_catalog, public"])
            for name in sorted(MODULE.FINANCE_MARKER_FUNCTIONS)],
        "privileges": (False,)*28}


def _finance_monotonic():
    return {"triggers":[MODULE.FINANCE_MONOTONIC_TRIGGER],
        "functions":[(MODULE.FINANCE_MONOTONIC_FUNCTION,False,
            ["search_path=pg_catalog, public"])],
        "privileges":(False,)*4,"ownership":(False,False)}


def _netshop_guard():
    return {"columns": [("transaction_id",True,"bigint"),
                ("baseline_revision",True,"bigint"),
                ("baseline_digest",True,"character varying(64)")],
        "primaryKey":[("PRIMARY KEY (transaction_id)",)],
        "triggers":list(MODULE.NETSHOP_MARKER_TRIGGERS),
        "functions":[(name,definer,["search_path=pg_catalog, public"])
            for name,definer in MODULE.NETSHOP_MARKER_FUNCTIONS.items()],
        "privileges":(False,)*34}


def _market_v2_guards():
    import importlib
    profile = importlib.import_module(
        "ai_assistant.migrations.0044_business_market_v2_profile")
    return [
        ("ai_report_runs", "ai_market_v2_report_guard", 31, False, False,
         "O", "ai_market_v2_parked_report_guard", "public", "",
         profile.REPORT_GUARD.split("$$")[1], ["search_path=pg_catalog,public"],
         False, "plpgsql"),
        ("ai_workflow_runs", "ai_market_v2_workflow_guard", 31, False, False,
         "O", "ai_market_v2_parked_workflow_guard", "public", "",
         profile.WORKFLOW_GUARD.split("$$")[1], ["search_path=pg_catalog,public"],
         False, "plpgsql"),
        ("ai_workflow_runs", "ai_market_v2_workflow_complete", 5, True, True,
         "O", "ai_market_v2_parked_orphan_guard", "public", "",
         profile.ORPHAN_GUARD.split("$$")[1], ["search_path=pg_catalog,public"],
         False, "plpgsql"),
        ("ai_agent_jobs", "ai_market_v2_job_guard", 7, False, False,
         "O", "ai_market_v2_parked_job_guard", "public", "",
         profile.JOB_GUARD.split("$$")[1], ["search_path=pg_catalog,public"],
         False, "plpgsql"),
    ]


def _market_material_state():
    import importlib
    migration = importlib.import_module(
        "ai_assistant.migrations.0045_business_market_v2_material_attestation")
    truncate = importlib.import_module(
        "ai_assistant.migrations.0041_business_v4_seal_ticket")
    return {"role": (False,) * 7, "member": None,
        "table": ("r", "fixture_owner"), "privilege": (False,),
        "columns": [
            ("report_id", "character varying(160)", True),
            ("source_report_id", "character varying(160)", True),
            *[(name, "character varying(64)", True) for name in (
                "source_snapshot_digest", "source_workflow_input_digest",
                "selector_digest", "algorithms_digest", "manifest_digest",
                "manifest_json_sha256", "summary_digest")],
            ("table_spec_digests_json", "text", True),
            ("manifest_json", "text", True), ("summary_json", "text", True),
            ("created_at", "timestamp with time zone", True)],
        "constraints": [
            ("p", "PRIMARY KEY (report_id)"),
            ("f", "FOREIGN KEY (report_id) REFERENCES ai_report_runs(id) ON DELETE RESTRICT"),
            ("f", "FOREIGN KEY (source_report_id) REFERENCES ai_report_runs(id) ON DELETE RESTRICT")],
        "triggers": [
            ("ai_business_market_v2_materials", "ai_market_v2_material_guard",
             31, False, False, "O", "public", "ai_market_v2_material_guard", ""),
            ("ai_business_market_v2_materials", "ai_market_v2_material_no_truncate",
             34, False, False, "O", "public", "ai_v4_seal_ticket_no_truncate", "")],
        "guard": (migration.GUARD.split("$$")[1], False,
                  ["search_path=pg_catalog,public"], "plpgsql", "fixture_owner"),
        "attest": (migration.ATTEST.split("$$")[1], True,
                   ["search_path=pg_catalog,public"], "plpgsql", "fixture_owner"),
        "no_truncate": (truncate.NO_TRUNCATE.split("$$")[1], False,
                        ["search_path=pg_catalog,public"], "plpgsql", "fixture_owner"),
        "guard_acl": (False, False, False),
        "attest_acl": (True, False, False),
        "no_truncate_acl": (False, False, False)}


def _promotion_trial_state():
    import importlib
    migration = importlib.import_module(
        "ai_assistant.migrations.0046_business_promotion_trial_file_guard")
    signatures = (
        "public.ai_business_file_chunk_guard()",
        "public.ai_business_volume_chunk_guard()",
        "public.ai_business_volume_manifest_check(text,integer,text,boolean,text)",
        "public.ai_business_files_guard()",
        "public.ai_business_volume_complete_guard()",
        "public.ai_business_promotion_trial_parent_requirements(text,text,text)",
        "public.ai_business_promotion_trial_ready_requirements(text)",
    )
    bodies = (*migration.NEW_SQL, migration.PARENT_REQUIREMENTS,
              migration.READY_REQUIREMENTS)
    oids = {signature: 1000 + i for i, signature in enumerate(signatures)}
    return {
        "constraint": (True, "CHECK ((renderer_version = ANY "
                       "(ARRAY[1, 2, 3, 4, 5, 6, 7, 9])))"),
        "functions": {signature: (body.split("$$")[1], False,
                       ["search_path=pg_catalog,public"], "plpgsql",
                       "fixture_owner", oids[signature])
                      for signature, body in zip(signatures, bodies)},
        "acls": {signature: {("OWNER", "EXECUTE", False),
                     (("PUBLIC" if i < 5 else "teruisi_ai_writer"),
                      "EXECUTE", False)}
                 for i, signature in enumerate(signatures)},
        "oids": oids,
        "triggers": [
            ("ai_business_file_chunks", "ai_business_file_chunk_state", 7,
             False, False, "O", oids[signatures[0]]),
            ("ai_business_volume_chunks", "ai_business_volume_chunk_state", 7,
             False, False, "O", oids[signatures[1]]),
            ("ai_business_file_runs", "ai_business_volume_initial", 7,
             False, False, "O", oids[signatures[3]]),
            ("ai_business_file_runs", "ai_business_file_state", 27,
             False, False, "O", oids[signatures[3]]),
            ("ai_business_file_runs", "ai_business_volume_complete", 21,
             True, True, "O", oids[signatures[4]]),
            ("ai_business_volume_chunks", "ai_business_volume_complete", 5,
             True, True, "O", oids[signatures[4]]),
        ],
    }


class _EvidenceCursor:
    def __init__(self, tables, migrations, finance_guard=None,
                  finance_monotonic=None, netshop_guard=None,
                  seal_guard_body_override=None,
                  verifier_body_override=None, market_guards=None,
                  market_material=None, promotion_trial=None):
        self.tables = sorted(tables)
        self.migrations = sorted(migrations)
        self.finance_guard = finance_guard or _finance_guard()
        self.finance_monotonic = finance_monotonic if finance_monotonic is not None else (
            _finance_monotonic() if ("finance",MODULE.FINANCE_MONOTONIC_MIGRATION)
            in self.migrations else {"triggers":[],"functions":[]})
        self.netshop_guard = netshop_guard or _netshop_guard()
        self.seal_guard_body_override = seal_guard_body_override
        self.verifier_body_override = verifier_body_override
        self.market_guards = market_guards if market_guards is not None else _market_v2_guards()
        self.market_material = market_material if market_material is not None else _market_material_state()
        self.promotion_trial = promotion_trial if promotion_trial is not None else _promotion_trial_state()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params=None):
        query = statement if isinstance(statement, str) else statement.as_string()
        if "current_database()" in query:
            self.rows = [("fixture", "fixture_owner", "127.0.0.1/32", 15479, False, 170011)]
        elif "pg_catalog.pg_tables" in query:
            self.rows = [(table,) for table in self.tables]
        elif "pg_catalog.pg_get_function_identity_arguments" in query and "ai_market_v2_report_guard" in query:
            self.rows = self.market_guards
        elif "WHERE rolname=%s" in query and "rolcanlogin" in query:
            self.rows = [self.market_material["role"]]
        elif "FROM pg_catalog.pg_auth_members membership" in query:
            self.rows = ([] if self.market_material["member"] is None
                         else [self.market_material["member"]])
        elif "c.relname='ai_business_market_v2_materials'" in query and "c.relkind" in query:
            self.rows = [self.market_material["table"]]
        elif "a.attrelid='public.ai_business_market_v2_materials'::regclass" in query:
            self.rows = self.market_material["columns"]
        elif "c.conrelid='public.ai_business_market_v2_materials'::regclass" in query:
            self.rows = self.market_material["constraints"]
        elif ("ai_business_market_v2_materials" in str(params)
              and ("has_table_privilege(%s,%s,%s)" in query
                   or "has_any_column_privilege(%s,%s,%s)" in query)):
            self.rows = [self.market_material["privilege"]]
        elif "c.relname='ai_business_market_v2_materials'" in query and "t.tgtype" in query:
            self.rows = self.market_material["triggers"]
        elif ("pg_catalog.pg_get_userbyid(p.proowner)" in query
              and "to_regprocedure(%s)" in query
              and ("ai_market_v2_" in str(params)
                   or "ai_v4_seal_ticket_no_truncate" in str(params))):
            key = ("guard" if "material_guard" in params[0] else
                   "no_truncate" if "no_truncate" in params[0] else "attest")
            self.rows = [self.market_material[key]]
        elif "pg_catalog.has_function_privilege(%s,%s,'EXECUTE')" in query:
            key = ("guard_acl" if "material_guard" in params[1] else
                   "no_truncate_acl" if "no_truncate" in params[1] else "attest_acl")
            self.rows = [self.market_material[key]]
        elif "SELECT app, name FROM django_migrations" in query:
            self.rows = self.migrations
        elif "c.conname='ai_business_file_bound'" in query:
            self.rows = [self.promotion_trial["constraint"]]
        elif "p.oid=to_regprocedure(%s)" in query and "pg_catalog.pg_language l" in query and "p.oid" in query:
            self.rows = [self.promotion_trial["functions"].get(params[0])]
        elif "pg_catalog.aclexplode" in query:
            self.rows = list(self.promotion_trial["acls"].get(params[0], set()))
        elif "'ai_business_file_chunk_state'" in query and "t.tgfoid" in query:
            self.rows = self.promotion_trial["triggers"]
        elif "SELECT to_regprocedure(%s)::oid" in query:
            self.rows = [(self.promotion_trial["oids"].get(params[0]),)]
        elif "FROM pg_catalog.pg_attribute a" in query and "finance_source_revision_markers" in query:
            self.rows = self.finance_guard["columns"]
        elif "FROM pg_catalog.pg_attribute a" in query and "netshop_source_revision_markers" in query:
            self.rows = self.netshop_guard["columns"]
        elif "pg_catalog.pg_get_constraintdef" in query and "finance_source_revision_markers" in query:
            self.rows = self.finance_guard["primaryKey"]
        elif "pg_catalog.pg_get_constraintdef" in query and "netshop_source_revision_markers" in query:
            self.rows = self.netshop_guard["primaryKey"]
        elif "t.tgname='finance_revision_monotonic'" in query:
            self.rows = self.finance_monotonic["triggers"]
        elif "p.proname='finance_revision_monotonic_guard'" in query:
            self.rows = self.finance_monotonic["functions"]
        elif "FROM pg_catalog.pg_trigger t" in query and "finance_source_revision" in str(params):
            self.rows = self.finance_guard["triggers"]
        elif "FROM pg_catalog.pg_trigger t" in query and "netshop_source_revision" in str(params):
            self.rows = self.netshop_guard["triggers"]
        elif "FROM pg_catalog.pg_proc p" in query and "finance_source_revision" in str(params):
            self.rows = self.finance_guard["functions"]
        elif "FROM pg_catalog.pg_proc p" in query and "netshop_source_revision" in str(params):
            self.rows = self.netshop_guard["functions"]
        elif "pg_catalog.has_table_privilege" in query and "finance_source_revision_markers" in str(params):
            self.rows = [self.finance_guard["privileges"]]
        elif "pg_catalog.has_table_privilege" in query and "public.finance_data_revisions" in str(params):
            self.rows = [self.finance_monotonic["privileges"]]
        elif "pg_catalog.pg_has_role" in query and "finance_data_revisions" in query:
            self.rows = [self.finance_monotonic["ownership"]]
        elif "pg_catalog.has_table_privilege" in query and "netshop_source_revision_markers" in str(params):
            self.rows = [self.netshop_guard["privileges"]]
        elif "WHERE rolname='teruisi_ai_seal_writer'" in query:
            self.rows = [(False,) * 7]
        elif "t.tgname IN ('ai_v4_ticket_immutable'" in query:
            self.rows = [(2,)]
        elif "c.relname='ai_business_v4_seal_consumptions'" in query:
            self.rows = [(3,)]
        elif "t.tgname='ai_v4_seal_consumption_required'" in query:
            import importlib
            guard = importlib.import_module(
                "ai_assistant.migrations.0043_business_v4_seal_consumption_candidate"
            ).REQUIRE_CONSUMPTION.split("$$")[1]
            if self.seal_guard_body_override is not None:
                guard = self.seal_guard_body_override
            self.rows = [(5, True, True, "O", True, True,
                          ["search_path=pg_catalog,public"], guard, False)]
        elif "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)" in query:
            import importlib
            body = importlib.import_module(
                "ai_assistant.migrations.0043_business_v4_seal_consumption_candidate"
            ).VERIFY_CONSUMPTION.split("$$")[1]
            if self.verifier_body_override is not None:
                body = self.verifier_body_override
            self.rows = [(body, True, ["search_path=pg_catalog,public"],
                          "fixture_owner")]
        elif "has_any_column_privilege(%s,%s,'SELECT')" in query:
            self.rows = [(False,) * 5]
        elif "SELECT to_regprocedure(%s)" in query:
            self.rows = [(params[0],)]
        elif "has_function_privilege('teruisi_ai_seal_writer',%s,'EXECUTE')" in query:
            name = params[0]
            self.rows = [(
                name.startswith("public.ai_v4_claim_seal_ticket(") or
                name.startswith("public.ai_v4_sealer_consumption_result(") or
                name.startswith("public.ai_v4_sealer_ticket_"),
                name.startswith("public.ai_v4_issue_seal_ticket(") or
                name.startswith("public.ai_v4_verify_seal_consumption(") or
                name == "public.ai_v4_lock_source_revisions_for_admission()",
                False)]
        elif query.startswith("SELECT COUNT(*)"):
            self.rows = [(0,)]
        elif "FROM sales_data_revisions" in query:
            self.rows = [("sales", 1), ("erp", 1)]
        elif "FROM netshop_data_revisions" in query:
            self.rows = [("netshop",1,"a"*64)]
        elif "FROM netshop_write_authority" in query:
            self.rows = [("d1","","","")]
        elif "FROM sales_write_authority" in query:
            self.rows = [("active", "11111111-1111-1111-1111-111111111111", "fixture-cutover")]
        elif "FROM ai_data_revisions" in query:
            self.rows = [(0, "")]
        elif "FROM ai_write_authority" in query:
            self.rows = [("d1", "", "", "")]
        else:
            raise AssertionError("Unexpected evidence query: " + query)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def _ai_evidence(tables, migrations, finance_guard=None,
                 finance_monotonic=None, netshop_guard=None,
                 seal_guard_body_override=None,
                 verifier_body_override=None, market_guards=None,
                 market_material=None, promotion_trial=None):
    base_tables = {
        "django_migrations", "sales_data_revisions", "sales_import_batches",
        "sales_order_lines", "sales_write_authority", "erp_product_master",
    }
    cursor = _EvidenceCursor(base_tables | set(tables),
        [("sales", "0001_initial"), *migrations], finance_guard,
        finance_monotonic, netshop_guard, seal_guard_body_override,
        verifier_body_override, market_guards, market_material,
        promotion_trial)
    connection = mock.Mock()
    connection.cursor.return_value = cursor
    return MODULE.collect_evidence(connection, "fixture", "fixture_owner")


class _SnapshotCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement):
        self.statement = statement

    def fetchone(self):
        return ("00000003-00000001-1",)


class _SnapshotConnection:
    def __init__(self):
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement):
        self.statement = statement

    def cursor(self):
        return _SnapshotCursor()

    def commit(self):
        self.committed = True


class ConsistentBackupTests(unittest.TestCase):
    def test_finance_0006_exact_receipt_and_tables_block_formal_archive(self):
        self.assertEqual(MODULE.FINANCE_RAW_WORKBOOK_MIGRATION,
            "0006_raw_workbook_bytes_v2")
        self.assertEqual(MODULE.FINANCE_RAW_WORKBOOK_TABLES, {
            "finance_raw_workbook_attestations",
            "finance_raw_workbook_columns",
            "finance_raw_workbook_cells"})

        class Cursor:
            def __init__(self, receipt, tables):
                self.receipt, self.tables, self.calls = receipt, tables, 0

            def execute(self, *_args):
                self.calls += 1

            def fetchone(self):
                return (self.receipt,)

            def fetchall(self):
                return [(item,) for item in self.tables]

        self.assertTrue(MODULE._finance_raw_workbook_preflight(Cursor(
            True, MODULE.FINANCE_RAW_WORKBOOK_TABLES)))
        self.assertFalse(MODULE._finance_raw_workbook_preflight(Cursor(
            False, set())))
        for receipt, tables in ((True, set()),
                (False, MODULE.FINANCE_RAW_WORKBOOK_TABLES),
                (True, {"finance_raw_workbook_attestations"})):
            with self.subTest(receipt=receipt, tables=tables), \
                    self.assertRaises(RuntimeError):
                MODULE._finance_raw_workbook_preflight(Cursor(receipt, tables))

    def test_0075_without_new_tables_is_still_a_protected_backup_receipt(self):
        name = "0075_business_v4_report_restricted_page"
        self.assertIn(name, MODULE.PROTECTED_AI_MIGRATIONS)
        self.assertEqual(MODULE.PROTECTED_AI_TABLES_BY_MIGRATION[name], set())
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('if protected["appliedProtectedMigrations"]:', source)
        self.assertIn('raise RuntimeError("protected AI daily backup is not admitted")', source)

    def test_protected_0073_has_exact_table_role_and_migration_inventory(self):
        name = "0073_business_promotion_budget_v11_login_attestation"
        self.assertEqual(MODULE.PROTECTED_AI_TABLES_BY_MIGRATION[name],
            {"protected_business_budget_v11_login_attestations"})
        self.assertIn(name, MODULE.PROTECTED_AI_MIGRATIONS)
        self.assertIn("teruisi_ai_budget_v11_attestor_v2_login",
            MODULE.PROTECTED_AI_ROLES)

    def test_promotion_trial_renderer9_requires_0046_receipt_and_exact_file_gates(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 46)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_REPLAY_AI_TABLES, migrations)
        previous = _ai_evidence(PRE_REPLAY_AI_TABLES, migrations[:-1])
        self.assertEqual(len([table for table in current["tables"]
                              if table.startswith("ai_")]), 78)
        self.assertNotEqual(current["contentSha256"], previous["contentSha256"])
        self.assertEqual(_ai_evidence(PRE_REPLAY_AI_TABLES, migrations[:-1],
                                     promotion_trial={})["tables"], previous["tables"])
        for missing in ("0045_business_market_v2_material_attestation",
                        "0029_business_promotion_file_ready"):
            with self.subTest(missing=missing), self.assertRaisesRegex(RuntimeError,
                    "predecessor"):
                _ai_evidence(PRE_REPLAY_AI_TABLES,
                    [item for item in migrations if item[1] != missing])

        def mutated(change):
            state = copy.deepcopy(_promotion_trial_state())
            change(state)
            return state

        first = next(iter(_promotion_trial_state()["functions"]))
        new = "public.ai_business_promotion_trial_ready_requirements(text)"
        cases = (
            lambda s: s.update(constraint=(False, s["constraint"][1])),
            lambda s: s.update(constraint=(True,
                s["constraint"][1].replace("6, 7, 9", "6, 7, 8, 9"))),
            lambda s: s.update(constraint=(True,
                s["constraint"][1] + " OR TRUE")),
            lambda s: s["functions"].__setitem__(first,
                ("BEGIN RETURN NEW; END", *s["functions"][first][1:])),
            lambda s: s["functions"].__setitem__(new,
                ("BEGIN RETURN; END", *s["functions"][new][1:])),
            lambda s: s["functions"].__setitem__(new,
                (s["functions"][new][0], True, *s["functions"][new][2:])),
            lambda s: s["acls"][new].add(("PUBLIC", "EXECUTE", False)),
            lambda s: s["acls"][first].remove(("PUBLIC", "EXECUTE", False)),
            lambda s: s["triggers"].__setitem__(0,
                (*s["triggers"][0][:6], 99999)),
            lambda s: s["triggers"].__setitem__(0,
                (*s["triggers"][0][:5], "D", s["triggers"][0][6])),
            lambda s: s["triggers"].pop(),
        )
        for index, change in enumerate(cases):
            with self.subTest(drift=index), self.assertRaisesRegex(RuntimeError,
                    "promotion trial file"):
                _ai_evidence(PRE_REPLAY_AI_TABLES, migrations,
                             promotion_trial=mutated(change))

    def test_finance_0004_monotonic_trigger_function_and_writer_boundary(self):
        migrated = [("finance","0002_finance_target_gross_margin"),
            ("finance",MODULE.FINANCE_MARKER_MIGRATION),
            ("finance",MODULE.FINANCE_MONOTONIC_MIGRATION)]
        marker = {MODULE.FINANCE_MARKER_TABLE}
        self.assertIn(MODULE.FINANCE_MARKER_TABLE,
            _ai_evidence(marker,migrated)["tables"])
        for state in (
                {"triggers":[],"functions":_finance_monotonic()["functions"],
                    "privileges":(False,)*4,"ownership":(False,False)},
                {**_finance_monotonic(),"functions":[]},
                {**_finance_monotonic(),"functions":[
                    (MODULE.FINANCE_MONOTONIC_FUNCTION,True,
                        ["search_path=pg_catalog, public"])]},
                {**_finance_monotonic(),"privileges":(True,False,False,False)},
                {**_finance_monotonic(),"ownership":(True,False)}):
            with self.subTest(state=state),self.assertRaises(RuntimeError):
                _ai_evidence(marker,migrated,finance_monotonic=state)
        with self.assertRaisesRegex(RuntimeError,"no migration receipt"):
            _ai_evidence(marker,migrated[:-1],
                finance_monotonic=_finance_monotonic())

    def test_netshop_0003_marker_and_monotonic_guards_are_versioned(self):
        base = {"netshop_data_revisions","netshop_import_batches",
            "netshop_rows","netshop_write_authority"}
        old = [("netshop","0001_initial"),
            ("netshop","0002_migration_run_time_order")]
        current = [*old,("netshop",MODULE.NETSHOP_MARKER_MIGRATION)]
        self.assertIn("netshop_rows",_ai_evidence(base,old)["tables"])
        self.assertIn(MODULE.NETSHOP_MARKER_TABLE,
            _ai_evidence(base | {MODULE.NETSHOP_MARKER_TABLE},current)["tables"])
        with self.assertRaisesRegex(RuntimeError,"no migration receipt"):
            _ai_evidence(base | {MODULE.NETSHOP_MARKER_TABLE},old)
        with self.assertRaisesRegex(RuntimeError,"lacks table"):
            _ai_evidence(base,current)
        with self.assertRaisesRegex(RuntimeError,"predecessor"):
            _ai_evidence(base | {MODULE.NETSHOP_MARKER_TABLE},current[:1]+current[2:])
        changes = []
        value = _netshop_guard(); value["columns"].pop(); changes.append(value)
        value = _netshop_guard(); value["primaryKey"].clear(); changes.append(value)
        value = _netshop_guard(); value["triggers"].pop(); changes.append(value)
        value = _netshop_guard(); value["functions"].pop(); changes.append(value)
        value = _netshop_guard(); value["functions"][0] = (
            value["functions"][0][0],False,["search_path=public"]); changes.append(value)
        value = _netshop_guard(); value["privileges"] = (False,)*10+(True,)+(False,)*23; changes.append(value)
        for state in changes:
            with self.subTest(state=state),self.assertRaises(RuntimeError):
                _ai_evidence(base | {MODULE.NETSHOP_MARKER_TABLE},current,
                    netshop_guard=state)

    def test_finance_0003_marker_schema_requires_exact_migration_and_guards(self):
        old = [("finance", "0002_finance_target_gross_margin")]
        current = [*old, ("finance", MODULE.FINANCE_MARKER_MIGRATION)]
        marker = {MODULE.FINANCE_MARKER_TABLE}
        before = _ai_evidence(set(), old)
        adopted = _ai_evidence(marker, current)
        self.assertIn(MODULE.FINANCE_MARKER_TABLE, adopted["tables"])
        self.assertNotEqual(before["contentSha256"], adopted["contentSha256"])
        with self.assertRaisesRegex(RuntimeError, "no migration receipt"):
            _ai_evidence(marker, old)
        with self.assertRaisesRegex(RuntimeError, "lacks table"):
            _ai_evidence(set(), current)
        with self.assertRaisesRegex(RuntimeError, "predecessor"):
            _ai_evidence(marker, current[1:])
        for change in (
                lambda state: state["columns"].pop(),
                lambda state: state["primaryKey"].clear(),
                lambda state: state["triggers"].pop(),
                lambda state: state["functions"].pop(),
                lambda state: state["functions"].__setitem__(0,
                    (state["functions"][0][0], False, state["functions"][0][2])),
                lambda state: state["functions"].__setitem__(0,
                    (state["functions"][0][0], True, ["search_path=public"])),
                lambda state: state.update(privileges=(True,)+(False,)*27),
                lambda state: state.update(privileges=(False,)*9+(True,)+(False,)*18)):
            state = _finance_guard()
            change(state)
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                _ai_evidence(marker, current, state)

    def test_v4_ledger_generation_has_explicit_71_table_boundary(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 35)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_VALIDATION_AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 71)
        self.assertTrue({"ai_business_v4_runs", "ai_business_v4_sources",
            "ai_business_v4_chunks", "ai_business_v4_tool_receipts"} <= set(current["tables"]))
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_V4_AI_TABLES, migrations)
        with self.assertRaises(RuntimeError):
            _ai_evidence(CURRENT_AI_TABLES, migrations[:-1])

    def test_v4_validation_generation_has_explicit_73_table_boundary(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 36)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_SEAL_AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 73)
        self.assertIn("ai_business_v4_validation_attempts", current["tables"])
        self.assertIn("ai_business_v4_validation_segments", current["tables"])
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_VALIDATION_AI_TABLES, migrations)
        with self.assertRaises(RuntimeError):
            _ai_evidence(CURRENT_AI_TABLES, migrations[:-1])

    def test_v4_seal_generation_has_explicit_74_table_boundary(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 38)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_TICKET_AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 74)
        self.assertIn("ai_business_v4_seals", current["tables"])
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_SEAL_AI_TABLES, migrations)
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_TICKET_AI_TABLES, migrations[:-1])

    def test_v4_ticket_generation_has_explicit_76_table_boundary(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 41)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_CONSUMPTION_AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 76)
        self.assertIn("ai_business_v4_seal_tickets", current["tables"])
        self.assertIn("ai_business_v4_seal_claims", current["tables"])
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_TICKET_AI_TABLES, migrations)
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_CONSUMPTION_AI_TABLES, migrations[:-1])

    def test_v4_claimed_reader_retains_76_tables_and_closed_publication(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 42)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_CONSUMPTION_AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 76)
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_CONSUMPTION_AI_TABLES,
                [item for item in migrations if item[1] != "0041_business_v4_seal_ticket"])

    def test_v4_consumption_candidate_has_explicit_77_table_boundary(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 43)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_MARKET_AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 77)
        self.assertIn("ai_business_v4_seal_consumptions", current["tables"])
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_CONSUMPTION_AI_TABLES, migrations)
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_MARKET_AI_TABLES, migrations[:-1])
        with self.assertRaisesRegex(RuntimeError,
                "consumption commit fence missing"):
            _ai_evidence(PRE_MARKET_AI_TABLES, migrations,
                seal_guard_body_override="BEGIN RETURN NULL; END")
        with self.assertRaisesRegex(RuntimeError,
                "consumption verifier missing"):
            _ai_evidence(PRE_MARKET_AI_TABLES, migrations,
                verifier_body_override="BEGIN RETURN true; END")

    def test_market_v2_parked_guards_follow_exact_migration_receipt(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 44)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_MARKET_AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 77)
        before = _ai_evidence(PRE_MARKET_AI_TABLES, migrations[:-1], market_guards=[])
        self.assertNotEqual(current["contentSha256"], before["contentSha256"])
        with self.assertRaisesRegex(RuntimeError, "predecessor"):
            _ai_evidence(PRE_MARKET_AI_TABLES,
                [item for item in migrations if item[1] !=
                 "0043_business_v4_seal_consumption_candidate"])
        original = _market_v2_guards()
        for index, value in (
                (0, "ai_workflow_runs"), (1, "ai_market_v2_wrong_guard"),
                (2, 5), (3, True), (4, True), (5, "D"),
                (6, "ai_market_v2_parked_job_guard"), (7, "evil"),
                (8, "text"), (9, "BEGIN RETURN NEW; END"),
                (10, ["search_path=public"]), (11, True), (12, "sql")):
            damaged = list(original)
            row = list(damaged[0]); row[index] = value; damaged[0] = tuple(row)
            with self.subTest(column=index), self.assertRaisesRegex(RuntimeError,
                    "market v2 parked profile"):
                _ai_evidence(PRE_MARKET_AI_TABLES, migrations, market_guards=damaged)
        for damaged in (original[:-1], original + [original[0]]):
            with self.assertRaisesRegex(RuntimeError, "market v2 parked profile"):
                _ai_evidence(PRE_MARKET_AI_TABLES, migrations, market_guards=damaged)

    def test_market_v2_material_requires_0045_receipt_and_closed_contract(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 45)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_REPLAY_AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 78)
        self.assertIn("ai_business_market_v2_materials", current["tables"])
        before = _ai_evidence(PRE_MARKET_AI_TABLES, migrations[:-1])
        self.assertEqual(len([name for name in before["tables"] if name.startswith("ai_")]), 77)
        with self.assertRaisesRegex(RuntimeError, "predecessor"):
            _ai_evidence(PRE_REPLAY_AI_TABLES,
                [item for item in migrations if item[1] != "0044_business_market_v2_profile"])
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_MARKET_AI_TABLES, migrations)
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_REPLAY_AI_TABLES, migrations[:-1])
        cases = [
            ("role", (True,) + (False,) * 6),
            ("member", (1,)),
            ("table", ("v", "fixture_owner")),
            ("table", ("r", "teruisi_ai_market_attestor")),
            ("columns", _market_material_state()["columns"][:-1]),
            ("constraints", _market_material_state()["constraints"][:-1]),
            ("privilege", (True,)),
            ("triggers", _market_material_state()["triggers"][:1]),
            ("guard", ("BEGIN RETURN NEW; END", False,
                       ["search_path=pg_catalog,public"], "plpgsql", "fixture_owner")),
            ("attest", ("BEGIN RETURN; END", True,
                        ["search_path=pg_catalog,public"], "plpgsql", "fixture_owner")),
            ("no_truncate", ("BEGIN RETURN NULL; END", False,
                             ["search_path=pg_catalog,public"], "plpgsql", "fixture_owner")),
            ("attest_acl", (False, False, False)),
            ("guard_acl", (True, False, False)),
            ("no_truncate_acl", (True, False, False)),
        ]
        for key, bad in cases:
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError,
                    "market v2 material"):
                state = _market_material_state()
                state[key] = bad
                _ai_evidence(PRE_REPLAY_AI_TABLES, migrations, market_material=state)

    def test_paused_intent_generation_has_explicit_67_table_boundary(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 34)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_V4_AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 67)
        self.assertIn("ai_business_v3_report_intents", current["tables"])
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_INTENT_AI_TABLES, migrations)
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_V4_AI_TABLES, migrations[:-1])

    def test_receipt_generation_has_explicit_66_table_boundary(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py")
                       if p.stem[:4].isdigit() and int(p.stem[:4]) <= 32)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(PRE_INTENT_AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 66)
        self.assertIn("ai_business_source_tool_receipts", current["tables"])
        with self.assertRaises(RuntimeError):
            _ai_evidence(AI_TABLES, migrations)
        with self.assertRaises(RuntimeError):
            _ai_evidence(PRE_INTENT_AI_TABLES, migrations[:-1])

    def test_business_evidence_tables_require_exact_migration_generation(self):
        names = sorted(p.stem for p in (ROOT / "backend/ai_assistant/migrations").glob("*.py") if p.stem[:4].isdigit() and int(p.stem[:4]) <= 24)
        migrations = [("ai_assistant", name) for name in names]
        current = _ai_evidence(AI_TABLES, migrations)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 65)
        before_runtime = _ai_evidence(AI_TABLES, [m for m in migrations if int(m[1][:4]) <= 23])
        self.assertEqual(len([name for name in before_runtime["tables"] if name.startswith("ai_")]),65)
        self.assertNotEqual(current["contentSha256"],before_runtime["contentSha256"])
        # Removing both storage tables must not disguise 0024 without 0023 as a
        # legitimate historical 63-table backup.
        with self.assertRaisesRegex(RuntimeError,"storage predecessor"):
            _ai_evidence(set(AI_TABLES)-SCREENING_TABLES,[m for m in migrations if m[1] != "0023_business_screening_storage"])
        old_tables = set(AI_TABLES)-SCREENING_TABLES
        old_migrations = [m for m in migrations if int(m[1][:4]) <= 22]
        before_screening = _ai_evidence(old_tables, old_migrations)
        self.assertEqual(len([name for name in before_screening["tables"] if name.startswith("ai_")]), 63)
        self.assertNotEqual(current["contentSha256"],before_screening["contentSha256"])
        for table in SCREENING_TABLES:
            with self.assertRaises(RuntimeError): _ai_evidence(set(AI_TABLES)-{table}, migrations)
        for name in ("0022_business_integrated_reports","0023_business_screening_storage"):
            with self.assertRaises(RuntimeError): _ai_evidence(AI_TABLES,[m for m in migrations if m[1] != name])
        volume_tables = old_tables-{"ai_business_budget_plans"}
        volume_migrations = [m for m in migrations if int(m[1][:4]) <= 20]
        before_budgets = _ai_evidence(volume_tables, volume_migrations)
        self.assertEqual(len([name for name in before_budgets["tables"] if name.startswith("ai_")]), 62)
        self.assertNotEqual(current["contentSha256"], before_budgets["contentSha256"])
        directory_tables = volume_tables-{"ai_business_volume_chunks"}
        directory_migrations = [m for m in migrations if int(m[1][:4]) <= 19]
        before_volumes = _ai_evidence(directory_tables, directory_migrations)
        self.assertEqual(len([name for name in before_volumes["tables"] if name.startswith("ai_")]), 61)
        self.assertEqual(before_volumes["contentSha256"], _ai_evidence(directory_tables, directory_migrations)["contentSha256"])
        self.assertNotEqual(current["contentSha256"], before_volumes["contentSha256"])
        before_directory = _ai_evidence(directory_tables-{"ai_business_evidence_sources"}, [m for m in migrations if int(m[1][:4]) <= 18])
        self.assertEqual(len([name for name in before_directory["tables"] if name.startswith("ai_")]), 60)
        previous = [item for item in migrations if int(item[1][:4]) <= 13]
        self.assertEqual(len([name for name in _ai_evidence(PRE_EVIDENCE_TABLES, previous)["tables"] if name.startswith("ai_")]), 56)
        for tables, history in [(set(AI_TABLES)-{"ai_business_evidence_chunks"}, migrations), (set(AI_TABLES)-{"ai_business_file_chunks"}, migrations), (AI_TABLES, previous), (AI_TABLES, [m for m in migrations if m[1] != "0013_dingtalk_schedule_media"]), (AI_TABLES, [m for m in migrations if m[1] != "0015_business_collection"])]:
            with self.assertRaises(RuntimeError):
                _ai_evidence(tables, history)
        for missing in ("0014_business_evidence", "0015_business_collection", "0016_business_files", "0017_business_file_renderer", "0018_business_excel_renderer", "0019_business_source_directory", "0020_business_volume_files", "0021_business_budget_plans"):
            with self.subTest(missing=missing), self.assertRaises(RuntimeError):
                _ai_evidence(AI_TABLES, [m for m in migrations if m[1] != missing])
        with self.assertRaises(RuntimeError):
            _ai_evidence(set(AI_TABLES)-{"ai_business_evidence_sources"}, migrations)
        with self.assertRaises(RuntimeError):
            _ai_evidence(directory_tables, migrations)
        with self.assertRaises(RuntimeError):
            _ai_evidence(AI_TABLES, directory_migrations)

    def test_evidence_accepts_both_backup_generations_with_stable_content_digest(self):
        legacy_tables = set(PRE_EVIDENCE_TABLES) - {"ai_library_revisions", "ai_execution_guidance", "ai_report_runs", "ai_report_deliveries", "ai_prompt_settings_revisions", "ai_conversation_workspaces", "ai_dingtalk_sessions", "ai_dingtalk_receipts", "ai_dingtalk_settings", "ai_dingtalk_schedules", "ai_dingtalk_schedule_runs"}
        legacy_migrations = [("ai_assistant", "0001_initial"), ("ai_assistant", "0005_postgres_image_payload")]
        legacy = _ai_evidence(legacy_tables, legacy_migrations)
        current = _ai_evidence(PRE_EVIDENCE_TABLES, [*legacy_migrations, ("ai_assistant", "0006_conversation_workspaces"), ("ai_assistant", "0007_dingtalk_readonly"), ("ai_assistant", "0008_dingtalk_settings"), ("ai_assistant", "0009_model_generation_capabilities"), ("ai_assistant", "0010_dingtalk_schedules"), ("ai_assistant", "0011_prompt_settings"), ("ai_assistant", "0012_report_library"), ("ai_assistant", "0013_dingtalk_schedule_media")])
        pre_settings = _ai_evidence(set(PRE_EVIDENCE_TABLES) - {"ai_library_revisions", "ai_execution_guidance", "ai_report_runs", "ai_report_deliveries", "ai_prompt_settings_revisions", "ai_dingtalk_settings", "ai_dingtalk_schedules", "ai_dingtalk_schedule_runs"}, [*legacy_migrations, ("ai_assistant", "0006_conversation_workspaces"), ("ai_assistant", "0007_dingtalk_readonly")])
        self.assertEqual(len([name for name in pre_settings["tables"] if name.startswith("ai_")]), 48)
        pre_dingtalk = _ai_evidence(set(PRE_EVIDENCE_TABLES) - {"ai_library_revisions", "ai_execution_guidance", "ai_report_runs", "ai_report_deliveries", "ai_prompt_settings_revisions", "ai_dingtalk_sessions", "ai_dingtalk_receipts", "ai_dingtalk_settings", "ai_dingtalk_schedules", "ai_dingtalk_schedule_runs"}, [*legacy_migrations, ("ai_assistant", "0006_conversation_workspaces")])
        self.assertEqual(len([name for name in pre_dingtalk["tables"] if name.startswith("ai_")]), 46)
        self.assertEqual(len([name for name in legacy["tables"] if name.startswith("ai_")]), 45)
        self.assertEqual(len([name for name in current["tables"] if name.startswith("ai_")]), 56)
        self.assertEqual(legacy["contentSha256"], _ai_evidence(legacy_tables, legacy_migrations)["contentSha256"])
        self.assertNotEqual(legacy["contentSha256"], current["contentSha256"])
        self.assertEqual(legacy["aiAssistant"], current["aiAssistant"])

    def test_evidence_rejects_inconsistent_ai_schema_and_migration_inventory(self):
        initial = ("ai_assistant", "0001_initial")
        workspace = ("ai_assistant", "0006_conversation_workspaces")
        legacy_tables = set(PRE_EVIDENCE_TABLES) - {"ai_library_revisions", "ai_execution_guidance", "ai_report_runs", "ai_report_deliveries", "ai_prompt_settings_revisions", "ai_conversation_workspaces", "ai_dingtalk_sessions", "ai_dingtalk_receipts", "ai_dingtalk_settings", "ai_dingtalk_schedules", "ai_dingtalk_schedule_runs"}
        for tables, migrations in [
            (legacy_tables, [initial, workspace]),
            (PRE_EVIDENCE_TABLES, [initial]),
            (PRE_EVIDENCE_TABLES, [workspace]),
            (PRE_EVIDENCE_TABLES, []),
            (set(), [initial]),
            (set(), [workspace]),
            (set(PRE_EVIDENCE_TABLES) | {"ai_unknown"}, [initial, workspace]),
            (legacy_tables | {"protected_business_spurious"},
                [initial, workspace]),
            (legacy_tables, [initial, workspace, (
                "ai_assistant", "0068_business_promotion_budget_v11_verifier_receipt")]),
        ]:
            with self.subTest(tables=len(tables), migrations=migrations):
                with self.assertRaisesRegex(RuntimeError, "AI .* (inventory|migration)"):
                    _ai_evidence(tables, migrations)

    def test_loopback_identity_normalizes_postgres_inet_cidr_text(self):
        self.assertEqual(
            MODULE._canonical_loopback_address("127.0.0.1/32"),
            "127.0.0.1",
        )
        self.assertEqual(MODULE._canonical_loopback_address("::1/128"), "::1")
        with self.assertRaisesRegex(RuntimeError, "not bound to a loopback"):
            MODULE._canonical_loopback_address("127.0.0.2/32")

    def test_backup_binds_dump_to_exported_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="teruisi-pg-helper-") as temporary:
            root = Path(temporary)
            pg_dump = root / "pg_dump.exe"
            pg_dump.write_bytes(b"fixture")
            output = root / "backup.dump"
            connection = _SnapshotConnection()
            captured_command = []

            def fake_run(command, **kwargs):
                captured_command.extend(command)
                output.write_bytes(b"valid-custom-archive-fixture")
                self.assertEqual(kwargs["timeout"], 77)
                self.assertEqual(kwargs["stdout"], subprocess.PIPE)
                self.assertEqual(kwargs["stderr"], subprocess.PIPE)
                return subprocess.CompletedProcess(command, 0, b"", b"")

            args = argparse.Namespace(
                pg_dump=str(pg_dump),
                output=str(output),
                expected_database="teruisi_sales",
                expected_user="teruisi_sales_owner",
                port=5432,
                timeout_seconds=77,
            )
            with (
                mock.patch.object(MODULE.psycopg, "connect", return_value=connection),
                mock.patch.object(
                    MODULE,
                    "collect_evidence",
                    return_value={"contentSha256": "a" * 64},
                ),
                mock.patch.object(MODULE, "_protected_ai_preflight",
                    return_value={"appliedProtectedMigrations": []}),
                mock.patch.object(MODULE, "_finance_raw_evidence_preflight",
                    return_value=False),
                mock.patch.object(MODULE, "_finance_raw_workbook_preflight",
                    return_value=False),
                mock.patch.object(MODULE.subprocess, "run", side_effect=fake_run),
            ):
                result = MODULE.run_backup(args)

            self.assertTrue(connection.committed)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["snapshotIdSha256"]), 64)
            self.assertIn("--snapshot=00000003-00000001-1", captured_command)
            self.assertIn("--format=custom", captured_command)
            self.assertIn("--no-owner", captured_command)
            self.assertIn("--no-privileges", captured_command)
            self.assertNotIn("password", " ".join(captured_command).lower())

    def test_failed_backup_removes_only_its_new_output(self):
        with tempfile.TemporaryDirectory(prefix="teruisi-pg-helper-") as temporary:
            root = Path(temporary)
            pg_dump = root / "pg_dump.exe"
            pg_dump.write_bytes(b"fixture")
            output = root / "backup.dump"
            connection = _SnapshotConnection()

            def fake_run(command, **kwargs):
                output.write_bytes(b"partial")
                return subprocess.CompletedProcess(command, 9, b"", b"failure")

            args = argparse.Namespace(
                pg_dump=str(pg_dump),
                output=str(output),
                expected_database="teruisi_sales",
                expected_user="teruisi_sales_owner",
                port=5432,
                timeout_seconds=77,
            )
            with (
                mock.patch.object(MODULE.psycopg, "connect", return_value=connection),
                mock.patch.object(MODULE, "collect_evidence", return_value={}),
                mock.patch.object(MODULE, "_protected_ai_preflight",
                    return_value={"appliedProtectedMigrations": []}),
                mock.patch.object(MODULE, "_finance_raw_evidence_preflight",
                    return_value=False),
                mock.patch.object(MODULE, "_finance_raw_workbook_preflight",
                    return_value=False),
                mock.patch.object(MODULE.subprocess, "run", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(RuntimeError, "pg_dump failed"):
                    MODULE.run_backup(args)
            self.assertFalse(output.exists())
            self.assertTrue(pg_dump.exists())

    def test_protected_backup_refuses_before_dump_or_evidence_read(self):
        with tempfile.TemporaryDirectory(prefix="teruisi-pg-helper-") as temporary:
            root = Path(temporary)
            pg_dump = root / "pg_dump.exe"
            pg_dump.write_bytes(b"fixture")
            args = argparse.Namespace(pg_dump=str(pg_dump),
                output=str(root / "protected.dump"),
                expected_database="teruisi_sales",
                expected_user="teruisi_sales_owner", port=5432,
                timeout_seconds=77)
            with (mock.patch.object(MODULE.psycopg, "connect",
                        return_value=_SnapshotConnection()),
                    mock.patch.object(MODULE, "_protected_ai_preflight",
                        return_value={"appliedProtectedMigrations": [
                            "0068_business_promotion_budget_v11_verifier_receipt"],
                            "status": "blocked"}),
                    mock.patch.object(MODULE, "collect_evidence") as evidence,
                    mock.patch.object(MODULE.subprocess, "run") as native):
                with self.assertRaisesRegex(RuntimeError, "not admitted"):
                    MODULE.run_backup(args)
            evidence.assert_not_called()
            native.assert_not_called()
            self.assertFalse((root / "protected.dump").exists())

    def test_finance_raw_evidence_receipt_or_table_refuses_backup_before_dump(self):
        class Cursor:
            def __init__(self, receipt, tables):
                self.receipt, self.tables = receipt, tables
            def execute(self, statement, params=None):
                self.statement = statement
            def fetchone(self):
                return (self.receipt,)
            def fetchall(self):
                return [(name,) for name in self.tables]
        self.assertFalse(MODULE._finance_raw_evidence_preflight(Cursor(False, [])))
        self.assertTrue(MODULE._finance_raw_evidence_preflight(Cursor(True,
            MODULE.FINANCE_RAW_EVIDENCE_TABLES)))
        for receipt, tables in ((True, []), (False, MODULE.FINANCE_RAW_EVIDENCE_TABLES),
                (True, ["finance_raw_column_evidence_cells"])):
            with self.subTest(receipt=receipt, tables=tables), \
                    self.assertRaisesRegex(RuntimeError, "finance raw evidence"):
                MODULE._finance_raw_evidence_preflight(Cursor(receipt, tables))
        with tempfile.TemporaryDirectory(prefix="teruisi-pg-helper-") as temporary:
            root = Path(temporary)
            pg_dump = root / "pg_dump.exe"
            pg_dump.write_bytes(b"fixture")
            args = argparse.Namespace(pg_dump=str(pg_dump),
                output=str(root / "raw-evidence.dump"),
                expected_database="teruisi_sales",
                expected_user="teruisi_sales_owner", port=5432,
                timeout_seconds=77)
            with (mock.patch.object(MODULE.psycopg, "connect",
                        return_value=_SnapshotConnection()),
                    mock.patch.object(MODULE, "_protected_ai_preflight",
                        return_value={"appliedProtectedMigrations": []}),
                    mock.patch.object(MODULE, "_finance_raw_evidence_preflight",
                        return_value=True),
                    mock.patch.object(MODULE, "_finance_raw_workbook_preflight",
                        return_value=False),
                    mock.patch.object(MODULE, "collect_evidence") as evidence,
                    mock.patch.object(MODULE.subprocess, "run") as native):
                with self.assertRaisesRegex(RuntimeError,
                        "finance raw evidence daily backup is not admitted"):
                    MODULE.run_backup(args)
            evidence.assert_not_called(); native.assert_not_called()
            self.assertFalse((root / "raw-evidence.dump").exists())

    def test_restore_is_single_transaction_and_bounded(self):
        with tempfile.TemporaryDirectory(prefix="teruisi-pg-helper-") as temporary:
            root = Path(temporary)
            pg_restore = root / "pg_restore.exe"
            archive = root / "approved.dump"
            pg_restore.write_bytes(b"fixture")
            archive.write_bytes(b"archive")
            captured_command = []

            def fake_run(command, **kwargs):
                captured_command.extend(command)
                self.assertEqual(kwargs["timeout"],
                    60 if "--list" in command else 91)
                return subprocess.CompletedProcess(command, 0, b"", b"")

            args = argparse.Namespace(
                pg_restore=str(pg_restore),
                archive=str(archive),
                expected_database="teruisi_sales",
                expected_user="postgres",
                port=55432,
                timeout_seconds=91,
            )
            with mock.patch.object(MODULE.subprocess, "run", side_effect=fake_run):
                result = MODULE.run_restore(args)
            self.assertEqual(result["status"], "completed")
            self.assertIn("--port=55432", captured_command)
            self.assertIn("--single-transaction", captured_command)
            self.assertIn("--no-owner", captured_command)
            self.assertIn("--no-privileges", captured_command)

    def test_protected_restore_refuses_before_running_restore(self):
        with tempfile.TemporaryDirectory(prefix="teruisi-pg-helper-") as temporary:
            root = Path(temporary)
            pg_restore = root / "pg_restore.exe"
            archive = root / "approved.dump"
            pg_restore.write_bytes(b"fixture")
            archive.write_bytes(b"archive")
            args = argparse.Namespace(pg_restore=str(pg_restore),
                archive=str(archive), expected_database="teruisi_sales",
                expected_user="postgres", port=55432, timeout_seconds=91)
            def fake_run(command, **kwargs):
                self.assertIn("--list", command)
                return subprocess.CompletedProcess(command, 0,
                    b"TABLE DATA public protected_business_budget_v11_verifier_keys", b"")
            with mock.patch.object(MODULE.subprocess, "run",
                    side_effect=fake_run) as native:
                with self.assertRaisesRegex(RuntimeError, "not admitted"):
                    MODULE.run_restore(args)
            self.assertEqual(native.call_count, 1)

    def test_finance_raw_evidence_toc_refuses_before_restore(self):
        with tempfile.TemporaryDirectory(prefix="teruisi-pg-helper-") as temporary:
            root = Path(temporary)
            pg_restore = root / "pg_restore.exe"
            archive = root / "approved.dump"
            pg_restore.write_bytes(b"fixture")
            archive.write_bytes(b"archive")
            args = argparse.Namespace(pg_restore=str(pg_restore),
                archive=str(archive), expected_database="teruisi_sales",
                expected_user="postgres", port=55432,
                timeout_seconds=91)
            def fake_run(command, **kwargs):
                self.assertIn("--list", command)
                return subprocess.CompletedProcess(command, 0,
                    b"TABLE DATA public finance_raw_column_evidence_cells", b"")
            with mock.patch.object(MODULE.subprocess, "run",
                    side_effect=fake_run) as native:
                with self.assertRaisesRegex(RuntimeError,
                        "finance raw evidence archive restore is not admitted"):
                    MODULE.run_restore(args)
            self.assertEqual(native.call_count, 1)

    def test_protected_preflight_checks_roles_owner_capability_and_policy_without_key_read(self):
        class Cursor:
            def __init__(self, *, missing_role=False, wrong_owner=False,
                         can_read=False):
                self.missing_role = missing_role
                self.wrong_owner = wrong_owner
                self.can_read = can_read
                self.queries = []
                self.current = ""

            def execute(self, query, params=None):
                self.current = query
                self.queries.append(query)

            def fetchall(self):
                if "django_migrations" in self.current:
                    return [("0068_business_promotion_budget_v11_verifier_receipt",)]
                if "FROM pg_catalog.pg_roles" in self.current:
                    names = MODULE.PROTECTED_AI_ROLES[:-1] if self.missing_role else MODULE.PROTECTED_AI_ROLES
                    return [(name, *(False,) * 7) for name in names]
                raise AssertionError("unexpected preflight rowset")

            def fetchone(self):
                if "pg_auth_members" in self.current:
                    return (0,)
                if "pg_catalog.pg_class" in self.current:
                    return ("wrong_owner" if self.wrong_owner else
                        "teruisi_ai_budget_v11_key_owner",)
                if "has_table_privilege" in self.current:
                    return (self.can_read,)
                if "rolcreaterole,rolsuper" in self.current:
                    return (False, False)
                raise AssertionError("unexpected preflight scalar")

        cursor = Cursor(missing_role=True, wrong_owner=True)
        result = MODULE._protected_ai_preflight(cursor)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("protected_roles_not_exact_nologin", result["issues"])
        self.assertIn("private_key_table_owner_unverified", result["issues"])
        self.assertIn("archive_acl_not_preserved", result["issues"])
        self.assertIn("cross_cluster_owner_acl_not_preserved", result["issues"])
        self.assertIn("private_key_archive_encryption_not_configured", result["issues"])
        self.assertTrue(result["readOnly"])
        self.assertFalse(any("secret" in query.lower() or "count(*) from public.protected" in query.lower()
            for query in cursor.queries))

        cursor = Cursor(can_read=False)
        result = MODULE._protected_ai_preflight(cursor)
        self.assertIn("backup_identity_cannot_read_private_key_table", result["issues"])
        self.assertEqual(result["exactProtectedRoleCount"], 15)

    def test_explicit_protected_preflight_uses_read_only_bound_identity(self):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        cursor = mock.MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = ("teruisi_sales", "teruisi_sales_owner",
            "127.0.0.1", 5432)
        connection.cursor.return_value = cursor
        args = argparse.Namespace(expected_database="teruisi_sales",
            expected_user="teruisi_sales_owner", port=5432)
        with (mock.patch.object(MODULE.psycopg, "connect",
                    return_value=connection),
                mock.patch.object(MODULE, "_protected_ai_preflight",
                    return_value={"status": "blocked", "issues": ["archive_acl_not_preserved"]})):
            self.assertEqual(MODULE.run_protected_preflight(args)["status"],
                "blocked")
        connection.execute.assert_called_once_with(
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.rollback.assert_called_once()

        cursor.fetchone.return_value = ("wrong_database", "teruisi_sales_owner",
            "127.0.0.1", 5432)
        with mock.patch.object(MODULE.psycopg, "connect",
                return_value=connection):
            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                MODULE.run_protected_preflight(args)

    def test_native_diagnostic_is_bounded_and_contains_no_output(self):
        completed = subprocess.CompletedProcess(
            ["fixture"], 4, b"x" * 20000, b"secret-text" * 2000
        )
        diagnostic = MODULE._safe_native_diagnostic(completed)
        self.assertEqual(diagnostic["exitCode"], 4)
        self.assertEqual(diagnostic["capturedBytes"], MODULE.MAX_NATIVE_DIAGNOSTIC_BYTES)
        self.assertTrue(diagnostic["outputTruncated"])
        self.assertEqual(set(diagnostic), {
            "exitCode", "outputBytes", "capturedBytes", "outputTruncated", "outputSha256"
        })


if __name__ == "__main__":
    unittest.main()
