#!/usr/bin/env python
"""Create and inspect a PostgreSQL backup with snapshot-bound evidence.

Credentials and connection coordinates are read only from libpq's PG*
environment variables.  The command prints one bounded JSON object and never
prints a connection string or native stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import psycopg
from psycopg import sql


VERSION = "teruisi-postgres-consistent-backup-v1"
ALLOWED_TABLE_PREFIXES = (
    "sales_", "erp_", "finance_", "netshop_", "market_", "product_",
    "inventory_", "replenishment_", "workflow_", "customer_service_", "bi_", "access_control_", "ai_",
)
PROTECTED_AI_TABLES_BY_MIGRATION = {
    "0068_business_promotion_budget_v11_verifier_receipt": {
        "protected_business_budget_v11_verifier_keys"},
    "0070_business_promotion_budget_v11_limited_identity": {
        "protected_business_budget_v11_proof_tickets",
        "protected_business_budget_v11_proof_ticket_claims"},
    "0071_business_v4_report_source_link": {
        "protected_business_v4_report_link_intents",
        "protected_business_v4_report_source_links"},
    "0072_business_market_v2_authority_proposals": {
        "protected_business_market_v2_rate_proposals",
        "protected_business_market_v2_cap_proposals",
        "protected_business_market_v2_authority_revocations"},
    "0073_business_promotion_budget_v11_login_attestation": {
        "protected_business_budget_v11_login_attestations"},
    "0074_business_market_v2_human_cap_approval": {
        "protected_business_market_v2_human_cap_approvals",
        "protected_business_market_v2_human_cap_revocations"},
    "0075_business_v4_report_restricted_page": set(),
    "0076_business_promotion_budget_v11_ticket_bound_signer": {
        "protected_business_budget_v11_signed_receipts_v3"},
    "0077_business_market_v6_paused_topology": {
        "protected_business_market_v6_topologies",
        "protected_business_market_v6_topology_cancellations"},
    "0078_business_promotion_budget_v11_signed_publication": {
        "protected_business_budget_v11_publications_v2"},
    "0079_business_market_v6_source_ticket": {
        "protected_business_market_v6_source_tickets"},
}
PROTECTED_AI_MIGRATIONS = frozenset(PROTECTED_AI_TABLES_BY_MIGRATION) | {
    "0067_business_promotion_budget_v11_attestation",
    "0069_business_market_v2_paid_round_rehearsal",
}
PROTECTED_AI_ROLES = (
    "teruisi_ai_budget_v11_attestor",
    "teruisi_ai_budget_v11_key_owner",
    "teruisi_ai_budget_v11_publisher",
    "teruisi_ai_market_paid_adopter",
    "teruisi_ai_market_paid_reserver",
    "teruisi_ai_market_paid_starter",
    "teruisi_ai_budget_v11_attest_login",
    "teruisi_ai_budget_v11_sign_login",
    "teruisi_ai_budget_v11_publish_login",
    "teruisi_ai_market_rate_proposer",
    "teruisi_ai_market_cap_proposer",
    "teruisi_ai_market_proposal_revoker",
    "teruisi_ai_budget_v11_attestor_v2_login",
    "teruisi_ai_market_v6_topology_login",
    "teruisi_ai_budget_v11_download_v2_login",
    "teruisi_ai_market_v6_source_login",
)
PROTECTED_KEY_TABLE = "public.protected_business_budget_v11_verifier_keys"
FINANCE_RAW_EVIDENCE_MIGRATION = "0005_raw_column_evidence_v2"
FINANCE_RAW_EVIDENCE_TABLES = frozenset({
    "finance_raw_column_evidence_months",
    "finance_raw_column_evidence_columns",
    "finance_raw_column_evidence_cells",
})
FINANCE_RAW_WORKBOOK_MIGRATION = "0006_raw_workbook_bytes_v2"
FINANCE_RAW_WORKBOOK_TABLES = frozenset({
    "finance_raw_workbook_attestations",
    "finance_raw_workbook_columns",
    "finance_raw_workbook_cells",
})
FORMAL_DUMP_FLAGS = ("--no-owner", "--no-privileges")
FORMAL_RESTORE_FLAGS = ("--no-owner", "--no-privileges")
MAX_NATIVE_DIAGNOSTIC_BYTES = 16 * 1024
FINANCE_MARKER_MIGRATION = "0003_finance_source_revision_guard"
FINANCE_MARKER_TABLE = "finance_source_revision_markers"
FINANCE_MARKER_TRIGGERS = {
    ("finance_lines", "finance_line_revision_required", "finance_source_mark_revision_required", 30, False, False),
    ("finance_months", "finance_month_revision_required", "finance_source_mark_revision_required", 30, False, False),
    ("finance_import_batches", "finance_batch_revision_required", "finance_source_mark_revision_required", 30, False, False),
    (FINANCE_MARKER_TABLE, "finance_source_revision_required", "finance_source_revision_required_at_commit", 5, True, True),
}
FINANCE_MARKER_FUNCTIONS = {"finance_source_mark_revision_required",
    "finance_source_revision_required_at_commit"}
FINANCE_MONOTONIC_MIGRATION = "0004_finance_revision_monotonic"
FINANCE_MONOTONIC_FUNCTION = "finance_revision_monotonic_guard"
FINANCE_MONOTONIC_TRIGGER = ("finance_data_revisions",
    "finance_revision_monotonic", FINANCE_MONOTONIC_FUNCTION, "O", 31, False, False)
NETSHOP_MARKER_MIGRATION = "0003_netshop_source_revision_guard"
NETSHOP_MARKER_TABLE = "netshop_source_revision_markers"
NETSHOP_MARKER_TRIGGERS = {
    ("netshop_rows", "netshop_row_revision_required", "netshop_source_mark_revision_required", "O", 30, False, False),
    ("netshop_import_batches", "netshop_batch_revision_required", "netshop_source_mark_revision_required", "O", 30, False, False),
    (NETSHOP_MARKER_TABLE, "netshop_source_revision_required", "netshop_source_revision_required_at_commit", "O", 5, True, True),
    ("netshop_data_revisions", "netshop_source_revision_monotonic", "netshop_source_revision_monotonic", "O", 31, False, False),
}
NETSHOP_MARKER_FUNCTIONS = {"netshop_source_mark_revision_required": True,
    "netshop_source_revision_required_at_commit": True,
    "netshop_source_revision_monotonic": False}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_native_diagnostic(completed: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    bounded = (stdout + b"\n" + stderr)[:MAX_NATIVE_DIAGNOSTIC_BYTES]
    return {
        "exitCode": int(completed.returncode),
        "outputBytes": int(len(stdout) + len(stderr)),
        "capturedBytes": int(len(bounded)),
        "outputTruncated": len(stdout) + len(stderr) > len(bounded),
        "outputSha256": _sha256_bytes(bounded),
    }


def _table_names(cursor: psycopg.Cursor[Any]) -> list[str]:
    cursor.execute(
        "SELECT tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname = 'public' ORDER BY tablename"
    )
    names = []
    for (name,) in cursor.fetchall():
        text = str(name)
        if (text == "django_migrations" or
                text.startswith(ALLOWED_TABLE_PREFIXES) or
                text.startswith("protected_business_")):
            names.append(text)
    return names


def _canonical_loopback_address(value: Any) -> str:
    address = str(value).split("/", 1)[0]
    if address not in ("127.0.0.1", "::1"):
        raise RuntimeError("database is not bound to a loopback address")
    return address


def _require_market_facet_indexes(cursor: psycopg.Cursor[Any]) -> None:
    """The adopted market.0005 indexes must survive an options upgrade/restore."""
    expected = {
        "mkt_facet_scope_idx": "scope",
        "mkt_facet_dimension_idx": "ranking_dimension",
        "mkt_facet_operation_idx": "operation_mode",
        "mkt_facet_subcategory_idx": "subcategory",
    }
    cursor.execute(
        "SELECT index_table.relname, indexed_column.attname, access_method.amname, "
        "index_info.indisvalid, index_info.indisready, index_info.indnatts, "
        "index_info.indnkeyatts, index_info.indpred IS NULL, index_info.indexprs IS NULL "
        "FROM pg_catalog.pg_index AS index_info "
        "JOIN pg_catalog.pg_class AS fact_table ON fact_table.oid = index_info.indrelid "
        "JOIN pg_catalog.pg_namespace AS fact_schema ON fact_schema.oid = fact_table.relnamespace "
        "JOIN pg_catalog.pg_class AS index_table ON index_table.oid = index_info.indexrelid "
        "JOIN pg_catalog.pg_am AS access_method ON access_method.oid = index_table.relam "
        "JOIN pg_catalog.pg_attribute AS indexed_column "
        "ON indexed_column.attrelid = fact_table.oid AND indexed_column.attnum = index_info.indkey[0] "
        "WHERE fact_schema.nspname = 'public' AND fact_table.relname = 'market_ranking_entries' "
        "AND index_table.relname = ANY(%s)",
        (list(expected),),
    )
    found = {
        str(name): (
            str(column), str(method), bool(valid), bool(ready),
            int(attributes), int(keys), bool(unfiltered), bool(uncomputed),
        )
        for name, column, method, valid, ready, attributes, keys, unfiltered, uncomputed in cursor.fetchall()
    }
    if found != {
        name: (column, "btree", True, True, 1, 1, True, True)
        for name, column in expected.items()
    }:
        raise RuntimeError("adopted market facet indexes are incomplete")


def _require_finance_source_marker_guard(cursor: psycopg.Cursor[Any],
                                         migrations: set[str], tables: set[str]) -> None:
    """Version-gated marker integrity in this backup's own MVCC snapshot."""
    migrated = FINANCE_MARKER_MIGRATION in migrations
    present = FINANCE_MARKER_TABLE in tables
    if not migrated:
        if present:
            raise RuntimeError("finance source marker table has no migration receipt")
        return
    if not present or "0002_finance_target_gross_margin" not in migrations:
        raise RuntimeError("finance source marker migration lacks table or predecessor")
    cursor.execute(
        "SELECT a.attname,a.attnotnull,pg_catalog.format_type(a.atttypid,a.atttypmod) "
        "FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname='finance_source_revision_markers' "
        "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"
    )
    if cursor.fetchall() != [
        ("transaction_id", True, "bigint"),
        ("baseline_revision", True, "bigint"),
        ("baseline_digest", True, "character varying(64)"),
    ]:
        raise RuntimeError("finance source marker columns are incomplete")
    cursor.execute(
        "SELECT pg_catalog.pg_get_constraintdef(con.oid) "
        "FROM pg_catalog.pg_constraint con "
        "JOIN pg_catalog.pg_class c ON c.oid=con.conrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname='finance_source_revision_markers' "
        "AND con.contype='p'"
    )
    if cursor.fetchall() != [("PRIMARY KEY (transaction_id)",)]:
        raise RuntimeError("finance source marker primary key is missing")
    cursor.execute(
        "SELECT c.relname,t.tgname,p.proname,t.tgtype,t.tgdeferrable,t.tginitdeferred "
        "FROM pg_catalog.pg_trigger t "
        "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
        "WHERE n.nspname='public' AND t.tgenabled='O' AND NOT t.tgisinternal "
        "AND t.tgname=ANY(%s)",
        ([item[1] for item in FINANCE_MARKER_TRIGGERS],),
    )
    if {(str(table),str(name),str(function),int(kind),bool(deferred),bool(initial))
            for table,name,function,kind,deferred,initial in cursor.fetchall()} != FINANCE_MARKER_TRIGGERS:
        raise RuntimeError("finance source revision triggers are missing or disabled")
    cursor.execute(
        "SELECT p.proname,p.prosecdef,p.proconfig "
        "FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public' AND pg_catalog.pg_get_function_identity_arguments(p.oid)='' "
        "AND p.proname=ANY(%s)", (list(FINANCE_MARKER_FUNCTIONS),),
    )
    functions = {str(name):(bool(definer), config) for name,definer,config in cursor.fetchall()}
    if (set(functions) != FINANCE_MARKER_FUNCTIONS or any(not definer or
            [str(item).replace(" ", "") for item in (config or [])
                if str(item).startswith("search_path=")] != ["search_path=pg_catalog,public"]
            for definer,config in functions.values())):
        raise RuntimeError("finance source revision SECURITY DEFINER search_path is invalid")
    privilege_checks = [
        (role, privilege) for role, privileges in (
            ("teruisi_finance_writer", ("INSERT","UPDATE","DELETE","TRUNCATE")),
            ("teruisi_finance_reader", ("SELECT","INSERT","UPDATE","DELETE","TRUNCATE")))
        for privilege in privileges
    ]
    clauses = ["pg_catalog.has_table_privilege(%s,%s,%s)" for _ in privilege_checks]
    values: list[str] = []
    for role, privilege in privilege_checks:
        values.extend((role,"public.finance_source_revision_markers",privilege))
    for role, privileges in (
            ("teruisi_finance_writer", ("INSERT", "UPDATE")),
            ("teruisi_finance_reader", ("SELECT", "INSERT", "UPDATE"))):
        for column in ("transaction_id", "baseline_revision", "baseline_digest"):
            for privilege in privileges:
                clauses.append("pg_catalog.has_column_privilege(%s,%s,%s,%s)")
                values.extend((role,"public.finance_source_revision_markers",
                    column,privilege))
    for role in ("teruisi_finance_writer","teruisi_finance_reader"):
        for function in sorted(FINANCE_MARKER_FUNCTIONS):
            clauses.append("pg_catalog.has_function_privilege(%s,%s,%s)")
            values.extend((role,f"public.{function}()","EXECUTE"))
    cursor.execute("SELECT "+",".join(clauses),values)
    if not all(value is False for value in cursor.fetchone()):
        raise RuntimeError("finance source marker grants expose protected state or functions")


def _require_finance_monotonic_guard(cursor: psycopg.Cursor[Any],
                                     migrations: set[str]) -> None:
    migrated = FINANCE_MONOTONIC_MIGRATION in migrations
    cursor.execute(
        "SELECT c.relname,t.tgname,p.proname,t.tgenabled,t.tgtype,"
        "t.tgdeferrable,t.tginitdeferred "
        "FROM pg_catalog.pg_trigger t "
        "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "AND t.tgname='finance_revision_monotonic'"
    )
    triggers = {(str(table),str(name),str(function),str(enabled),int(kind),
        bool(deferred),bool(initial)) for table,name,function,enabled,kind,deferred,initial
        in cursor.fetchall()}
    cursor.execute(
        "SELECT p.proname,p.prosecdef,p.proconfig "
        "FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public' AND pg_catalog.pg_get_function_identity_arguments(p.oid)='' "
        "AND p.proname='finance_revision_monotonic_guard'"
    )
    functions = {str(name):(bool(definer),config) for name,definer,config in cursor.fetchall()}
    if not migrated:
        if triggers or functions:
            raise RuntimeError("finance monotonic objects have no migration receipt")
        return
    if FINANCE_MARKER_MIGRATION not in migrations:
        raise RuntimeError("finance monotonic migration has no marker predecessor")
    if triggers != {FINANCE_MONOTONIC_TRIGGER} or set(functions) != {FINANCE_MONOTONIC_FUNCTION}:
        raise RuntimeError("finance revision monotonic trigger or function is missing")
    definer, config = functions[FINANCE_MONOTONIC_FUNCTION]
    if definer or [str(item).replace(" ", "") for item in (config or [])
            if str(item).startswith("search_path=")] != ["search_path=pg_catalog,public"]:
        raise RuntimeError("finance revision monotonic function shape is invalid")
    clauses = ["pg_catalog.has_table_privilege(%s,%s,%s)" for _ in range(3)]
    values = []
    for privilege in ("DELETE","TRUNCATE","TRIGGER"):
        values.extend(("teruisi_finance_writer","public.finance_data_revisions",privilege))
    clauses.append("pg_catalog.has_function_privilege(%s,%s,%s)")
    values.extend(("teruisi_finance_writer",
        "public.finance_revision_monotonic_guard()","EXECUTE"))
    cursor.execute("SELECT "+",".join(clauses),values)
    if not all(value is False for value in cursor.fetchone()):
        raise RuntimeError("finance writer can delete revision or alter its trigger boundary")
    cursor.execute(
        "SELECT pg_catalog.pg_has_role(%s,c.relowner,'MEMBER'),r.rolsuper "
        "FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_roles r ON r.rolname=%s "
        "WHERE n.nspname='public' AND c.relname='finance_data_revisions'",
        ("teruisi_finance_writer","teruisi_finance_writer"),
    )
    ownership = cursor.fetchone()
    if ownership is None or ownership != (False,False):
        raise RuntimeError("finance writer can disable the revision trigger")


def _require_netshop_source_marker_guard(cursor: psycopg.Cursor[Any],
                                         migrations: set[str], tables: set[str]) -> None:
    migrated = NETSHOP_MARKER_MIGRATION in migrations
    present = NETSHOP_MARKER_TABLE in tables
    if not migrated:
        if present:
            raise RuntimeError("netshop source marker table has no migration receipt")
        return
    if not present or "0002_migration_run_time_order" not in migrations:
        raise RuntimeError("netshop source marker migration lacks table or predecessor")
    cursor.execute(
        "SELECT a.attname,a.attnotnull,pg_catalog.format_type(a.atttypid,a.atttypmod) "
        "FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname='netshop_source_revision_markers' "
        "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"
    )
    if cursor.fetchall() != [("transaction_id",True,"bigint"),
            ("baseline_revision",True,"bigint"),
            ("baseline_digest",True,"character varying(64)")]:
        raise RuntimeError("netshop source marker columns are incomplete")
    cursor.execute(
        "SELECT pg_catalog.pg_get_constraintdef(con.oid) "
        "FROM pg_catalog.pg_constraint con "
        "JOIN pg_catalog.pg_class c ON c.oid=con.conrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname='netshop_source_revision_markers' "
        "AND con.contype='p'"
    )
    if cursor.fetchall() != [("PRIMARY KEY (transaction_id)",)]:
        raise RuntimeError("netshop source marker primary key is missing")
    cursor.execute(
        "SELECT c.relname,t.tgname,p.proname,t.tgenabled,t.tgtype,"
        "t.tgdeferrable,t.tginitdeferred "
        "FROM pg_catalog.pg_trigger t "
        "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "AND t.tgname=ANY(%s)",
        ([item[1] for item in NETSHOP_MARKER_TRIGGERS],),
    )
    found = {(str(table),str(name),str(function),str(enabled),int(kind),
        bool(deferred),bool(initial)) for table,name,function,enabled,kind,deferred,initial
        in cursor.fetchall()}
    if found != NETSHOP_MARKER_TRIGGERS:
        raise RuntimeError("netshop source revision triggers are missing or disabled")
    cursor.execute(
        "SELECT p.proname,p.prosecdef,p.proconfig "
        "FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public' AND pg_catalog.pg_get_function_identity_arguments(p.oid)='' "
        "AND p.proname=ANY(%s)", (list(NETSHOP_MARKER_FUNCTIONS),),
    )
    functions = {str(name):(bool(definer),config) for name,definer,config in cursor.fetchall()}
    if set(functions) != set(NETSHOP_MARKER_FUNCTIONS):
        raise RuntimeError("netshop source revision functions are missing")
    for name, required_definer in NETSHOP_MARKER_FUNCTIONS.items():
        definer, config = functions[name]
        if definer is not required_definer or [str(item).replace(" ", "")
                for item in (config or []) if str(item).startswith("search_path=")] != ["search_path=pg_catalog,public"]:
            raise RuntimeError("netshop source revision function shape is invalid")
    clauses, values = [], []
    for role in ("teruisi_netshop_writer","teruisi_netshop_reader"):
        for privilege in ("SELECT","INSERT","UPDATE","DELETE","TRUNCATE"):
            clauses.append("pg_catalog.has_table_privilege(%s,%s,%s)")
            values.extend((role,"public.netshop_source_revision_markers",privilege))
        for column in ("transaction_id","baseline_revision","baseline_digest"):
            for privilege in ("SELECT","INSERT","UPDATE"):
                clauses.append("pg_catalog.has_column_privilege(%s,%s,%s,%s)")
                values.extend((role,"public.netshop_source_revision_markers",column,privilege))
        for function in sorted(NETSHOP_MARKER_FUNCTIONS):
            clauses.append("pg_catalog.has_function_privilege(%s,%s,%s)")
            values.extend((role,f"public.{function}()","EXECUTE"))
    cursor.execute("SELECT "+",".join(clauses),values)
    if not all(value is False for value in cursor.fetchone()):
        raise RuntimeError("netshop marker grants expose protected state or functions")


def verify_market_v2_material_attestation(cursor) -> None:
    from importlib import import_module

    migration = import_module(
        "ai_assistant.migrations.0045_business_market_v2_material_attestation")
    role = "teruisi_ai_market_attestor"
    table = "public.ai_business_market_v2_materials"
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [role])
    if cursor.fetchone() != (False,) * 7:
        raise RuntimeError("AI market v2 material attestor role is not closed")
    cursor.execute("SELECT 1 FROM pg_catalog.pg_auth_members membership "
        "JOIN pg_catalog.pg_roles member ON member.oid=membership.member "
        "JOIN pg_catalog.pg_roles parent ON parent.oid=membership.roleid "
        "WHERE member.rolname=%s OR parent.rolname=%s", [role, role])
    if cursor.fetchone() is not None:
        raise RuntimeError("AI market v2 material attestor membership drift")
    cursor.execute("SELECT c.relkind,pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relname='ai_business_market_v2_materials'")
    table_state = cursor.fetchone()
    if (table_state is None or table_state[0] != "r"
            or table_state[1] in {role, "teruisi_ai_reader", "teruisi_ai_writer"}):
        raise RuntimeError("AI market v2 material table ownership drift")
    cursor.execute("SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),"
        "a.attnotnull FROM pg_catalog.pg_attribute a "
        "WHERE a.attrelid='public.ai_business_market_v2_materials'::regclass "
        "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum")
    expected_columns = [
        ("report_id", "character varying(160)", True),
        ("source_report_id", "character varying(160)", True),
        *[(name, "character varying(64)", True) for name in (
            "source_snapshot_digest", "source_workflow_input_digest",
            "selector_digest", "algorithms_digest", "manifest_digest",
            "manifest_json_sha256", "summary_digest")],
        ("table_spec_digests_json", "text", True),
        ("manifest_json", "text", True), ("summary_json", "text", True),
        ("created_at", "timestamp with time zone", True)]
    if cursor.fetchall() != expected_columns:
        raise RuntimeError("AI market v2 material table columns drift")
    cursor.execute("SELECT c.contype,pg_catalog.pg_get_constraintdef(c.oid) "
        "FROM pg_catalog.pg_constraint c WHERE c.conrelid="
        "'public.ai_business_market_v2_materials'::regclass")
    constraints = cursor.fetchall()
    if len(constraints) != 3 or set(constraints) != {
            ("p", "PRIMARY KEY (report_id)"),
            ("f", "FOREIGN KEY (report_id) REFERENCES ai_report_runs(id) ON DELETE RESTRICT"),
            ("f", "FOREIGN KEY (source_report_id) REFERENCES ai_report_runs(id) ON DELETE RESTRICT") }:
        raise RuntimeError("AI market v2 material table constraints drift")
    for checked_role in (role, "teruisi_ai_writer", "teruisi_ai_reader"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
                          "REFERENCES", "TRIGGER"):
            cursor.execute("SELECT pg_catalog.has_table_privilege(%s,%s,%s)",
                [checked_role, table, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("AI market v2 material table ACL drift")
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            cursor.execute("SELECT pg_catalog.has_any_column_privilege(%s,%s,%s)",
                [checked_role, table, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("AI market v2 material column ACL drift")
    cursor.execute("SELECT c.relname,t.tgname,t.tgtype,t.tgdeferrable,"
        "t.tginitdeferred,t.tgenabled,pn.nspname,p.proname,"
        "pg_catalog.pg_get_function_identity_arguments(p.oid) "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c "
        "ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
        "JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
        "WHERE n.nspname='public' AND c.relname='ai_business_market_v2_materials' "
        "AND NOT t.tgisinternal")
    triggers = cursor.fetchall()
    if len(triggers) != 2 or set(triggers) != {
            ("ai_business_market_v2_materials", "ai_market_v2_material_guard",
             31, False, False, "O", "public", "ai_market_v2_material_guard", ""),
            ("ai_business_market_v2_materials", "ai_market_v2_material_no_truncate",
             34, False, False, "O", "public", "ai_v4_seal_ticket_no_truncate", "") }:
        raise RuntimeError("AI market v2 material trigger drift")
    for signature, definition, definer, allowed_attestor in (
            ("public.ai_market_v2_material_guard()", migration.GUARD, False, False),
            ("public.ai_market_v2_attest_material(text,text,text,text,text,text,text,text)",
             migration.ATTEST, True, True),
            ("public.ai_v4_seal_ticket_no_truncate()", import_module(
                "ai_assistant.migrations.0041_business_v4_seal_ticket").NO_TRUNCATE,
             False, False)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        function = cursor.fetchone()
        if (function is None or function[0] != definition.split("$$")[1]
                or function[1] is not definer or function[3] != "plpgsql"
                or {item.replace(" ", "") for item in (function[2] or [])}
                != {"search_path=pg_catalog,public"}
                or function[4] in {role, "teruisi_ai_writer", "teruisi_ai_reader"}):
            raise RuntimeError("AI market v2 material function drift")
        cursor.execute("SELECT pg_catalog.has_function_privilege(%s,%s,'EXECUTE'),"
            "pg_catalog.has_function_privilege(%s,%s,'EXECUTE'),"
            "pg_catalog.has_function_privilege(%s,%s,'EXECUTE')",
            [role, signature, "teruisi_ai_writer", signature,
             "teruisi_ai_reader", signature])
        if cursor.fetchone() != (allowed_attestor, False, False):
            raise RuntimeError("AI market v2 material function ACL drift")


def verify_promotion_trial_file_guard(cursor, *, budget_stage_enabled=False,
                                      publish_gate_enabled=False,
                                      slim_stage_enabled=False) -> None:
    """Pin renderer-9, staged-10, or unpublished renderer-11 guard."""
    import importlib

    if publish_gate_enabled and not budget_stage_enabled:
        raise RuntimeError("budget publish gate has no staged predecessor")
    if slim_stage_enabled and not publish_gate_enabled:
        raise RuntimeError("budget slim stage has no publish-gate predecessor")
    migration = importlib.import_module(
        "ai_assistant.business_promotion_budget_v11_stage_sql"
        if slim_stage_enabled else
        "ai_assistant.migrations.0058_business_promotion_budget_v10_publish_gate"
        if publish_gate_enabled else
        "ai_assistant.migrations.0054_business_promotion_budget_file_staging"
        if budget_stage_enabled else
        "ai_assistant.migrations.0046_business_promotion_trial_file_guard")
    cursor.execute("SELECT c.convalidated,pg_catalog.pg_get_constraintdef(c.oid) "
        "FROM pg_catalog.pg_constraint c WHERE c.conrelid="
        "'public.ai_business_file_runs'::regclass "
        "AND c.conname='ai_business_file_bound' AND c.contype='c'")
    row = cursor.fetchone()
    versions = (re.search(r"renderer_version\s*=\s*ANY\s*\(ARRAY\[([0-9,\s]+)\]\)",
                          row[1]) if row and row[0] else None)
    expected_versions = ((1, 2, 3, 4, 5, 6, 7, 9, 10, 11) if slim_stage_enabled
                         else (1, 2, 3, 4, 5, 6, 7, 9, 10) if budget_stage_enabled
                         else (1, 2, 3, 4, 5, 6, 7, 9))
    if (versions is None or row[1].count("renderer_version") != 1
            or re.search(r"\bOR\b", row[1], re.IGNORECASE)
            or tuple(int(part.strip()) for part in versions.group(1).split(","))
                != expected_versions):
        raise RuntimeError("AI promotion trial file version constraint drift")

    signatures = (
        "public.ai_business_file_chunk_guard()",
        "public.ai_business_volume_chunk_guard()",
        "public.ai_business_volume_manifest_check(text,integer,text,boolean,text)",
        "public.ai_business_files_guard()",
        "public.ai_business_volume_complete_guard()",
        "public.ai_business_promotion_trial_parent_requirements(text,text,text)",
        "public.ai_business_promotion_trial_ready_requirements(text)",
    ) + (("public.ai_business_promotion_budget_parent_requirements(text,text,text)",)
         if budget_stage_enabled else ())
    publish = (importlib.import_module(
        "ai_assistant.migrations.0058_business_promotion_budget_v10_publish_gate")
        if publish_gate_enabled else None)
    trial = (publish.stage.previous if publish_gate_enabled else
             migration.previous if budget_stage_enabled else migration)
    definitions = (*migration.NEW_SQL, trial.PARENT_REQUIREMENTS,
                   trial.READY_REQUIREMENTS) + (((publish.stage if publish_gate_enabled
                   else migration).BUDGET_PARENT_REQUIREMENTS,)
                   if budget_stage_enabled else ())
    for index, (signature, definition) in enumerate(zip(signatures, definitions)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "pg_catalog.pg_get_userbyid(p.proowner),p.oid "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
            "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)", [signature])
        function = cursor.fetchone()
        if (function is None or function[0] != definition.split("$$")[1]
                or function[1] is not (publish_gate_enabled and index == 4)
                or function[3] != "plpgsql"
                or {item.replace(" ", "") for item in (function[2] or [])}
                != {"search_path=pg_catalog,public"}
                or function[4] in {"teruisi_ai_writer", "teruisi_ai_reader"}):
            raise RuntimeError("AI promotion trial file function drift")
        cursor.execute("SELECT CASE WHEN acl.grantee=p.proowner THEN 'OWNER' "
            "WHEN acl.grantee=0 THEN 'PUBLIC' ELSE grantee.rolname END,"
            "acl.privilege_type,acl.is_grantable FROM pg_catalog.pg_proc p "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) acl "
            "LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        acl = set(cursor.fetchall())
        expected_acl = {("OWNER", "EXECUTE", False)}
        expected_acl.add(("PUBLIC", "EXECUTE", False) if index < 5 else
                         ("teruisi_ai_writer", "EXECUTE", False))
        if acl != expected_acl:
            raise RuntimeError("AI promotion trial file function ACL drift: " + signature)

    if slim_stage_enabled:
        migration.verify_catalog(cursor)

    cursor.execute("SELECT c.relname,t.tgname,t.tgtype,t.tgdeferrable,"
        "t.tginitdeferred,t.tgenabled,t.tgfoid "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "AND t.tgname IN ('ai_business_file_chunk_state',"
        "'ai_business_volume_chunk_state','ai_business_volume_initial',"
        "'ai_business_file_state','ai_business_volume_complete')")
    expected_triggers = {
        ("ai_business_file_chunks", "ai_business_file_chunk_state"):
            (7, False, False, signatures[0]),
        ("ai_business_volume_chunks", "ai_business_volume_chunk_state"):
            (7, False, False, signatures[1]),
        ("ai_business_file_runs", "ai_business_volume_initial"):
            (7, False, False, signatures[3]),
        ("ai_business_file_runs", "ai_business_file_state"):
            (27, False, False, signatures[3]),
        ("ai_business_file_runs", "ai_business_volume_complete"):
            (21, True, True, signatures[4]),
        ("ai_business_volume_chunks", "ai_business_volume_complete"):
            (5, True, True, signatures[4]),
    }
    triggers = cursor.fetchall()
    if len(triggers) != len(expected_triggers):
        raise RuntimeError("AI promotion trial file trigger drift")
    for table, name, kind, deferred, initially_deferred, enabled, function_oid in triggers:
        expected = expected_triggers.get((table, name))
        if expected is None or (kind, deferred, initially_deferred) != expected[:3] or enabled != "O":
            raise RuntimeError("AI promotion trial file trigger drift")
        cursor.execute("SELECT to_regprocedure(%s)::oid", [expected[3]])
        if cursor.fetchone() != (function_oid,):
            raise RuntimeError("AI promotion trial file trigger OID drift")


def verify_market_v2_parked_guards(cursor, *, execution_profile_installed=False,
                                   synthetic_profile_installed=False) -> None:
    from importlib import import_module

    profile = import_module(
        "ai_assistant.migrations.0044_business_market_v2_profile")
    execution = (import_module(
        "ai_assistant.migrations.0060_business_market_v2_execution_snapshot")
        if execution_profile_installed else None)
    synthetic = (import_module(
        "ai_assistant.migrations.0064_business_market_v2_synthetic_vertical")
        if synthetic_profile_installed else None)
    expected = {
        ("ai_report_runs", "ai_market_v2_report_guard"):
            (31, False, False, "ai_market_v2_parked_report_guard",
             execution.NEW_PARKED_REPORT if execution else profile.REPORT_GUARD),
        ("ai_workflow_runs", "ai_market_v2_workflow_guard"):
            (31, False, False, "ai_market_v2_parked_workflow_guard",
             synthetic.NEW_PARKED_WORKFLOW if synthetic else
             execution.NEW_PARKED_WORKFLOW if execution else profile.WORKFLOW_GUARD),
        ("ai_workflow_runs", "ai_market_v2_workflow_complete"):
            (5, True, True, "ai_market_v2_parked_orphan_guard", profile.ORPHAN_GUARD),
        ("ai_agent_jobs", "ai_market_v2_job_guard"):
            (7, False, False, "ai_market_v2_parked_job_guard", profile.JOB_GUARD),
    }
    cursor.execute("SELECT c.relname,t.tgname,t.tgtype,t.tgdeferrable,"
        "t.tginitdeferred,t.tgenabled,p.proname,pn.nspname,"
        "pg_catalog.pg_get_function_identity_arguments(p.oid),"
        "p.prosrc,p.proconfig,p.prosecdef,l.lanname "
        "FROM pg_catalog.pg_trigger t "
        "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace "
        "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
        "JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE cn.nspname='public' AND NOT t.tgisinternal "
        "AND t.tgname IN ('ai_market_v2_report_guard',"
        "'ai_market_v2_workflow_guard','ai_market_v2_workflow_complete',"
        "'ai_market_v2_job_guard')")
    rows = cursor.fetchall()
    if len(rows) != len(expected):
        raise RuntimeError("AI market v2 parked profile guards missing")
    for (table, name, kind, deferred, initially_deferred, enabled,
         function, namespace, arguments, body, config, security_definer,
         language) in rows:
        guard = expected.get((table, name))
        if (guard is None or (kind, deferred, initially_deferred, function)
                != guard[:4] or enabled != "O" or namespace != "public"
                or arguments != "" or body != guard[4].split("$$")[1]
                or {item.replace(" ", "") for item in (config or [])}
                != {"search_path=pg_catalog,public"}
                or security_definer is not False or language != "plpgsql"):
            raise RuntimeError("AI market v2 parked profile guard drift")


def collect_evidence(
    connection: psycopg.Connection[Any],
    expected_database: str,
    expected_user: str,
) -> dict[str, Any]:
    """Collect exact, deterministic evidence from the current transaction."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_database(), current_user, "
            "COALESCE(inet_server_addr()::text, ''), inet_server_port(), "
            "pg_is_in_recovery(), current_setting('server_version_num')::integer"
        )
        identity = cursor.fetchone()
        if identity is None:
            raise RuntimeError("database identity probe returned no row")
        database_name, database_user, server_address, server_port, recovery, version = identity
        if str(database_name) != expected_database or str(database_user) != expected_user:
            raise RuntimeError("database identity does not match the approved target")
        canonical_server_address = _canonical_loopback_address(server_address)

        tables = _table_names(cursor)
        if "django_migrations" not in tables:
            raise RuntimeError("required database tables are missing")
        cursor.execute("SELECT app, name FROM django_migrations ORDER BY app, name")
        migrations = [
            {"app": str(app), "name": str(name)}
            for app, name in cursor.fetchall()
        ]
        if not migrations:
            raise RuntimeError("django migration evidence is empty")
        applied_ai = {item["name"] for item in migrations
                      if item["app"] == "ai_assistant"}
        expected_protected = set().union(*(
            names for migration, names in
            PROTECTED_AI_TABLES_BY_MIGRATION.items()
            if migration in applied_ai))
        present_protected = {name for name in tables
                             if name.startswith("protected_business_")}
        if present_protected != expected_protected:
            raise RuntimeError("AI protected table inventory does not match migrations")
        required = {
            "django_migrations",
            "sales_data_revisions",
            "sales_import_batches",
            "sales_order_lines",
            "sales_write_authority",
            "erp_product_master",
        }
        sales_options_tables = {"sales_analysis_options", "sales_analysis_options_state"}
        sales_migrations = {item["name"] for item in migrations if item["app"] == "sales"}
        if "0010_analysis_options" in sales_migrations:
            if "0009_postgres_raw_upload_payload" not in sales_migrations:
                raise RuntimeError("ERP options migration dependency is missing")
            required.update(sales_options_tables)
        elif set(tables) & sales_options_tables:
            raise RuntimeError("ERP options tables lack their migration receipt")
        erp_reference_required = {
            "erp_product_master", "erp_combo_items",
            "erp_reference_import_batches_pg", "erp_reference_import_scope_heads",
            "erp_reference_import_fingerprints", "erp_reference_import_attempts",
            "erp_reference_write_authority", "erp_reference_write_request_receipts",
            "erp_reference_migration_runs", "erp_reference_raw_upload_sessions",
            "erp_reference_raw_upload_chunks",
        }
        erp_reference_tables = {
            name for name in tables if name.startswith("erp_reference_")
        }
        if "erp_reference_write_authority" in erp_reference_tables:
            required.update(erp_reference_required)
        netshop_required = {
            "netshop_data_revisions",
            "netshop_import_batches",
            "netshop_rows",
            "netshop_write_authority",
        }
        netshop_tables = {name for name in tables if name.startswith("netshop_")}
        if netshop_tables:
            required.update(netshop_required)
        netshop_migrations = {item["name"] for item in migrations if item["app"] == "netshop"}
        _require_netshop_source_marker_guard(cursor, netshop_migrations, set(tables))
        if NETSHOP_MARKER_MIGRATION in netshop_migrations:
            required.add(NETSHOP_MARKER_TABLE)
        market_required = {
            "market_data_revisions",
            "market_import_batches",
            "market_ranking_entries",
            "market_write_authority",
        }
        market_tables = {name for name in tables if name.startswith("market_")}
        if market_tables:
            required.update(market_required)
        market_options_tables = {"market_analysis_options", "market_analysis_options_state"}
        market_migrations = {item["name"] for item in migrations if item["app"] == "market"}
        if "0005_filter_facet_indexes" in market_migrations:
            if "0004_projection_sync_fencing" not in market_migrations:
                raise RuntimeError("Market facet migration dependency is missing")
            _require_market_facet_indexes(cursor)
        if "0006_analysis_options" in market_migrations:
            if "0005_filter_facet_indexes" not in market_migrations:
                raise RuntimeError("Market options migration dependency is missing")
            required.update(market_options_tables)
        elif market_tables & market_options_tables:
            raise RuntimeError("Market options tables lack their migration receipt")
        finance_migrations = {item["name"] for item in migrations if item["app"] == "finance"}
        _require_finance_source_marker_guard(cursor, finance_migrations, set(tables))
        _require_finance_monotonic_guard(cursor, finance_migrations)
        if FINANCE_MARKER_MIGRATION in finance_migrations:
            required.add(FINANCE_MARKER_TABLE)
        products_required = {
            "product_data_revisions",
            "product_shipping_rate_import_batches",
            "product_shipping_rates",
            "product_inventory_projection",
            "product_write_authority",
        }
        products_tables = {name for name in tables if name.startswith("product_")}
        if products_tables:
            required.update(products_required)
        inventory_required = {
            "inventory_data_revisions",
            "inventory_import_batches",
            "inventory_stock_lines",
            "inventory_age_lines",
            "inventory_write_authority",
            "inventory_operating_settings",
            "replenishment_plan_items",
            "inventory_replenishment_group_deliveries",
            "inventory_guangdong_monitor_items",
            "inventory_guangdong_supplier_cycles",
            "inventory_guangdong_monitor_audits",
        }
        inventory_tables = {
            name for name in tables
            if name.startswith("inventory_") or name.startswith("replenishment_")
        }
        if inventory_tables:
            required.update(inventory_required)
        workflow_required = {
            "workflow_data_revisions",
            "workflow_write_authority",
            "workflow_operations_write_authority",
            "workflow_new_product_projects",
            "workflow_new_product_targets",
            "workflow_new_product_stages",
            "workflow_new_product_activities",
            "workflow_new_product_lines",
            "workflow_new_product_line_codes",
            "workflow_new_product_weekly_report_config",
            "workflow_new_product_weekly_deliveries",
            "workflow_tasks",
            "workflow_task_comments",
            "workflow_task_activity_logs",
            "workflow_task_reminders",
            "workflow_task_templates",
            "workflow_task_entity_links",
            "workflow_task_attachments",
            "workflow_attachment_cleanup_queue",
            "workflow_operation_records",
            "workflow_operation_activities",
        }
        workflow_tables = {name for name in tables if name.startswith("workflow_")}
        if workflow_tables:
            required.update(workflow_required)
        customer_service_required = {
            "customer_service_data_revisions",
            "customer_service_import_batches",
            "customer_service_conversations",
            "customer_service_deletion_audits",
            "customer_service_import_scope_heads",
            "customer_service_import_fingerprints",
            "customer_service_import_attempts",
            "customer_service_write_authority",
            "customer_service_write_request_receipts",
            "customer_service_migration_runs",
            "customer_service_raw_upload_sessions",
            "customer_service_raw_upload_chunks",
        }
        customer_service_tables = {
            name for name in tables if name.startswith("customer_service_")
        }
        if customer_service_tables:
            required.update(customer_service_required)
        bi_tables = {name for name in tables if name.startswith("bi_")}
        if bi_tables:
            required.add("bi_migration_runs")
        access_control_tables = {name for name in tables if name.startswith("access_control_")}
        if access_control_tables:
            required.update({
                "access_control_users", "access_control_roles", "access_control_permission_audits",
                "access_control_data_revisions", "access_control_write_authority",
                "access_control_write_request_receipts", "access_control_migration_runs",
            })
        ai_tables = {name for name in tables if name.startswith("ai_")}
        ai_migrations = {
            item["name"] for item in migrations if item["app"] == "ai_assistant"
        }
        if ai_tables or ai_migrations:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
            from ai_assistant.table_manifest import AI_TABLES
            if "0001_initial" not in ai_migrations:
                raise RuntimeError("AI table inventory has no initial migration evidence")
            if "0044_business_market_v2_profile" in ai_migrations:
                if "0043_business_v4_seal_consumption_candidate" not in ai_migrations:
                    raise RuntimeError("AI market v2 parked profile has no predecessor")
                verify_market_v2_parked_guards(cursor,
                    execution_profile_installed=
                        "0060_business_market_v2_execution_snapshot" in ai_migrations,
                    synthetic_profile_installed=
                        "0064_business_market_v2_synthetic_vertical" in ai_migrations)
            if "0045_business_market_v2_material_attestation" in ai_migrations:
                if "0044_business_market_v2_profile" not in ai_migrations:
                    raise RuntimeError("AI market v2 material has no parked predecessor")
                verify_market_v2_material_attestation(cursor)
            if "0046_business_promotion_trial_file_guard" in ai_migrations:
                if ("0045_business_market_v2_material_attestation" not in ai_migrations
                        or "0029_business_promotion_file_ready" not in ai_migrations):
                    raise RuntimeError("AI promotion trial file guard has no predecessor")
                verify_promotion_trial_file_guard(cursor,
                    budget_stage_enabled="0054_business_promotion_budget_file_staging" in ai_migrations,
                    publish_gate_enabled="0058_business_promotion_budget_v10_publish_gate" in ai_migrations,
                    slim_stage_enabled="0066_business_promotion_budget_v11_durable_stage" in ai_migrations)
                if "0057_business_promotion_budget_v10_attestation" in ai_migrations:
                    if ("0056_business_market_v2_material_role_bridge" not in ai_migrations
                            or "0054_business_promotion_budget_file_staging" not in ai_migrations):
                        raise RuntimeError("AI budget v10 attestation predecessor missing")
                    from importlib import import_module
                    attestation_guard = import_module(
                        "ai_assistant.migrations.0059_business_promotion_budget_v10_reader_fence"
                        if "0059_business_promotion_budget_v10_reader_fence" in ai_migrations
                        else "ai_assistant.migrations.0058_business_promotion_budget_v10_publish_gate"
                        if "0058_business_promotion_budget_v10_publish_gate" in ai_migrations
                        else "ai_assistant.migrations.0057_business_promotion_budget_v10_attestation")
                    attestation_guard.verify_catalog(cursor)
                    if ("0059_business_promotion_budget_v10_reader_fence" in ai_migrations
                            and "0058_business_promotion_budget_v10_publish_gate" not in ai_migrations):
                        raise RuntimeError("AI budget v10 reader fence lacks publish predecessor")
                elif "0058_business_promotion_budget_v10_publish_gate" in ai_migrations:
                    raise RuntimeError("AI budget v10 publish gate lacks attestation predecessor")
            if "0047_business_v4_sealer_replay_progress" in ai_migrations:
                if ("0046_business_promotion_trial_file_guard" not in ai_migrations
                        or "0043_business_v4_seal_consumption_candidate" not in ai_migrations):
                    raise RuntimeError("AI v4 replay progress has no claimed ticket predecessor")
                from ai_assistant.v4_replay_progress_catalog import verify
                verify(cursor, RuntimeError,
                    finance_enabled="0048_business_v4_finance_replay_progress" in ai_migrations,
                    read_cast_enabled="0050_business_v4_replay_read_cast" in ai_migrations,
                    prior_claim_qualified="0051_business_v4_prior_claim_column" in ai_migrations)
            if ("0048_business_v4_finance_replay_progress" in ai_migrations
                    and "0047_business_v4_sealer_replay_progress" not in ai_migrations):
                raise RuntimeError("AI v4 finance replay has no promotion replay predecessor")
            if "0049_business_v4_sealer_source_bridge" in ai_migrations:
                if ("0048_business_v4_finance_replay_progress" not in ai_migrations
                        or "0042_business_v4_claimed_read" not in ai_migrations):
                    raise RuntimeError("AI v4 sealer source bridge has no claimed-reader predecessor")
                from ai_assistant.v4_sealer_source_catalog import verify
                verify(cursor, RuntimeError)
            if ("0050_business_v4_replay_read_cast" in ai_migrations
                    and "0049_business_v4_sealer_source_bridge" not in ai_migrations):
                raise RuntimeError("AI v4 replay read cast has no source bridge predecessor")
            if ("0051_business_v4_prior_claim_column" in ai_migrations
                    and "0050_business_v4_replay_read_cast" not in ai_migrations):
                raise RuntimeError("AI v4 qualified prior claim has no read cast predecessor")
            if "0052_business_v4_commit_consumption" in ai_migrations:
                if ("0051_business_v4_prior_claim_column" not in ai_migrations
                        or "0043_business_v4_seal_consumption_candidate" not in ai_migrations):
                    raise RuntimeError("AI v4 commit consumption has no claimed replay predecessor")
                from ai_assistant.v4_commit_consumption_catalog import verify
                verify(cursor, RuntimeError)
            if "0053_business_market_v2_admitted_paused" in ai_migrations:
                if ("0052_business_v4_commit_consumption" not in ai_migrations
                        or "0045_business_market_v2_material_attestation" not in ai_migrations):
                    raise RuntimeError("AI market v2 admitted snapshot has no parked/material predecessor")
                from ai_assistant.market_v2_admitted_catalog import verify
                verify(cursor, RuntimeError)
            if "0061_business_market_v2_context_proof" in ai_migrations:
                if "0060_business_market_v2_execution_snapshot" not in ai_migrations:
                    raise RuntimeError("AI market context proof has no execution profile")
                from ai_assistant.business_market_v2_context_catalog import verify
                verify(cursor, RuntimeError)
            if "0062_business_market_v2_read_receipt_candidate" in ai_migrations:
                if "0061_business_market_v2_context_proof" not in ai_migrations:
                    raise RuntimeError("AI market read receipt has no context proof")
                from ai_assistant.business_market_v2_read_catalog import verify
                verify(cursor, RuntimeError)
            if "0063_business_market_v2_execution_plan" in ai_migrations:
                if "0062_business_market_v2_read_receipt_candidate" not in ai_migrations:
                    raise RuntimeError("AI market execution plan lacks read-receipt predecessor")
                from ai_assistant.business_market_v2_execution_plan_catalog import verify
                verify(cursor, RuntimeError)
            if "0064_business_market_v2_synthetic_vertical" in ai_migrations:
                if "0063_business_market_v2_execution_plan" not in ai_migrations:
                    raise RuntimeError("AI market synthetic chain lacks execution plan")
                from ai_assistant.business_market_v2_synthetic_catalog import verify
                verify(cursor, RuntimeError)
            if "0065_business_market_v2_model_cost_reservation" in ai_migrations:
                if "0064_business_market_v2_synthetic_vertical" not in ai_migrations:
                    raise RuntimeError("AI market cost candidate lacks synthetic predecessor")
                from ai_assistant.business_market_v2_cost_catalog import verify
                verify(cursor, RuntimeError)
            if ("0066_business_promotion_budget_v11_durable_stage" in ai_migrations
                    and "0065_business_market_v2_model_cost_reservation" not in ai_migrations):
                raise RuntimeError("AI budget v11 stage lacks market cost predecessor")
            if "0067_business_promotion_budget_v11_attestation" in ai_migrations:
                if "0066_business_promotion_budget_v11_durable_stage" not in ai_migrations:
                    raise RuntimeError("AI budget v11 attestation lacks staged predecessor")
                from importlib import import_module
                import_module(
                    "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation"
                ).verify_catalog(cursor)
            for migration_name, predecessor in (
                    ("0068_business_promotion_budget_v11_verifier_receipt",
                     "0067_business_promotion_budget_v11_attestation"),
                    ("0069_business_market_v2_paid_round_rehearsal",
                     "0068_business_promotion_budget_v11_verifier_receipt"),
                    ("0070_business_promotion_budget_v11_limited_identity",
                     "0069_business_market_v2_paid_round_rehearsal"),
                    ("0071_business_v4_report_source_link",
                     "0070_business_promotion_budget_v11_limited_identity"),
                    ("0072_business_market_v2_authority_proposals",
                     "0071_business_v4_report_source_link"),
                    ("0073_business_promotion_budget_v11_login_attestation",
                     "0072_business_market_v2_authority_proposals"),
                    ("0074_business_market_v2_human_cap_approval",
                     "0073_business_promotion_budget_v11_login_attestation"),
                    ("0075_business_v4_report_restricted_page",
                     "0074_business_market_v2_human_cap_approval"),
                    ("0076_business_promotion_budget_v11_ticket_bound_signer",
                     "0075_business_v4_report_restricted_page"),
                    ("0077_business_market_v6_paused_topology",
                     "0076_business_promotion_budget_v11_ticket_bound_signer"),
                    ("0078_business_promotion_budget_v11_signed_publication",
                     "0077_business_market_v6_paused_topology"),
                    ("0079_business_market_v6_source_ticket",
                     "0078_business_promotion_budget_v11_signed_publication")):
                if migration_name in ai_migrations:
                    if predecessor not in ai_migrations:
                        raise RuntimeError("AI protected sidecar lacks predecessor: "
                            + migration_name)
                    from importlib import import_module
                    import_module("ai_assistant.migrations." + migration_name
                        ).verify_catalog(cursor)
            if ("0054_business_promotion_budget_file_staging" in ai_migrations
                    and ("0053_business_market_v2_admitted_paused" not in ai_migrations
                         or "0046_business_promotion_trial_file_guard" not in ai_migrations)):
                raise RuntimeError("AI promotion budget staging has no market/trial predecessors")
            if "0055_business_v4_period_plan_candidate" in ai_migrations:
                if "0054_business_promotion_budget_file_staging" not in ai_migrations:
                    raise RuntimeError("AI v4 period plan candidate has no file predecessor")
                from ai_assistant.v4_period_plan_catalog import verify
                verify(cursor, RuntimeError)
            # Restore probes run with today's helper against the backup's schema.
            # Use migrations from this same transaction, never the deployed schema,
            # to retain the approved pre-workspace (45 table) backup contract.
            expected_ai_tables = set(AI_TABLES)
            if "0069_business_market_v2_paid_round_rehearsal" not in ai_migrations:
                expected_ai_tables.difference_update({
                    "ai_business_market_v2_paid_authorities",
                    "ai_business_market_v2_round_reservations",
                    "ai_business_market_v2_round_events",
                })
            if "0065_business_market_v2_model_cost_reservation" not in ai_migrations:
                expected_ai_tables.discard("ai_business_market_v2_cost_ledger_candidates")
            if "0063_business_market_v2_execution_plan" not in ai_migrations:
                expected_ai_tables.discard("ai_business_market_v2_execution_plans")
            if "0062_business_market_v2_read_receipt_candidate" not in ai_migrations:
                expected_ai_tables.discard("ai_business_market_v2_read_receipts")
            if "0061_business_market_v2_context_proof" not in ai_migrations:
                expected_ai_tables.discard("ai_business_market_v2_context_proofs")
            if "0057_business_promotion_budget_v10_attestation" not in ai_migrations:
                expected_ai_tables.discard("ai_business_promotion_budget_v10_attestations")
            if "0067_business_promotion_budget_v11_attestation" not in ai_migrations:
                expected_ai_tables.discard("ai_business_promotion_budget_v11_attestations")
            if "0055_business_v4_period_plan_candidate" not in ai_migrations:
                expected_ai_tables.discard("ai_business_v4_period_plan_candidates")
            if "0047_business_v4_sealer_replay_progress" not in ai_migrations:
                expected_ai_tables.discard("ai_business_v4_sealer_replay_progress")
            if "0045_business_market_v2_material_attestation" not in ai_migrations:
                expected_ai_tables.discard("ai_business_market_v2_materials")
            if "0043_business_v4_seal_consumption_candidate" not in ai_migrations:
                expected_ai_tables.discard("ai_business_v4_seal_consumptions")
            elif "0042_business_v4_claimed_read" not in ai_migrations:
                raise RuntimeError("AI v4 consumption has no claimed-reader predecessor")
            ticket_tables = {"ai_business_v4_seal_tickets",
                             "ai_business_v4_seal_claims"}
            if "0041_business_v4_seal_ticket" not in ai_migrations:
                expected_ai_tables.difference_update(ticket_tables)
            elif "0040_business_v4_sealer_narrow_stream" not in ai_migrations:
                raise RuntimeError("AI v4 seal ticket has no narrow-reader predecessor")
            if ("0042_business_v4_claimed_read" in ai_migrations
                    and "0041_business_v4_seal_ticket" not in ai_migrations):
                raise RuntimeError("AI v4 claimed reader has no ticket predecessor")
            if "0038_business_v4_seal_writer_gate" not in ai_migrations:
                expected_ai_tables.discard("ai_business_v4_seals")
            elif "0037_business_v4_seal_admission_read" not in ai_migrations:
                raise RuntimeError("AI v4 seal schema has no read-lock admission predecessor")
            validation_tables = {"ai_business_v4_validation_attempts",
                "ai_business_v4_validation_segments"}
            if "0036_business_v4_validation_segments" not in ai_migrations:
                expected_ai_tables.difference_update(validation_tables)
            elif "0035_business_v4_ledger" not in ai_migrations:
                raise RuntimeError("AI v4 validation schema has no physical ledger predecessor")
            v4_tables = {"ai_business_v4_runs", "ai_business_v4_sources",
                         "ai_business_v4_chunks", "ai_business_v4_tool_receipts"}
            if "0035_business_v4_ledger" not in ai_migrations:
                expected_ai_tables.difference_update(v4_tables)
            elif "0034_business_v3_report_intent" not in ai_migrations:
                raise RuntimeError("AI v4 ledger schema has no paused v3 intent predecessor")
            if "0034_business_v3_report_intent" not in ai_migrations:
                expected_ai_tables.discard("ai_business_v3_report_intents")
            elif "0033_business_v3_parent_seal" not in ai_migrations:
                raise RuntimeError("AI v3 paused intent schema has no sealed parent predecessor")
            if "0032_business_source_tool_receipts" not in ai_migrations:
                expected_ai_tables.discard("ai_business_source_tool_receipts")
            elif "0031_business_daily_v3_source_pages" not in ai_migrations:
                raise RuntimeError("AI tool receipt schema has no v3 daily source predecessor")
            if "0024_business_screening_runtime" in ai_migrations and "0023_business_screening_storage" not in ai_migrations:
                raise RuntimeError("AI screening runtime schema has no screening storage predecessor")
            if "0023_business_screening_storage" not in ai_migrations:
                expected_ai_tables.difference_update({"ai_business_screening_runs", "ai_business_screening_pages"})
            elif "0022_business_integrated_reports" not in ai_migrations or "0021_business_budget_plans" not in ai_migrations:
                raise RuntimeError("AI screening storage schema has no integrated report predecessor")
            if "0021_business_budget_plans" not in ai_migrations:
                expected_ai_tables.discard("ai_business_budget_plans")
            elif "0020_business_volume_files" not in ai_migrations:
                raise RuntimeError("AI fixed budget schema has no volume predecessor")
            if "0020_business_volume_files" not in ai_migrations:
                expected_ai_tables.discard("ai_business_volume_chunks")
            elif "0019_business_source_directory" not in ai_migrations:
                raise RuntimeError("AI volume files schema has no source directory predecessor")
            if "0019_business_source_directory" not in ai_migrations:
                expected_ai_tables.discard("ai_business_evidence_sources")
            elif not {"0014_business_evidence", "0015_business_collection", "0016_business_files",
                      "0017_business_file_renderer", "0018_business_excel_renderer"} <= ai_migrations:
                raise RuntimeError("AI source directory schema has incomplete evidence/file predecessors")
            if "0014_business_evidence" not in ai_migrations:
                expected_ai_tables.difference_update({"ai_business_evidence_runs", "ai_business_evidence_chunks"})
            elif "0013_dingtalk_schedule_media" not in ai_migrations:
                raise RuntimeError("AI business evidence schema has no media predecessor")
            if "0016_business_files" not in ai_migrations:
                expected_ai_tables.difference_update({"ai_business_file_runs", "ai_business_file_chunks"})
            elif not {"0014_business_evidence", "0015_business_collection"} <= set(ai_migrations):
                raise RuntimeError("AI business files schema has no collection predecessor")
            if "0012_report_library" in ai_migrations and "0011_prompt_settings" not in ai_migrations:
                raise RuntimeError("AI report schema has no prompt predecessor")
            if "0012_report_library" not in ai_migrations:
                expected_ai_tables.difference_update({"ai_library_revisions", "ai_execution_guidance", "ai_report_runs", "ai_report_deliveries"})
            if "0011_prompt_settings" in ai_migrations and "0010_dingtalk_schedules" not in ai_migrations:
                raise RuntimeError("AI prompt settings schema has no schedule predecessor")
            if "0011_prompt_settings" not in ai_migrations:
                expected_ai_tables.remove("ai_prompt_settings_revisions")
            if "0010_dingtalk_schedules" in ai_migrations and "0009_model_generation_capabilities" not in ai_migrations:
                raise RuntimeError("DingTalk schedule schema has no AI model predecessor")
            if "0010_dingtalk_schedules" not in ai_migrations:
                expected_ai_tables.difference_update({"ai_dingtalk_schedules", "ai_dingtalk_schedule_runs"})
            if "0008_dingtalk_settings" in ai_migrations and "0007_dingtalk_readonly" not in ai_migrations:
                raise RuntimeError("DingTalk settings schema has no channel predecessor")
            if "0008_dingtalk_settings" not in ai_migrations:
                expected_ai_tables.remove("ai_dingtalk_settings")
            if "0007_dingtalk_readonly" in ai_migrations and "0006_conversation_workspaces" not in ai_migrations:
                raise RuntimeError("DingTalk schema has no workspace predecessor")
            if "0007_dingtalk_readonly" not in ai_migrations:
                expected_ai_tables.difference_update({"ai_dingtalk_sessions", "ai_dingtalk_receipts"})
            if "0006_conversation_workspaces" not in ai_migrations:
                expected_ai_tables.remove("ai_conversation_workspaces")
            if ai_tables != expected_ai_tables:
                raise RuntimeError("AI closed table inventory is incomplete or contains unknown tables")
            if "0041_business_v4_seal_ticket" in ai_migrations:
                cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
                    "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
                    "WHERE rolname='teruisi_ai_seal_writer'")
                if cursor.fetchone() != (False,) * 7:
                    raise RuntimeError("AI v4 seal ticket role is not default closed")
                for table in ticket_tables:
                    cursor.execute("SELECT count(*) FROM pg_catalog.pg_trigger t "
                        "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
                        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='public' AND c.relname=%s "
                        "AND t.tgname IN ('ai_v4_ticket_immutable',"
                        "'ai_v4_ticket_no_truncate') AND t.tgenabled='O'",
                        [table])
                    if cursor.fetchone()[0] != 2:
                        raise RuntimeError("AI v4 seal ticket immutable triggers missing")
                    for role in ("teruisi_ai_reader", "teruisi_ai_writer",
                                 "teruisi_ai_seal_writer"):
                        cursor.execute("SELECT has_any_column_privilege(%s,%s,'SELECT'),"
                            "has_any_column_privilege(%s,%s,'INSERT'),"
                            "has_any_column_privilege(%s,%s,'UPDATE'),"
                            "has_table_privilege(%s,%s,'DELETE'),"
                            "has_table_privilege(%s,%s,'TRUNCATE')",
                            [role, "public." + table] * 5)
                        if any(cursor.fetchone()):
                            raise RuntimeError("AI v4 seal ticket table ACL drift")
                for signature, sealer, writer in (
                    ("public.ai_v4_issue_seal_ticket(text,text,text,bigint,bigint,text,text)",
                     False, True),
                    ("public.ai_v4_claim_seal_ticket(text,text,text,bigint,text)",
                     True, False),
                    ("public.ai_v4_sealer_read_context(text,text,text,bigint)",
                     False, False),
                    ("public.ai_v4_sealer_read_segment(text,text,text,integer,text,bigint)",
                     False, False),
                    ("public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)",
                     False, False),
                    ("public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)",
                     False, False),
                    ("public.ai_v4_lock_source_revisions_for_admission()",
                     False, True),
                ):
                    cursor.execute("SELECT to_regprocedure(%s)", [signature])
                    if cursor.fetchone()[0] is None:
                        raise RuntimeError("AI v4 seal ticket function missing")
                    cursor.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',%s,'EXECUTE'),"
                        "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
                        "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
                        [signature] * 3)
                    if cursor.fetchone() != (sealer, writer, False):
                        raise RuntimeError("AI v4 seal ticket function ACL drift")
            if "0042_business_v4_claimed_read" in ai_migrations:
                for signature, sealer in (
                    ("public.ai_v4_sealer_assert_claim(text,text,text,bigint,text,text)",
                     False),
                    ("public.ai_v4_sealer_ticket_context(text,text,text,bigint,text,text)",
                     True),
                    ("public.ai_v4_sealer_ticket_segment(text,text,text,integer,text,bigint,text,text)",
                     True),
                    ("public.ai_v4_sealer_ticket_page(text,text,text,bigint,text,bigint,text,text)",
                     True),
                ):
                    cursor.execute("SELECT to_regprocedure(%s)", [signature])
                    if cursor.fetchone()[0] is None:
                        raise RuntimeError("AI v4 claimed reader function missing")
                    cursor.execute("SELECT has_function_privilege("
                        "'teruisi_ai_seal_writer',%s,'EXECUTE'),"
                        "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
                        "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
                        [signature] * 3)
                    if cursor.fetchone() != (sealer, False, False):
                        raise RuntimeError("AI v4 claimed reader function ACL drift")
            if "0043_business_v4_seal_consumption_candidate" in ai_migrations:
                cursor.execute("SELECT count(*) FROM pg_catalog.pg_trigger t "
                    "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relname="
                    "'ai_business_v4_seal_consumptions' "
                    "AND t.tgname IN ('ai_v4_consumption_state',"
                    "'ai_v4_ticket_immutable','ai_v4_ticket_no_truncate') "
                    "AND t.tgenabled='O'")
                if cursor.fetchone()[0] != 3:
                    raise RuntimeError("AI v4 consumption immutable triggers missing")
                cursor.execute("SELECT t.tgtype,t.tgdeferrable,t.tginitdeferred,"
                    "t.tgenabled,t.tgfoid='public.ai_v4_seal_requires_consumption()'"
                    "::regprocedure,p.prosecdef,p.proconfig,p.prosrc,"
                    "p.proowner='teruisi_ai_seal_writer'::regrole "
                    "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p "
                    "ON p.oid=t.tgfoid WHERE t.tgrelid="
                    "'public.ai_business_v4_seals'::regclass AND "
                    "t.tgname='ai_v4_seal_consumption_required'")
                required_seal = cursor.fetchone()
                from importlib import import_module
                consumption_migration = import_module(
                    "ai_assistant.migrations.0043_business_v4_seal_consumption_candidate"
                )
                expected_seal = consumption_migration.REQUIRE_CONSUMPTION.split("$$")[1]
                if (required_seal is None or required_seal[:6] !=
                        (5, True, True, "O", True, True)
                        or {value.replace(" ", "") for value in (required_seal[6] or [])}
                        != {"search_path=pg_catalog,public"}
                        or required_seal[7] != expected_seal
                        or required_seal[8] is not False):
                    raise RuntimeError("AI v4 seal consumption commit fence missing")
                for role in ("teruisi_ai_reader", "teruisi_ai_writer",
                             "teruisi_ai_seal_writer"):
                    cursor.execute("SELECT has_any_column_privilege(%s,%s,'SELECT'),"
                        "has_any_column_privilege(%s,%s,'INSERT'),"
                        "has_any_column_privilege(%s,%s,'UPDATE'),"
                        "has_table_privilege(%s,%s,'DELETE'),"
                        "has_table_privilege(%s,%s,'TRUNCATE')",
                        [role, "public.ai_business_v4_seal_consumptions"] * 5)
                    if any(cursor.fetchone()):
                        raise RuntimeError("AI v4 consumption table ACL drift")
                signature = "public.ai_v4_sealer_consumption_result(text,text,text,text,text)"
                cursor.execute("SELECT to_regprocedure(%s)", [signature])
                if cursor.fetchone()[0] is None:
                    raise RuntimeError("AI v4 consumption result function missing")
                cursor.execute("SELECT has_function_privilege("
                    "'teruisi_ai_seal_writer',%s,'EXECUTE'),"
                    "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
                    "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
                    [signature] * 3)
                if cursor.fetchone() != (True, False, False):
                    raise RuntimeError("AI v4 consumption result ACL drift")
                signature = "public.ai_v4_verify_seal_consumption(text,text,bigint,text)"
                cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
                    "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
                    "WHERE p.oid=to_regprocedure(%s)", [signature])
                verifier = cursor.fetchone()
                if (verifier is None or verifier[0] !=
                        consumption_migration.VERIFY_CONSUMPTION.split("$$")[1]
                        or verifier[1] is not True
                        or {item.replace(" ", "") for item in (verifier[2] or [])}
                        != {"search_path=pg_catalog,public"}
                        or verifier[3] in {"teruisi_ai_reader", "teruisi_ai_writer",
                                           "teruisi_ai_seal_writer"}):
                    raise RuntimeError("AI v4 consumption verifier missing")
                cursor.execute("SELECT has_function_privilege("
                    "'teruisi_ai_seal_writer',%s,'EXECUTE'),"
                    "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
                    "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
                    [signature] * 3)
                if cursor.fetchone() != (False, True, False):
                    raise RuntimeError("AI v4 consumption verifier ACL drift")
            required.update(expected_ai_tables)
        missing = sorted(required.difference(tables))
        if missing:
            raise RuntimeError("required database tables are missing")

        row_counts: dict[str, int] = {}
        for table in tables:
            cursor.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(
                    sql.Identifier("public", table)
                )
            )
            row_counts[table] = int(cursor.fetchone()[0])

        cursor.execute(
            "SELECT domain, revision FROM sales_data_revisions ORDER BY domain"
        )
        revisions = {
            str(domain): int(revision) for domain, revision in cursor.fetchall()
        }
        if "sales" not in revisions or "erp" not in revisions:
            raise RuntimeError("sales/erp revision evidence is incomplete")

        cursor.execute(
            "SELECT status, authority_epoch::text, cutover_id "
            "FROM sales_write_authority WHERE id = 1"
        )
        authority = cursor.fetchone()
        if authority is None:
            raise RuntimeError("sales write authority singleton is missing")
        authority_status, authority_epoch, cutover_id = authority
        if str(authority_status) != "active" or not str(authority_epoch) or not str(cutover_id):
            raise RuntimeError("sales write authority is not active")

        erp_reference_authority: dict[str, str] | None = None
        if "erp_reference_write_authority" in erp_reference_tables:
            cursor.execute(
                "SELECT status,COALESCE(authority_epoch::text,''),cutover_id,"
                "COALESCE(migration_verify_run_id,'') "
                "FROM erp_reference_write_authority WHERE id=1"
            )
            erp_authority = cursor.fetchone()
            if erp_authority is None:
                raise RuntimeError("ERP reference write authority singleton is missing")
            erp_status, erp_epoch, erp_cutover, erp_run = map(str, erp_authority)
            if erp_status == "postgres":
                if (
                    re.fullmatch(r"[0-9a-fA-F-]{36}", erp_epoch) is None
                    or re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", erp_cutover) is None
                    or re.fullmatch(r"erp-reference-[0-9a-f]{32}", erp_run) is None
                ):
                    raise RuntimeError("active ERP reference authority evidence is incomplete")
            elif erp_status != "d1" or erp_epoch or erp_cutover:
                raise RuntimeError("ERP reference authority evidence is invalid")
            erp_reference_authority = {
                "status": erp_status, "authorityEpoch": erp_epoch,
                "cutoverId": erp_cutover, "migrationRunId": erp_run,
            }

        netshop_revisions: dict[str, dict[str, Any]] | None = None
        netshop_authority: dict[str, str] | None = None
        if netshop_tables:
            cursor.execute(
                "SELECT domain, revision, source_digest "
                "FROM netshop_data_revisions ORDER BY domain"
            )
            netshop_revisions = {
                str(domain): {
                    "revision": int(revision),
                    "sourceDigest": str(source_digest),
                }
                for domain, revision, source_digest in cursor.fetchall()
            }
            revision = netshop_revisions.get("netshop")
            if (
                revision is None
                or int(revision["revision"]) < 0
                or re.fullmatch(r"[0-9a-f]{64}", str(revision["sourceDigest"])) is None
            ):
                raise RuntimeError("netshop revision evidence is incomplete")
            cursor.execute(
                "SELECT status, COALESCE(authority_epoch::text, ''), cutover_id, "
                "migration_verify_run_id FROM netshop_write_authority WHERE id = 1"
            )
            netshop_authority_row = cursor.fetchone()
            if netshop_authority_row is None:
                raise RuntimeError("netshop write authority singleton is missing")
            netshop_status, netshop_epoch, netshop_cutover, netshop_run = (
                str(value or "") for value in netshop_authority_row
            )
            if netshop_status not in {"d1", "postgres"}:
                raise RuntimeError("netshop write authority status is invalid")
            if netshop_run and re.fullmatch(r"netshop-[0-9a-f]{24}", netshop_run) is None:
                raise RuntimeError("netshop migration run evidence is invalid")
            if netshop_status == "postgres":
                if (
                    re.fullmatch(r"[0-9a-fA-F-]{36}", netshop_epoch) is None
                    or re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", netshop_cutover) is None
                    or not netshop_run
                ):
                    raise RuntimeError("active netshop write authority evidence is incomplete")
            elif netshop_epoch or netshop_cutover:
                raise RuntimeError("inactive netshop write authority contains activation evidence")
            netshop_authority = {
                "status": netshop_status,
                "authorityEpoch": netshop_epoch,
                "cutoverId": netshop_cutover,
                "migrationRunId": netshop_run,
            }

        market_revisions: dict[str, dict[str, Any]] | None = None
        market_authority: dict[str, str] | None = None
        if market_tables:
            cursor.execute(
                "SELECT domain, revision, source_digest "
                "FROM market_data_revisions ORDER BY domain"
            )
            market_revisions = {
                str(domain): {
                    "revision": int(revision),
                    "sourceDigest": str(source_digest),
                }
                for domain, revision, source_digest in cursor.fetchall()
            }
            revision = market_revisions.get("market")
            if (
                revision is None
                or int(revision["revision"]) < 1
                or re.fullmatch(r"[0-9a-f]{64}", str(revision["sourceDigest"])) is None
            ):
                raise RuntimeError("market revision evidence is incomplete")
            cursor.execute(
                "SELECT status, COALESCE(authority_epoch::text, ''), cutover_id, "
                "migration_verify_run_id FROM market_write_authority WHERE id = 1"
            )
            market_authority_row = cursor.fetchone()
            if market_authority_row is None:
                raise RuntimeError("market write authority singleton is missing")
            market_status, market_epoch, market_cutover, market_run = (
                str(value or "") for value in market_authority_row
            )
            if market_status not in {"d1", "postgres"}:
                raise RuntimeError("market write authority status is invalid")
            if market_run and re.fullmatch(r"market-[0-9a-f]{24}", market_run) is None:
                raise RuntimeError("market migration run evidence is invalid")
            if market_status == "postgres":
                if (
                    re.fullmatch(r"[0-9a-fA-F-]{36}", market_epoch) is None
                    or re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", market_cutover) is None
                    or not market_run
                ):
                    raise RuntimeError("active market write authority evidence is incomplete")
            elif market_epoch or market_cutover:
                raise RuntimeError("inactive market write authority contains activation evidence")
            market_authority = {
                "status": market_status,
                "authorityEpoch": market_epoch,
                "cutoverId": market_cutover,
                "migrationRunId": market_run,
            }

        products_revisions: dict[str, dict[str, Any]] | None = None
        products_authority: dict[str, str] | None = None
        if products_tables:
            cursor.execute(
                "SELECT domain, revision, source_digest "
                "FROM product_data_revisions ORDER BY domain"
            )
            products_revisions = {
                str(domain): {
                    "revision": int(revision),
                    "sourceDigest": str(source_digest),
                }
                for domain, revision, source_digest in cursor.fetchall()
            }
            revision = products_revisions.get("products")
            if (
                revision is None
                or int(revision["revision"]) < 0
                or (
                    int(revision["revision"]) > 0
                    and re.fullmatch(r"[0-9a-f]{64}", str(revision["sourceDigest"])) is None
                )
            ):
                raise RuntimeError("products revision evidence is incomplete")
            cursor.execute(
                "SELECT status, COALESCE(authority_epoch::text, ''), cutover_id, "
                "migration_verify_run_id FROM product_write_authority WHERE id = 1"
            )
            products_authority_row = cursor.fetchone()
            if products_authority_row is None:
                raise RuntimeError("products write authority singleton is missing")
            products_status, products_epoch, products_cutover, products_run = (
                str(value or "") for value in products_authority_row
            )
            if products_status not in {"d1", "postgres"}:
                raise RuntimeError("products write authority status is invalid")
            if products_run and re.fullmatch(r"products-apply-[0-9a-f]{32}", products_run) is None:
                raise RuntimeError("products migration run evidence is invalid")
            if products_status == "postgres":
                if (
                    int(revision["revision"]) < 1
                    or re.fullmatch(r"[0-9a-fA-F-]{36}", products_epoch) is None
                    or re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", products_cutover) is None
                    or not products_run
                ):
                    raise RuntimeError("active products write authority evidence is incomplete")
            elif products_epoch or products_cutover:
                raise RuntimeError("inactive products write authority contains activation evidence")
            products_authority = {
                "status": products_status,
                "authorityEpoch": products_epoch,
                "cutoverId": products_cutover,
                "migrationRunId": products_run,
            }

        inventory_revisions: dict[str, dict[str, Any]] | None = None
        inventory_authority: dict[str, str] | None = None
        if inventory_tables:
            cursor.execute(
                "SELECT domain, revision, source_digest "
                "FROM inventory_data_revisions ORDER BY domain"
            )
            inventory_revisions = {
                str(domain): {
                    "revision": int(revision),
                    "sourceDigest": str(source_digest),
                }
                for domain, revision, source_digest in cursor.fetchall()
            }
            revision = inventory_revisions.get("inventory")
            if (
                revision is None
                or int(revision["revision"]) < 0
                or (
                    int(revision["revision"]) > 0
                    and re.fullmatch(r"[0-9a-f]{64}", str(revision["sourceDigest"])) is None
                )
            ):
                raise RuntimeError("inventory revision evidence is incomplete")
            cursor.execute(
                "SELECT status, COALESCE(authority_epoch::text, ''), cutover_id, "
                "migration_verify_run_id FROM inventory_write_authority WHERE id = 1"
            )
            inventory_authority_row = cursor.fetchone()
            if inventory_authority_row is None:
                raise RuntimeError("inventory write authority singleton is missing")
            inventory_status, inventory_epoch, inventory_cutover, inventory_run = (
                str(value or "") for value in inventory_authority_row
            )
            if inventory_status not in {"d1", "postgres"}:
                raise RuntimeError("inventory write authority status is invalid")
            if inventory_run and re.fullmatch(r"inventory-apply-[0-9a-f]{32}", inventory_run) is None:
                raise RuntimeError("inventory migration run evidence is invalid")
            if inventory_status == "postgres":
                if (
                    int(revision["revision"]) < 1
                    or re.fullmatch(r"[0-9a-fA-F-]{36}", inventory_epoch) is None
                    or re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", inventory_cutover) is None
                    or not inventory_run
                ):
                    raise RuntimeError("active inventory write authority evidence is incomplete")
            elif inventory_epoch or inventory_cutover:
                raise RuntimeError("inactive inventory write authority contains activation evidence")
            inventory_authority = {
                "status": inventory_status,
                "authorityEpoch": inventory_epoch,
                "cutoverId": inventory_cutover,
                "migrationRunId": inventory_run,
            }

        workflow_revisions: dict[str, dict[str, Any]] | None = None
        workflow_authority: dict[str, str] | None = None
        workflow_operations_authority: dict[str, str] | None = None
        if workflow_tables:
            cursor.execute(
                "SELECT domain, revision, source_digest "
                "FROM workflow_data_revisions ORDER BY domain"
            )
            workflow_revisions = {
                str(domain): {
                    "revision": int(revision),
                    "sourceDigest": str(source_digest),
                }
                for domain, revision, source_digest in cursor.fetchall()
            }
            revision = workflow_revisions.get("workflow")
            if (
                revision is None
                or int(revision["revision"]) < 0
                or (
                    int(revision["revision"]) > 0
                    and re.fullmatch(r"[0-9a-f]{64}", str(revision["sourceDigest"])) is None
                )
            ):
                raise RuntimeError("workflow revision evidence is incomplete")
            cursor.execute(
                "SELECT status, COALESCE(authority_epoch::text, ''), cutover_id, "
                "migration_verify_run_id FROM workflow_write_authority WHERE id=1"
            )
            authority_row = cursor.fetchone()
            if authority_row is None:
                raise RuntimeError("workflow write authority singleton is missing")
            workflow_status, workflow_epoch, workflow_cutover, workflow_run = (
                str(value or "") for value in authority_row
            )
            if workflow_status not in {"disabled", "postgres"}:
                raise RuntimeError("workflow write authority status is invalid")
            if workflow_run and re.fullmatch(r"workflow-[0-9a-f]{32}", workflow_run) is None:
                raise RuntimeError("workflow migration run evidence is invalid")
            if workflow_status == "postgres":
                if (
                    int(revision["revision"]) < 1
                    or re.fullmatch(r"[0-9a-fA-F-]{36}", workflow_epoch) is None
                    or re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", workflow_cutover) is None
                    or not workflow_run
                ):
                    raise RuntimeError("active workflow write authority evidence is incomplete")
            elif workflow_epoch or workflow_cutover:
                raise RuntimeError("inactive workflow write authority contains activation evidence")
            workflow_authority = {
                "status": workflow_status,
                "authorityEpoch": workflow_epoch,
                "cutoverId": workflow_cutover,
                "migrationRunId": workflow_run,
            }
            cursor.execute(
                "SELECT status, COALESCE(authority_epoch::text, ''), cutover_id, "
                "migration_verify_run_id FROM workflow_operations_write_authority WHERE id=1"
            )
            operations_authority_row = cursor.fetchone()
            if operations_authority_row is None:
                raise RuntimeError("workflow operations write authority singleton is missing")
            operations_status, operations_epoch, operations_cutover, operations_run = (
                str(value or "") for value in operations_authority_row
            )
            if operations_status not in {"disabled", "postgres"}:
                raise RuntimeError("workflow operations write authority status is invalid")
            if operations_run and re.fullmatch(r"workflow-ops-[0-9a-f]{32}", operations_run) is None:
                raise RuntimeError("workflow operations migration run evidence is invalid")
            if operations_status == "postgres":
                if (
                    int(revision["revision"]) < 1
                    or re.fullmatch(r"[0-9a-fA-F-]{36}", operations_epoch) is None
                    or re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", operations_cutover) is None
                    or not operations_run
                ):
                    raise RuntimeError("active workflow operations authority evidence is incomplete")
            elif operations_epoch or operations_cutover:
                raise RuntimeError("inactive workflow operations authority contains activation evidence")
            workflow_operations_authority = {
                "status": operations_status,
                "authorityEpoch": operations_epoch,
                "cutoverId": operations_cutover,
                "migrationRunId": operations_run,
            }

        customer_service_revisions: dict[str, dict[str, Any]] | None = None
        customer_service_authority: dict[str, str] | None = None
        if customer_service_tables:
            cursor.execute(
                "SELECT domain, revision, source_digest "
                "FROM customer_service_data_revisions ORDER BY domain"
            )
            customer_service_revisions = {
                str(domain): {
                    "revision": int(revision),
                    "sourceDigest": str(source_digest),
                }
                for domain, revision, source_digest in cursor.fetchall()
            }
            revision = customer_service_revisions.get("customer-service")
            if (
                revision is None
                or int(revision["revision"]) < 0
                or (
                    int(revision["revision"]) > 0
                    and re.fullmatch(r"[0-9a-f]{64}", str(revision["sourceDigest"])) is None
                )
            ):
                raise RuntimeError("customer-service revision evidence is incomplete")
            cursor.execute(
                "SELECT status, COALESCE(authority_epoch::text, ''), cutover_id, "
                "migration_verify_run_id FROM customer_service_write_authority WHERE id = 1"
            )
            customer_service_authority_row = cursor.fetchone()
            if customer_service_authority_row is None:
                raise RuntimeError("customer-service write authority singleton is missing")
            customer_service_status, customer_service_epoch, customer_service_cutover, customer_service_run = (
                str(value or "") for value in customer_service_authority_row
            )
            if customer_service_status not in {"d1", "postgres"}:
                raise RuntimeError("customer-service write authority status is invalid")
            if customer_service_run and re.fullmatch(r"customer-service-[0-9a-f]{32}", customer_service_run) is None:
                raise RuntimeError("customer-service migration run evidence is invalid")
            if customer_service_status == "postgres":
                if (
                    int(revision["revision"]) < 1
                    or re.fullmatch(r"[0-9a-fA-F-]{36}", customer_service_epoch) is None
                    or re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", customer_service_cutover) is None
                    or not customer_service_run
                ):
                    raise RuntimeError("active customer-service write authority evidence is incomplete")
            elif customer_service_epoch or customer_service_cutover:
                raise RuntimeError("inactive customer-service write authority contains activation evidence")
            customer_service_authority = {
                "status": customer_service_status,
                "authorityEpoch": customer_service_epoch,
                "cutoverId": customer_service_cutover,
                "migrationRunId": customer_service_run,
            }

        access_control_evidence = None
        if access_control_tables:
            cursor.execute("SELECT revision, source_digest FROM access_control_data_revisions WHERE domain='access-control'")
            revision_row = cursor.fetchone()
            cursor.execute("SELECT status, COALESCE(authority_epoch::text,''), cutover_id, migration_verify_run_id FROM access_control_write_authority WHERE id=1")
            authority_row = cursor.fetchone()
            if not revision_row or not authority_row:
                raise RuntimeError("access-control revision/authority evidence is missing")
            revision_number, digest = revision_row
            status, epoch, cutover, run_id = authority_row
            if int(revision_number) < 0 or not re.fullmatch(r"[0-9a-f]{64}", digest) or status not in {"d1", "postgres"}:
                raise RuntimeError("access-control revision/authority evidence is invalid")
            if status == "postgres" and (
                int(revision_number) < 1 or not re.fullmatch(r"[0-9a-f-]{36}", epoch)
                or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", cutover)
                or not re.fullmatch(r"access-control-[0-9a-f]{32}", run_id)
            ):
                raise RuntimeError("active access-control evidence is incomplete")
            if status == "d1" and (epoch or cutover):
                raise RuntimeError("inactive access-control contains activation evidence")
            if run_id:
                cursor.execute("SELECT status,source_snapshot_digest,target_snapshot_digest FROM access_control_migration_runs WHERE id=%s AND mode='apply'", (run_id,))
                run = cursor.fetchone()
                if not run or run[0] != "verified" or run[1] != run[2]:
                    raise RuntimeError("access-control migration evidence is not verified")
            access_control_evidence = {
                "revision": int(revision_number), "sourceDigest": digest,
                "status": status, "authorityEpoch": epoch, "cutoverId": cutover, "migrationRunId": run_id,
            }

        ai_evidence = None
        if ai_tables:
            cursor.execute("SELECT revision, source_digest FROM ai_data_revisions WHERE domain='ai-assistant'")
            revision_row = cursor.fetchone()
            cursor.execute("SELECT status, COALESCE(authority_epoch::text,''), cutover_id, migration_verify_run_id FROM ai_write_authority WHERE id=1")
            authority_row = cursor.fetchone()
            if not revision_row or not authority_row:
                raise RuntimeError("AI revision/authority evidence is missing")
            revision_number, digest = revision_row
            status, epoch, cutover, run_id = authority_row
            if int(revision_number) < 0 or status not in {"d1", "postgres"}:
                raise RuntimeError("AI revision/authority evidence is invalid")
            if status == "postgres" and (int(revision_number) < 1 or not re.fullmatch(r"[0-9a-f-]{36}", epoch) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", cutover) or not re.fullmatch(r"ai-apply-[0-9a-f]{32}", run_id)):
                raise RuntimeError("active AI evidence is incomplete")
            if status == "d1" and (epoch or cutover):
                raise RuntimeError("inactive AI contains activation evidence")
            if run_id:
                cursor.execute("SELECT status,source_snapshot_digest,target_snapshot_digest,source_counts,target_counts FROM ai_migration_runs WHERE id=%s AND mode='apply'", (run_id,))
                run = cursor.fetchone()
                if not run or run[0] != "verified" or run[1] != run[2] or run[1] != digest or run[3] != run[4] or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise RuntimeError("AI migration evidence is not verified")
            elif int(revision_number) != 0 or digest:
                raise RuntimeError("AI pre-adoption evidence is inconsistent")
            ai_evidence = {"revision": int(revision_number), "sourceDigest": digest, "status": status, "authorityEpoch": epoch, "cutoverId": cutover, "migrationRunId": run_id}

    content = {
        "tables": row_counts,
        "migrations": migrations,
        "revisions": revisions,
        "writeAuthority": {
            "status": str(authority_status),
            "authorityEpoch": str(authority_epoch),
            "cutoverId": str(cutover_id),
        },
    }
    if erp_reference_authority is not None:
        content["erpReferenceWriteAuthority"] = erp_reference_authority
    if netshop_revisions is not None and netshop_authority is not None:
        content["netshopRevisions"] = netshop_revisions
        content["netshopWriteAuthority"] = netshop_authority
    if market_revisions is not None and market_authority is not None:
        content["marketRevisions"] = market_revisions
        content["marketWriteAuthority"] = market_authority
    if products_revisions is not None and products_authority is not None:
        content["productsRevisions"] = products_revisions
        content["productsWriteAuthority"] = products_authority
    if inventory_revisions is not None and inventory_authority is not None:
        content["inventoryRevisions"] = inventory_revisions
        content["inventoryWriteAuthority"] = inventory_authority
    if (
        workflow_revisions is not None
        and workflow_authority is not None
        and workflow_operations_authority is not None
    ):
        content["workflowRevisions"] = workflow_revisions
        content["workflowWriteAuthority"] = workflow_authority
        content["workflowOperationsWriteAuthority"] = workflow_operations_authority
    if customer_service_revisions is not None and customer_service_authority is not None:
        content["customerServiceRevisions"] = customer_service_revisions
        content["customerServiceWriteAuthority"] = customer_service_authority
    if ai_evidence is not None:
        content["aiAssistant"] = ai_evidence
    if access_control_evidence is not None:
        content["accessControl"] = access_control_evidence
    content_bytes = json.dumps(
        content, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    canonical = {
        "database": {
            "name": str(database_name),
            "user": str(database_user),
            "serverAddress": canonical_server_address,
            "serverPort": int(server_port),
            "inRecovery": bool(recovery),
            "serverVersionNumber": int(version),
        },
        **content,
        "contentSha256": _sha256_bytes(content_bytes),
    }
    canonical_bytes = json.dumps(
        canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return {
        **canonical,
        "canonicalSha256": _sha256_bytes(canonical_bytes),
    }


def _validate_leaf(path_value: str, label: str) -> Path:
    path = Path(path_value).resolve(strict=True)
    if not path.is_file():
        raise RuntimeError(f"{label} is not a regular file")
    return path


def _validate_new_output(path_value: str) -> Path:
    path = Path(path_value).resolve(strict=False)
    if path.exists():
        raise RuntimeError("backup output already exists")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir() or path.parent != parent:
        raise RuntimeError("backup output parent is invalid")
    if path.suffix != ".dump":
        raise RuntimeError("backup output must use the .dump extension")
    return path


def _protected_ai_preflight(cursor: psycopg.Cursor[Any]) -> dict[str, Any]:
    """Read only. Never SELECT or expose the private verifier key contents."""
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name=ANY(%s)", [sorted(PROTECTED_AI_MIGRATIONS)])
    applied = sorted(str(row[0]) for row in cursor.fetchall())
    cursor.execute("SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=ANY(%s)", [list(PROTECTED_AI_ROLES)])
    roles = {str(row[0]): tuple(row[1:]) for row in cursor.fetchall()}
    issues: list[str] = []
    if set(roles) != set(PROTECTED_AI_ROLES) or any(
            flags != (False,) * 7 for flags in roles.values()):
        issues.append("protected_roles_not_exact_nologin")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members member "
        "JOIN pg_catalog.pg_roles parent ON parent.oid=member.roleid "
        "JOIN pg_catalog.pg_roles child ON child.oid=member.member "
        "WHERE parent.rolname=ANY(%s) OR child.rolname=ANY(%s)",
        [list(PROTECTED_AI_ROLES)] * 2)
    if cursor.fetchone() != (0,):
        issues.append("protected_role_membership_present")
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c WHERE c.oid=to_regclass(%s)",
        [PROTECTED_KEY_TABLE])
    owner = cursor.fetchone()
    if owner != ("teruisi_ai_budget_v11_key_owner",):
        issues.append("private_key_table_owner_unverified")
    else:
        cursor.execute("SELECT has_table_privilege(current_user,%s,'SELECT')",
            [PROTECTED_KEY_TABLE])
        if cursor.fetchone() != (True,):
            issues.append("backup_identity_cannot_read_private_key_table")
    cursor.execute("SELECT rolcreaterole,rolsuper FROM pg_catalog.pg_roles "
        "WHERE rolname=current_user")
    installer = cursor.fetchone()
    if installer is None or installer == (False, False):
        issues.append("privileged_migration_installer_not_configured")
    if "--no-privileges" in FORMAL_DUMP_FLAGS:
        issues.append("archive_acl_not_preserved")
    if ("--no-owner" in FORMAL_RESTORE_FLAGS or
            "--no-privileges" in FORMAL_RESTORE_FLAGS):
        issues.append("cross_cluster_owner_acl_not_preserved")
    issues.append("cross_cluster_protected_roles_not_preprovisioned")
    issues.append("restore_role_name_policy_rejects_versioned_roles")
    # The existing daily archive has no protected at-rest encryption contract.
    issues.append("private_key_archive_encryption_not_configured")
    return {"version": VERSION, "status": "blocked", "readOnly": True,
        "appliedProtectedMigrations": applied,
        "exactProtectedRoleCount": len(roles), "issues": sorted(set(issues))}


def _finance_raw_evidence_preflight(cursor: psycopg.Cursor[Any]) -> bool:
    """Read-only fail-closed detection before any formal archive work."""
    cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations "
        "WHERE app='finance' AND name=%s)",
        [FINANCE_RAW_EVIDENCE_MIGRATION])
    receipt = cursor.fetchone()
    cursor.execute("SELECT c.relname FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname=ANY(%s) "
        "AND c.relkind IN ('r','p')", [sorted(FINANCE_RAW_EVIDENCE_TABLES)])
    tables = {str(row[0]) for row in cursor.fetchall()}
    if receipt not in ((False,), (True,)) or tables and tables != FINANCE_RAW_EVIDENCE_TABLES:
        raise RuntimeError("finance raw evidence catalog is incomplete")
    if bool(receipt[0]) != bool(tables):
        raise RuntimeError("finance raw evidence receipt/table inventory differs")
    return bool(receipt[0])


def _finance_raw_workbook_preflight(cursor: psycopg.Cursor[Any]) -> bool:
    """Detect 0006 receipt and exact three-table inventory before pg_dump."""
    cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations "
        "WHERE app='finance' AND name=%s)",
        [FINANCE_RAW_WORKBOOK_MIGRATION])
    receipt = cursor.fetchone()
    cursor.execute("SELECT c.relname FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname=ANY(%s) "
        "AND c.relkind IN ('r','p')", [sorted(FINANCE_RAW_WORKBOOK_TABLES)])
    tables = {str(row[0]) for row in cursor.fetchall()}
    if (receipt not in ((False,), (True,))
            or tables and tables != FINANCE_RAW_WORKBOOK_TABLES):
        raise RuntimeError("finance raw workbook catalog is incomplete")
    if bool(receipt[0]) != bool(tables):
        raise RuntimeError("finance raw workbook receipt/table inventory differs")
    return bool(receipt[0])


def run_protected_preflight(args: argparse.Namespace) -> dict[str, Any]:
    with psycopg.connect("") as connection:
        connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(),current_user,"
                "COALESCE(inet_server_addr()::text,''),inet_server_port()")
            identity = cursor.fetchone()
            if (identity is None or identity[0] != args.expected_database
                    or identity[1] != args.expected_user
                    or _canonical_loopback_address(identity[2]) != "127.0.0.1"
                    or int(identity[3]) != int(args.port)):
                raise RuntimeError("protected AI preflight database identity mismatch")
            result = _protected_ai_preflight(cursor)
        connection.rollback()
    return result


def run_backup(args: argparse.Namespace) -> dict[str, Any]:
    pg_dump = _validate_leaf(args.pg_dump, "pg_dump")
    output = _validate_new_output(args.output)
    completed: subprocess.CompletedProcess[bytes] | None = None

    try:
        with psycopg.connect("") as connection:
            connection.execute(
                "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_export_snapshot()")
                snapshot = str(cursor.fetchone()[0])
                protected = _protected_ai_preflight(cursor)
                if protected["appliedProtectedMigrations"]:
                    raise RuntimeError("protected AI daily backup is not admitted")
                if _finance_raw_workbook_preflight(cursor):
                    raise RuntimeError("finance raw workbook daily backup is not admitted")
                if _finance_raw_evidence_preflight(cursor):
                    raise RuntimeError("finance raw evidence daily backup is not admitted")
            evidence = collect_evidence(
                connection,
                expected_database=args.expected_database,
                expected_user=args.expected_user,
            )
            command = [
                str(pg_dump),
                "--host=127.0.0.1",
                f"--port={int(args.port)}",
                f"--username={args.expected_user}",
                f"--dbname={args.expected_database}",
                "--format=custom",
                "--compress=6",
                *FORMAL_DUMP_FLAGS,
                "--lock-wait-timeout=5000",
                f"--snapshot={snapshot}",
                f"--file={output}",
            ]
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=int(args.timeout_seconds),
                env=os.environ.copy(),
            )
            if completed.returncode != 0:
                raise RuntimeError("pg_dump failed")
            if not output.is_file() or output.stat().st_size < 1:
                raise RuntimeError("pg_dump produced an empty archive")
            connection.commit()
        return {
            "version": VERSION,
            "status": "completed",
            "snapshotIdSha256": _sha256_bytes(snapshot.encode("utf-8")),
            "evidence": evidence,
            "nativeDiagnostic": _safe_native_diagnostic(completed),
        }
    except subprocess.TimeoutExpired as exc:
        bounded = ((exc.stdout or b"") + b"\n" + (exc.stderr or b""))[
            :MAX_NATIVE_DIAGNOSTIC_BYTES
        ]
        raise RuntimeError(
            "pg_dump timed out; diagnosticSha256=" + _sha256_bytes(bounded)
        ) from None
    except Exception:
        if output.exists():
            output.unlink()
        raise


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    with psycopg.connect("") as connection:
        connection.execute(
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        evidence = collect_evidence(
            connection,
            expected_database=args.expected_database,
            expected_user=args.expected_user,
        )
        connection.rollback()
    return {
        "version": VERSION,
        "status": "completed",
        "evidence": evidence,
    }


def run_restore(args: argparse.Namespace) -> dict[str, Any]:
    pg_restore = _validate_leaf(args.pg_restore, "pg_restore")
    archive = _validate_leaf(args.archive, "backup archive")
    listed = subprocess.run([str(pg_restore), "--list", str(archive)],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=min(int(args.timeout_seconds), 60), env=os.environ.copy())
    if listed.returncode != 0:
        raise RuntimeError("restore archive list preflight failed")
    if b"protected_business_" in listed.stdout:
        raise RuntimeError("protected AI archive restore is not admitted")
    if any(table.encode("ascii") in listed.stdout
            for table in FINANCE_RAW_EVIDENCE_TABLES | FINANCE_RAW_WORKBOOK_TABLES):
        raise RuntimeError("finance raw evidence archive restore is not admitted")
    command = [
        str(pg_restore),
        "--host=127.0.0.1",
        f"--port={int(args.port)}",
        f"--username={args.expected_user}",
        f"--dbname={args.expected_database}",
        "--single-transaction",
        *FORMAL_RESTORE_FLAGS,
        str(archive),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=int(args.timeout_seconds),
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        bounded = ((exc.stdout or b"") + b"\n" + (exc.stderr or b""))[
            :MAX_NATIVE_DIAGNOSTIC_BYTES
        ]
        raise RuntimeError(
            "pg_restore timed out; diagnosticSha256=" + _sha256_bytes(bounded)
        ) from None
    if completed.returncode != 0:
        diagnostic = _safe_native_diagnostic(completed)
        raise RuntimeError(
            "pg_restore failed; diagnosticSha256=" + diagnostic["outputSha256"]
        )
    return {
        "version": VERSION,
        "status": "completed",
        "nativeDiagnostic": _safe_native_diagnostic(completed),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup")
    backup.add_argument("--pg-dump", required=True)
    backup.add_argument("--output", required=True)
    backup.add_argument("--expected-database", required=True)
    backup.add_argument("--expected-user", required=True)
    backup.add_argument("--port", required=True, type=int)
    backup.add_argument("--timeout-seconds", type=int, default=1800)

    probe = subparsers.add_parser("probe")
    probe.add_argument("--expected-database", required=True)
    probe.add_argument("--expected-user", required=True)

    protected = subparsers.add_parser("protected-preflight")
    protected.add_argument("--expected-database", required=True)
    protected.add_argument("--expected-user", required=True)
    protected.add_argument("--port", required=True, type=int)

    restore = subparsers.add_parser("restore")
    restore.add_argument("--pg-restore", required=True)
    restore.add_argument("--archive", required=True)
    restore.add_argument("--expected-database", required=True)
    restore.add_argument("--expected-user", required=True)
    restore.add_argument("--port", required=True, type=int)
    restore.add_argument("--timeout-seconds", type=int, default=1800)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "backup":
            result = run_backup(args)
        elif args.command == "restore":
            result = run_restore(args)
        elif args.command == "protected-preflight":
            result = run_protected_preflight(args)
        else:
            result = run_probe(args)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        message = str(exc)
        # Never surface a URL or a credential-bearing native diagnostic.
        if "://" in message or "password" in message.lower():
            message = "database maintenance failed with redacted diagnostics"
        print(
            json.dumps(
                {
                    "version": VERSION,
                    "status": "failed",
                    "errorType": type(exc).__name__,
                    "errorSha256": _sha256_bytes(message.encode("utf-8")),
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
