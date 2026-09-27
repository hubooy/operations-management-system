"""Explicit read-only catalog export for a separately credentialed mirror.

The ordinary backup intentionally excludes ACLs. This export reads no business
rows and never supplies production credentials to the mirror runner.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import psycopg

target = Path(sys.argv[1]).absolute()
root = Path(__file__).resolve().parents[1]
if root == Path(r"D:\运营管理系统") or target.parent != root / ".runtime/d1-retirement" or target.exists():
    raise RuntimeError("acl_export_requires_new_isolated_evidence_file")
domains = ["sales", "finance", "netshop", "market", "products", "inventory", "workflow", "customer_service", "bi", "erp_reference", "access_control", "ai"]
roles = ["teruisi_" + domain + "_" + role for domain in domains for role in (["reader"] if domain == "bi" else ["reader", "writer"])]
with psycopg.connect(host="127.0.0.1", port=5432, dbname="teruisi_sales", user="teruisi_sales_reader",
                      password=os.environ["TERUISI_ACL_READER_PASSWORD"], options="-c default_transaction_read_only=on -c statement_timeout=10000", connect_timeout=5) as connection:
    connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    if connection.execute("SHOW transaction_read_only").fetchone()[0] != "on":
        raise RuntimeError("acl_export_read_only_required")
    flags = connection.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname", [roles]).fetchall()
    if len(flags) != 23 or any(any(row[1:]) for row in flags):
        raise RuntimeError("acl_export_role_flags_invalid")
    if connection.execute("SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=ANY(%s) LIMIT 1", [roles]).fetchone():
        raise RuntimeError("acl_export_role_membership_rejected")
    relations = connection.execute("""SELECT c.relkind,c.relname,r.rolname,a.privilege_type FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
      WHERE n.nspname='public' AND r.rolname=ANY(%s) ORDER BY 1,2,3,4""", [roles]).fetchall()
    columns = connection.execute("""SELECT c.relname,col.attname,r.rolname,a.privilege_type FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute col ON col.attrelid=c.oid
      CROSS JOIN LATERAL aclexplode(col.attacl) a JOIN pg_roles r ON r.oid=a.grantee
      WHERE n.nspname='public' AND col.attnum>0 AND NOT col.attisdropped AND r.rolname=ANY(%s) ORDER BY 1,2,3,4""", [roles]).fetchall()
    schemas = connection.execute("""SELECT r.rolname,a.privilege_type FROM pg_namespace n
      CROSS JOIN LATERAL aclexplode(n.nspacl) a JOIN pg_roles r ON r.oid=a.grantee
      WHERE n.nspname='public' AND r.rolname=ANY(%s) ORDER BY 1,2""", [roles]).fetchall()
    database = connection.execute("""SELECT r.rolname,a.privilege_type FROM pg_database d
      CROSS JOIN LATERAL aclexplode(d.datacl) a JOIN pg_roles r ON r.oid=a.grantee
      WHERE d.datname=current_database() AND r.rolname=ANY(%s) ORDER BY 1,2""", [roles]).fetchall()
payload = {"version": "teruisi-mirror-role-acl-v1", "roles": roles, "relations": relations, "columns": columns, "schemas": schemas, "database": database}
raw = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
with target.open("xb") as stream:
    stream.write(raw)
print(json.dumps({"status": "completed", "operation": "read_only_catalog_export", "roles": len(roles), "relationGrants": len(relations), "columnGrants": len(columns), "sha256": hashlib.sha256(raw).hexdigest()}))
