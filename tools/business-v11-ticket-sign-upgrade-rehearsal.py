"""Disposable 0075->0076 upgrade, frozen old catalogs and two restores."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ai_assistant import (business_promotion_budget_v11_stage_sql as stage,
    business_promotion_budget_v11_ticket_sign_sql as v3)


parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root",type=Path,required=True)
args=parser.parse_args()
folder=args.run_root.resolve()
database=settings.DATABASES["default"]
OLD=("ai_assistant","0075_business_v4_report_restricted_page")
NEW=("ai_assistant","0076_business_promotion_budget_v11_ticket_bound_signer")
candidate=importlib.import_module("ai_assistant.migrations."+NEW[1])
old68=importlib.import_module(
    "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
old70=importlib.import_module(
    "ai_assistant.migrations.0070_business_promotion_budget_v11_limited_identity")
old73=importlib.import_module(
    "ai_assistant.migrations.0073_business_promotion_budget_v11_login_attestation")
old75=importlib.import_module("ai_assistant.migrations."+OLD[1])
BIN=Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
OLD_SIGNATURES=(old68.MAC_SIGNATURE,old68.VERIFY_SIGNATURE,
    old70.v2.ISSUE,old70.v2.READ,old70.v2.VERIFY,
    *old73.v2.SIGNATURES,
    "public.ai_business_files_guard()",
    "public.ai_business_volume_complete_guard()",
    old75.page.RO_BINDINGS,old75.page.RO_READ,old75.page.PAGE)
OLD_TABLES=frozenset({
    "protected_business_budget_v11_verifier_keys",
    "protected_business_budget_v11_proof_tickets",
    "protected_business_budget_v11_proof_ticket_claims",
    "protected_business_v4_report_link_intents",
    "protected_business_v4_report_source_links",
    "protected_business_market_v2_rate_proposals",
    "protected_business_market_v2_cap_proposals",
    "protected_business_market_v2_authority_revocations",
    "protected_business_budget_v11_login_attestations",
    "protected_business_market_v2_human_cap_approvals",
    "protected_business_market_v2_human_cap_revocations"})
FROZEN_RENDERER_FILES=(
    "backend/ai_assistant/business_files.py",
    "backend/ai_assistant/business_volume_files.py",
    "backend/ai_assistant/business_promotion_budget_v11_stage_sql.py",
    "backend/ai_assistant/business_promotion_budget_v11_durable_stage.py",
    "backend/ai_assistant/business_promotion_budget_v11_preflight.py",
    "backend/business_analysis/promotion_budget_attestation_v11.py",
    "backend/business_analysis/promotion_budget_verifier_receipt_v11.py")

if (ROOT.resolve()==Path(r"D:\运营管理系统").resolve()
        or folder.parent!=(ROOT/".runtime").resolve()
        or settings.DJANGO_ENVIRONMENT!="test"
        or database["HOST"]!="127.0.0.1"
        or database["NAME"]!="teruisi_ai_rehearsal"
        or str(database["PORT"])!=os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440<=int(database["PORT"])<=55999
        or connection.vendor!="postgresql"):
    raise RuntimeError("0076 upgrade requires exact disposable PostgreSQL")


def db(name=None):
    return psycopg.connect(host=database["HOST"],port=database["PORT"],
        dbname=name or database["NAME"],user=database["USER"],
        password=database["PASSWORD"],autocommit=True)


def native(command,timeout=600):
    env={**os.environ,"PGHOST":str(database["HOST"]),
        "PGPORT":str(database["PORT"]),"PGUSER":str(database["USER"]),
        "PGPASSWORD":str(database["PASSWORD"])}
    result=subprocess.run([str(item) for item in command],cwd=ROOT,
        env=env,capture_output=True,timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    if result.returncode:
        raise RuntimeError("0076 isolated archive command failed; diagnosticSha256="+
            hashlib.sha256(result.stderr[:16384]).hexdigest())


def function_catalog(source):
    rows=[]
    for signature in OLD_SIGNATURES:
        row=source.execute("SELECT oid,prosrc,proacl::text,"
            "pg_catalog.pg_get_userbyid(proowner),prosecdef,proconfig "
            "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
            [signature]).fetchone()
        if row is None: raise RuntimeError("0076 old function missing")
        acl=source.execute("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(a.grantee) END,"
            "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,"
            "a.is_grantable FROM pg_catalog.pg_proc p CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) a "
            "WHERE p.oid=to_regprocedure(%s) ORDER BY 1,2,3,4",
            [signature]).fetchall()
        rows.append((signature,row,tuple(acl)))
    return tuple(rows)


def table_catalog(source,names):
    result=[]
    for name in sorted(names):
        owner=source.execute("SELECT c.relkind,"
            "pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c "
            "WHERE c.oid=%s::regclass",["public."+name]).fetchone()
        acl=source.execute("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(a.grantee) END,"
            "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,"
            "a.is_grantable FROM pg_catalog.pg_class c CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(c.relacl,"
            "pg_catalog.acldefault('r',c.relowner))) a "
            "WHERE c.oid=%s::regclass ORDER BY 1,2,3,4",
            ["public."+name]).fetchall()
        if owner is None: raise RuntimeError("0076 protected table missing")
        result.append((name,owner,tuple(acl)))
    return tuple(result)


def legacy_file_bytes(source):
    rows=[]
    for table in ("ai_business_file_chunks","ai_business_volume_chunks"):
        records=source.execute(sql.SQL("SELECT id,content_digest,"
            "octet_length(content),encode(sha256(content),'hex') FROM {} "
            "ORDER BY id").format(sql.Identifier("public",table))).fetchall()
        rows.append((table,tuple(records)))
    return tuple(rows)


def renderer_source_bytes():
    return tuple((name,hashlib.sha256((ROOT/name).read_bytes()).hexdigest())
        for name in FROZEN_RENDERER_FILES)


def restored_function_equal(actual,expected):
    # OIDs and implicit-vs-explicit ACL text are database-local; owner and
    # expanded effective ACL must remain identical.
    return tuple((n,r[1],r[3:],a) for n,r,a in actual)==tuple(
        (n,r[1],r[3:],a) for n,r,a in expected)


def protected_names(source):
    return frozenset(row[0] for row in source.execute(
        "SELECT c.relname FROM pg_catalog.pg_class c JOIN "
        "pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE "
        "n.nspname='public' AND c.relname LIKE 'protected_business_%' "
        "AND c.relkind IN ('r','p')").fetchall())


def assert_empty(source,names):
    if protected_names(source)!=names:
        raise RuntimeError("0076 protected table inventory drift")
    for name in names:
        row=source.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {} LIMIT 1)").format(
            sql.Identifier("public",name))).fetchone()
        if row!=(False,): raise RuntimeError("0076 protected table not empty")


def archive_restore(name,names):
    path=folder/(name+".dump")
    if path.exists(): raise RuntimeError("0076 rehearsal archive exists")
    with db() as source: assert_empty(source,names)
    native([BIN/"pg_dump.exe","--format=custom","--file",path,
        database["NAME"]])
    native([BIN/"createdb.exe",name])
    native([BIN/"pg_restore.exe","--single-transaction","--exit-on-error",
        "--dbname",name,path])


def assert_formal_backup_closed():
    env={**os.environ,"PGHOST":str(database["HOST"]),
        "PGPORT":str(database["PORT"]),"PGUSER":str(database["USER"]),
        "PGPASSWORD":str(database["PASSWORD"]),
        "PGDATABASE":str(database["NAME"])}
    helper=ROOT/"tools/postgres-consistent-backup.py"
    preflight=subprocess.run([sys.executable,helper,"protected-preflight",
        "--expected-database",database["NAME"],"--expected-user",
        database["USER"],"--port",str(database["PORT"])],
        env=env,capture_output=True,timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    if (preflight.returncode or NEW[1] not in json.loads(
            preflight.stdout)["appliedProtectedMigrations"]):
        raise AssertionError("formal preflight missed 0076")
    target=folder/"ticket_bound_unadmitted_formal.dump"
    if target.exists(): raise AssertionError("0076 formal backup target exists")
    result=subprocess.run([sys.executable,helper,"backup",
        "--pg-dump",BIN/"pg_dump.exe","--output",target,
        "--expected-database",database["NAME"],"--expected-user",
        database["USER"],"--port",str(database["PORT"]),
        "--timeout-seconds","60"],env=env,capture_output=True,timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    try: refused=json.loads(result.stderr)
    except (ValueError,UnicodeError):
        raise AssertionError("0076 formal backup refusal unstructured") from None
    expected=hashlib.sha256(b"protected AI daily backup is not admitted").hexdigest()
    if (result.returncode!=1 or target.exists() or
            refused.get("status")!="failed" or
            refused.get("errorSha256")!=expected):
        raise AssertionError("formal backup did not reject 0076 before archive")


with db() as source:
    receipts=source.execute("SELECT name FROM django_migrations WHERE "
        "app='ai_assistant' AND name IN (%s,%s) ORDER BY name",
        [OLD[1],NEW[1]]).fetchall()
    if receipts != [(OLD[1],)]:
        raise RuntimeError("0076 requires exact 0075 predecessor")
    if protected_names(source)!=OLD_TABLES:
        raise RuntimeError("0076 old protected inventory drift")
    before_functions=function_catalog(source)
    before_tables=table_catalog(source,OLD_TABLES)
    before_files=legacy_file_bytes(source)
    before_source_bytes=renderer_source_bytes()
    class ExpectedRollback(Exception): pass
    negative_target=folder/"ticket_sign_nonempty_key_rejected.dump"
    if negative_target.exists():
        raise RuntimeError("0076 nonempty key negative target exists")
    try:
        with source.transaction():
            source.execute("SET SESSION AUTHORIZATION "
                "teruisi_ai_budget_v11_key_owner")
            try:
                source.execute("INSERT INTO public."
                    "protected_business_budget_v11_verifier_keys "
                    "(key_id,secret,status,created_at) VALUES "
                    "('0076_synthetic_archive_probe',"
                    "decode(repeat('ac',32),'hex'),'active',clock_timestamp())")
            finally:
                source.execute("RESET SESSION AUTHORIZATION")
            try:
                assert_empty(source,OLD_TABLES)
            except RuntimeError as error:
                if str(error)!="0076 protected table not empty": raise
            else:
                raise AssertionError("0076 accepted nonempty key table")
            if negative_target.exists():
                raise AssertionError("0076 nonempty key produced clear archive")
            raise ExpectedRollback()
    except ExpectedRollback:
        pass
    assert_empty(source,OLD_TABLES)
archive_restore("ticket_sign_before",OLD_TABLES)
with db("ticket_sign_before") as restored:
    if (not restored_function_equal(function_catalog(restored),before_functions)
            or table_catalog(restored,OLD_TABLES)!=before_tables
            or legacy_file_bytes(restored)!=before_files):
        raise AssertionError("0076 before independent restore drift")

plan=MigrationExecutor(connection).migration_plan([NEW])
if [(m.app_label,m.name,backward) for m,backward in plan] != [
        (NEW[0],NEW[1],False)]:
    raise RuntimeError("0076 Django forward plan is not exact")
MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor: candidate.verify_catalog(cursor)
    if (function_catalog(source)!=before_functions or
            table_catalog(source,OLD_TABLES)!=before_tables or
            legacy_file_bytes(source)!=before_files or
            renderer_source_bytes()!=before_source_bytes):
        raise AssertionError("0076 changed old function/table OID/body/ACL/owner")
assert_formal_backup_closed()
after_names=OLD_TABLES|{"protected_business_budget_v11_signed_receipts_v3"}
archive_restore("ticket_sign_after",after_names)
with db("ticket_sign_after") as restored:
    with restored.cursor() as cursor: candidate.verify_catalog(cursor)
    if (not restored_function_equal(function_catalog(restored),before_functions)
            or table_catalog(restored,OLD_TABLES)!=before_tables
            or legacy_file_bytes(restored)!=before_files):
        raise AssertionError("0076 after independent restore drift")

MigrationExecutor(connection).migrate([OLD])
with db() as source:
    if (function_catalog(source)!=before_functions or
            table_catalog(source,OLD_TABLES)!=before_tables or
            protected_names(source)!=OLD_TABLES or
            legacy_file_bytes(source)!=before_files):
        raise AssertionError("0076 empty reverse changed predecessor")
    if any(source.execute("SELECT to_regprocedure(%s)", [signature]
            ).fetchone() != (None,) for signature in v3.SIGNATURES):
        raise AssertionError("0076 function survived empty reverse")
MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor: candidate.verify_catalog(cursor)
    if function_catalog(source)!=before_functions:
        raise AssertionError("0076 reapply changed predecessor functions")
if renderer_source_bytes()!=before_source_bytes:
    raise AssertionError("0076 changed frozen renderer source bytes")

result={"upgrade":"0075->0076","status":"passed",
    "finance0005Current":True,"oldFunctionCount":len(OLD_SIGNATURES),
    "oldFunctionOidBodyAclOwnerPreserved":True,
    "oldProtectedTableOwnerAclPreserved":True,
    "oldPersistedFileRowsPreserved":True,
    "oldPersistedFileRowCount":sum(len(rows) for _,rows in before_files),
    "sevenRendererSourceByteDigestsPreserved":True,
    "newTableCount":len(after_names),"newFunctions":len(v3.SIGNATURES),
    "beforeBackupRestored":True,"afterBackupRestored":True,
    "formalBackupRejectedBeforeArchive":True,
    "nonemptyPrivateKeyRejectedBeforeArchive":True,
    "emptyReverseAndReapply":True,"readyAuthorized":False,
    "productionWrites":False}
(folder/"business-v11-ticket-sign-upgrade-evidence.json").write_text(
    json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False))
