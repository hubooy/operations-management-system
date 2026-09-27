"""Column-only reader grants; safe to import in the existing operator scripts.

This does not grant table-level SELECT, DML, DDL, memberships or RLS bypass.
It runs only when the operator provisions the corresponding domain roles.
"""
import json
from pathlib import Path


def validate_reader_columns(cursor, process_role):
    domain = {"reader": "sales", "ai_reader": "ai_assistant"}.get(process_role)
    if domain is None and process_role.endswith("_reader"):
        domain = process_role[:-7]
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text(encoding="utf-8"))
    specs = [item for item in manifest["datasets"] if item["domain"] == domain]
    if not specs:
        return
    tables, columns = [], []
    for spec in specs:
        for field in [*spec["fields"].values(), *spec.get("paginationOnlyFields", {}).values()]:
            tables.append("public." + spec["table"])
            columns.append(field["column"])
    cursor.execute("SELECT bool_and(has_column_privilege(current_user,t,c,'SELECT')) FROM unnest(%s::text[],%s::text[]) AS requested(t,c)", (tables, columns))
    if cursor.fetchone()[0] is not True:
        raise ValueError("Dataset reader column grants are incomplete")


def grant_columns(cursor, domain):
    from psycopg import sql
    role = {"sales": "teruisi_sales_reader", "ai_assistant": "teruisi_ai_reader"}.get(domain, f"teruisi_{domain}_reader")
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text(encoding="utf-8"))
    specs = [item for item in manifest["datasets"] if item["domain"] == domain]
    if not specs:
        raise ValueError("Unknown dataset grant domain")
    for spec in specs:
        # Reconcile prior column grants as well, so a later allowlist removal
        # cannot leave stale privileges. Existing table-level domain grants and
        # grants on other domains are intentionally unaffected.
        cursor.execute("SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped", ("public." + spec["table"],))
        existing = [row[0] for row in cursor.fetchall()]
        if existing:
            cursor.execute(sql.SQL("REVOKE SELECT ({}) ON public.{} FROM {}").format(
                sql.SQL(",").join(sql.Identifier(name) for name in existing),
                sql.Identifier(spec["table"]), sql.Identifier(role)))
        columns = list(dict.fromkeys([*(v["column"] for v in spec["fields"].values()),
                                      *(v["column"] for v in spec.get("paginationOnlyFields", {}).values())]))
        if not columns:
            continue
        cursor.execute(sql.SQL("GRANT SELECT ({}) ON public.{} TO {}").format(
            sql.SQL(",").join(sql.Identifier(name) for name in columns),
            sql.Identifier(spec["table"]), sql.Identifier(role)))
