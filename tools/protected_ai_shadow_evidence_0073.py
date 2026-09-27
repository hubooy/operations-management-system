"""Versioned, bounded logical evidence for the frozen 0073 test snapshot.

This deliberately does not implement or impersonate the formal backup's
contentSha256 contract. No raw row, file content, or private key leaves this
collector; only deterministic digests and counts are returned.
"""
from __future__ import annotations

import hashlib
from ipaddress import IPv4Address, IPv6Address, ip_interface
import json
from typing import Any

from psycopg import sql


VERSION = "protected-shadow-logical-evidence-0073-v1"
MAX_TABLES = 320  # frozen 0073 synthetic TOC has 282 public tables
MAX_ROWS = 1_000_000
MAX_ROW_BYTES = 512 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
REQUIRED_RECEIPT = "0073_business_promotion_budget_v11_login_attestation"
CATALOG_ITEM_FIELDS = {
    "relations": ("name", "kind", "owner", "acl", "rowSecurity"),
    "constraints": ("table", "name", "type", "definition", "validated",
        "deferrable", "initiallyDeferred"),
    "indexes": ("table", "name", "definition", "valid", "ready"),
}


class ShadowEvidenceBlocked(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("ascii")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_acl_entries(is_null: bool, entries: object) -> dict:
    """Ignore ACL array order, retain every grantor/grantee/option and NULL."""
    if type(is_null) is not bool or not (entries is None or isinstance(
            entries, (list, tuple))):
        raise ShadowEvidenceBlocked("shadow relation ACL shape invalid")
    if is_null:
        if entries not in (None, [], ()):
            raise ShadowEvidenceBlocked("NULL relation ACL has explicit grants")
        return {"isNull": True, "grants": []}
    grants = []
    for item in entries or []:
        if (not isinstance(item, (list, tuple)) or len(item) != 4
                or not all(isinstance(value, str) and value
                    for value in item[:3])
                or type(item[3]) is not bool):
            raise ShadowEvidenceBlocked("shadow relation ACL grant invalid")
        grants.append(list(item))
    grants.sort(key=canonical)
    return {"isNull": False, "grants": grants}


def _loopback(value: object) -> str:
    try:
        endpoint = ip_interface(str(value))
    except ValueError as error:
        raise ShadowEvidenceBlocked("shadow source address is not loopback") from error
    address = endpoint.ip
    if isinstance(address, IPv4Address):
        if address == IPv4Address("127.0.0.1") and endpoint.network.prefixlen == 32:
            return "127.0.0.1"
    elif isinstance(address, IPv6Address) and endpoint.network.prefixlen == 128:
        if address == IPv6Address("::1"):
            return "::1"
        if address.ipv4_mapped == IPv4Address("127.0.0.1"):
            return "127.0.0.1"
    raise ShadowEvidenceBlocked("shadow source address is not exact loopback")


def digest_public_rows(rows_by_table: dict[str, list[object]]) -> dict:
    if (not rows_by_table or len(rows_by_table) > MAX_TABLES
            or any(not isinstance(name, str) or not name
                or not isinstance(rows, list)
                for name, rows in rows_by_table.items())):
        raise ShadowEvidenceBlocked("shadow public table inventory is invalid")
    result = {}
    total_rows = 0
    total_bytes = 0
    for name, rows in sorted(rows_by_table.items()):
        hashes = []
        for row in rows:
            packed = canonical(row)
            total_rows += 1
            total_bytes += len(packed)
            if total_rows > MAX_ROWS or total_bytes > MAX_ROW_BYTES:
                raise ShadowEvidenceBlocked("shadow public rows exceed bound")
            hashes.append(digest(packed))
        hashes.sort()
        result[name] = {"rowCount": len(rows),
            "rowHashesSha256": digest(canonical(hashes))}
    return {"tables": result, "tableCount": len(result),
        "rowCount": total_rows, "rowBytes": total_bytes,
        "tableRowsRootSha256": digest(canonical(result))}


def _public_row_roots(db, table_names: list[str]) -> dict:
    result = {}
    total_rows = 0
    total_bytes = 0
    for name in table_names:
        target = sql.Identifier("public", name)
        count, estimated_bytes = db.execute(sql.SQL("SELECT count(*),"
            "COALESCE(sum(octet_length(row_to_json(t)::text)),0) "
            "FROM {} t").format(target)).fetchone()
        if (total_rows + count > MAX_ROWS
                or total_bytes + estimated_bytes > MAX_ROW_BYTES):
            raise ShadowEvidenceBlocked("shadow public rows exceed bound")
        hashes = []
        with db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(target))
            for (row,) in cursor:
                packed = canonical(row)
                total_rows += 1
                total_bytes += len(packed)
                if total_rows > MAX_ROWS or total_bytes > MAX_ROW_BYTES:
                    raise ShadowEvidenceBlocked("shadow public rows exceed bound")
                hashes.append(digest(packed))
        if len(hashes) != count:
            raise ShadowEvidenceBlocked("shadow public table changed during read")
        hashes.sort()
        result[name] = {"rowCount": len(hashes),
            "rowHashesSha256": digest(canonical(hashes))}
    return {"tables": result, "tableCount": len(result), "rowCount": total_rows,
        "rowBytes": total_bytes,
        "tableRowsRootSha256": digest(canonical(result))}


def _catalog_roots(db) -> dict[str, object]:
    sections = {}
    items = {}
    queries = {
        "schemas": "SELECT n.nspname,pg_catalog.pg_get_userbyid(n.nspowner),"
            "n.nspacl::text FROM pg_catalog.pg_namespace n WHERE n.nspname='public'",
        "relations": "SELECT c.relname,c.relkind,pg_catalog.pg_get_userbyid(c.relowner),"
            "c.relacl IS NULL,c.relrowsecurity,c.relacl::text,"
            "(SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array("
            "CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(acl.grantee) END,"
            "CASE WHEN acl.grantor=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(acl.grantor) END,"
            "acl.privilege_type,acl.is_grantable)) "
            "FROM pg_catalog.aclexplode(c.relacl) acl) "
            "FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p','S','v','m') "
            "ORDER BY c.relname",
        "columns": "SELECT c.relname,a.attname,"
            "pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,"
            "a.attgenerated,a.attidentity,pg_catalog.pg_get_expr(d.adbin,d.adrelid) "
            "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c "
            "ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n "
            "ON n.oid=c.relnamespace LEFT JOIN pg_catalog.pg_attrdef d "
            "ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
            "WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped "
            "ORDER BY c.relname,a.attnum",
        "constraints": "SELECT c.relname,k.conname,k.contype,"
            "pg_catalog.pg_get_constraintdef(k.oid),k.convalidated,"
            "k.condeferrable,k.condeferred FROM pg_catalog.pg_constraint k "
            "JOIN pg_catalog.pg_class c ON c.oid=k.conrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' ORDER BY c.relname,k.conname",
        "indexes": "SELECT c.relname,i.relname,"
            "pg_catalog.pg_get_indexdef(i.oid),x.indisvalid,x.indisready "
            "FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class c "
            "ON c.oid=x.indrelid JOIN pg_catalog.pg_class i "
            "ON i.oid=x.indexrelid JOIN pg_catalog.pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "ORDER BY c.relname,i.relname",
        "triggers": "SELECT c.relname,t.tgname,t.tgenabled,t.tgtype,"
            "t.tgdeferrable,t.tginitdeferred,p.proname,"
            "pg_catalog.pg_get_function_identity_arguments(p.oid) "
            "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c "
            "ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n "
            "ON n.oid=c.relnamespace JOIN pg_catalog.pg_proc p "
            "ON p.oid=t.tgfoid WHERE n.nspname='public' AND NOT t.tgisinternal "
            "ORDER BY c.relname,t.tgname",
        "sequences": "SELECT schemaname,sequencename,last_value::text "
            "FROM pg_catalog.pg_sequences WHERE schemaname='public' "
            "ORDER BY sequencename",
    }
    total = 0
    raw_acl_roots = {}
    for name, query in queries.items():
        rows = db.execute(query).fetchall()
        total += len(canonical(rows))
        if total > MAX_CATALOG_BYTES:
            raise ShadowEvidenceBlocked("shadow catalog exceeds bound")
        if name == "relations":
            normalized = []
            for relation, kind, owner, is_null, security, raw_acl, grants in rows:
                identity = "public." + relation
                if identity in raw_acl_roots:
                    raise ShadowEvidenceBlocked("shadow relation identity repeated")
                raw_acl_roots[identity] = digest(canonical(raw_acl))
                normalized.append((relation, kind, owner,
                    canonical_acl_entries(is_null, grants), security))
            rows = normalized
        sections[name] = digest(canonical(rows))
        if name in CATALOG_ITEM_FIELDS:
            fields = CATALOG_ITEM_FIELDS[name]
            section_items = {}
            for row in rows:
                if len(row) != len(fields):
                    raise ShadowEvidenceBlocked("shadow catalog item shape drifted")
                identity = ("public." + str(row[0]) if name == "relations"
                    else "public." + str(row[0]) + "." + str(row[1]))
                if identity in section_items:
                    raise ShadowEvidenceBlocked("shadow catalog item identity repeated")
                section_items[identity] = {field: digest(canonical(value))
                    for field, value in zip(fields, row)}
            items[name] = section_items
    functions = db.execute("SELECT p.proname,"
        "pg_catalog.pg_get_function_identity_arguments(p.oid),p.prosrc,"
        "pg_catalog.pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,"
        "p.proacl::text,l.lanname FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='public' ORDER BY p.proname,"
        "pg_catalog.pg_get_function_identity_arguments(p.oid)").fetchall()
    if len(functions) > 1024:
        raise ShadowEvidenceBlocked("shadow function catalog exceeds bound")
    normalized = []
    for name, args, body, owner, definer, config, acl, language in functions:
        if not isinstance(body, str):
            raise ShadowEvidenceBlocked("shadow function body is invalid")
        total += len(body.encode("utf-8"))
        if total > MAX_CATALOG_BYTES:
            raise ShadowEvidenceBlocked("shadow catalog exceeds bound")
        normalized.append((name, args, digest(body.encode("utf-8")), owner,
            definer, config, acl, language))
    sections["functions"] = digest(canonical(normalized))
    return {"sections": sections, "items": items,
        "rawRelationAclRoots": raw_acl_roots,
        "catalogOwnerAclRootSha256": digest(canonical(sections))}


def collect_shadow_evidence(db, *, expected_port: int,
                            protected_rows: dict, files: dict,
                            roles: dict) -> dict[str, Any]:
    identity = db.execute("SELECT current_database(),session_user,"
        "inet_server_addr()::text,inet_server_port(),pg_is_in_recovery(),"
        "current_setting('server_version_num')::integer").fetchone()
    if (identity is None or identity[0] != "teruisi_ai_rehearsal"
            or identity[1] != "ai_rehearsal_admin"
            or identity[3] != expected_port or identity[4] is not False
            or identity[5] < 170000):
        raise ShadowEvidenceBlocked("shadow database identity drifted")
    _loopback(identity[2])
    migrations = db.execute("SELECT app,name FROM public.django_migrations "
        "ORDER BY app,name").fetchall()
    if (not migrations or ("ai_assistant", REQUIRED_RECEIPT) not in migrations
            or any(app == "ai_assistant" and name[:4].isdigit()
                and int(name[:4]) > 73 for app, name in migrations)):
        raise ShadowEvidenceBlocked("shadow 0073 migration inventory drifted")
    table_names = [row[0] for row in db.execute("SELECT tablename FROM "
        "pg_catalog.pg_tables WHERE schemaname='public' "
        "ORDER BY tablename").fetchall()]
    if not table_names or len(table_names) > MAX_TABLES:
        raise ShadowEvidenceBlocked("shadow table inventory exceeds bound")
    if (len(protected_rows) != 9
            or not all(name.startswith("protected_business_")
                and name in table_names for name in protected_rows)
            or files.get("chunkCount", 0) < 2
            or not {"html", "xlsx"} <= set(files.get("formats", []))
            or not isinstance(roles, dict) or not roles.get("entries")):
        raise ShadowEvidenceBlocked("shadow protected/file/role roots incomplete")
    table_rows = _public_row_roots(db, table_names)
    catalog_roots = _catalog_roots(db)
    migration_root = digest(canonical(migrations))
    protected_root = digest(canonical(protected_rows))
    role_root = digest(canonical(roles))
    core = {"migrationRootSha256": migration_root,
        "tableRowsRootSha256": table_rows["tableRowsRootSha256"],
        "catalogOwnerAclRootSha256": catalog_roots[
            "catalogOwnerAclRootSha256"],
        "protectedRowsRootSha256": protected_root,
        "fileRootSha256": files["chunkRootSha256"],
        "roleRootSha256": role_root}
    return {"schemaVersion": VERSION,
        "shadowContentSha256": digest(canonical(core)),
        **core, "tableCount": table_rows["tableCount"],
        "tableRoots": table_rows["tables"],
        "catalogSectionRoots": catalog_roots["sections"],
        "catalogItemRoots": catalog_roots["items"],
        "rawRelationAclRoots": catalog_roots["rawRelationAclRoots"],
        "rowCount": table_rows["rowCount"],
        "rowBytes": table_rows["rowBytes"],
        "formalContentSha256Verified": False,
        "productionWrites": False}
