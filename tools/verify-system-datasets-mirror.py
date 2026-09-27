"""Verify every dataset's column grants in a disposable, migrated PostgreSQL.

This script intentionally only runs against fixture_owner / datasets_test on
127.0.0.1:15468. It never starts services or accepts a production fallback.
"""
import os
import sys
import json
from pathlib import Path
from urllib.parse import urlsplit

url = urlsplit(os.environ.get("TERUISI_DJANGO_DATABASE_URL", ""))
if (url.hostname, url.port, url.username, url.path) != ("127.0.0.1", 15468, "fixture_owner", "/datasets_test"):
    raise SystemExit("Refusing non-fixture database")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "teruisi_backend.settings")
import django
django.setup()
from django.db import connection, DatabaseError, transaction
from django.test import override_settings
from psycopg import sql
from sales.auth import Principal
from system_datasets.catalog import SPECS, DOMAINS
from system_datasets.permissions import grant_columns, validate_reader_columns
from system_datasets.reader import query

actor = Principal("local-admin@teruisi.local", "Fixture admin", "admin", None)
with connection.cursor() as cursor:
    cursor.execute("SELECT current_user,current_database(),inet_server_port()")
    if cursor.fetchone() != ("fixture_owner", "datasets_test", 15468):
        raise SystemExit("Fixture database identity mismatch")
    for domain, (_, role) in DOMAINS.items():
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
        if not cursor.fetchone():
            cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role)))
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        grant_columns(cursor, domain)
        grant_columns(cursor, domain)  # Repeat provisioning must preserve the exact column contract.

checked = []
for domain, (process_role, database_role) in DOMAINS.items():
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(database_role)))
        validate_reader_columns(cursor, process_role)
    try:
        with override_settings(DJANGO_PROCESS_ROLE=process_role):
            for spec in SPECS.values():
                if spec["domain"] != domain:
                    continue
                columns = list(spec["fields"])
                for start in range(0, len(columns), 50):
                    result = query(spec["id"], {"columns": columns[start:start+50], "pageSize": 1}, actor)
                    assert result["dataset"] == spec["id"]
                checked.append(spec["id"])
            # Prove column-only roles cannot write, even outside the API.
            sample = next(s for s in SPECS.values() if s["domain"] == domain)
            rejected = False
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(sql.SQL("DELETE FROM {} WHERE false").format(sql.Identifier(sample["table"])))
            except DatabaseError:
                rejected = True
            assert rejected, domain + " retained DELETE"
            for spec in SPECS.values():
                if spec["domain"] != domain:
                    continue
                for name, reason in spec["excludedFields"].items():
                    if name in spec.get("paginationOnlyFields", {}):
                        continue
                    from django.apps import apps
                    model = apps.get_model(spec["domain"], spec["model"])
                    field = next(f for f in model._meta.concrete_fields if f.attname == name)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT has_column_privilege(current_user,%s,%s,'SELECT')", ("public."+spec["table"], field.column))
                        assert cursor.fetchone()[0] is False, spec["id"]+" leaked "+name
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
print(json.dumps({"datasetsVerified": len(checked), "domains": len(DOMAINS), "readOnly": True,
                  "protectedColumnsDenied": True, "productionTouched": False}, ensure_ascii=False))
