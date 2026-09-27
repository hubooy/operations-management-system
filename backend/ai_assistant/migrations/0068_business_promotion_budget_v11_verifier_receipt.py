"""Candidate-only, key-isolated v11 verifier MAC; no publication permission.

The key registry is EMPTY after migration. A separate protected verifier must
be provisioned out of band before any receipt can verify. Neither this
migration nor any ordinary AI role can sign an arbitrary body.
"""
import re

from django.db import migrations


KEY_OWNER = "teruisi_ai_budget_v11_key_owner"
PUBLISHER = "teruisi_ai_budget_v11_publisher"
KEY_TABLE = "public.protected_business_budget_v11_verifier_keys"
MAC_SIGNATURE = "public.ai_budget_v11_private_mac_valid(text,text,text)"
VERIFY_SIGNATURE = "public.ai_budget_v11_verify_protected_receipt(text,integer,text,text)"


KEY_GUARD = r"""CREATE FUNCTION public.ai_budget_v11_key_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.key_id !~ '^[A-Za-z0-9_-]{1,64}$'
       OR octet_length(NEW.secret) NOT BETWEEN 32 AND 128
       OR NEW.status<>'active' OR NEW.revoked_at IS NOT NULL
    THEN RAISE EXCEPTION 'ai_budget_v11_key_shape_invalid'; END IF;
    RETURN NEW;
  END IF;
  IF TG_OP='UPDATE' AND OLD.status='active' AND NEW.status='revoked'
     AND OLD.key_id=NEW.key_id AND OLD.secret=NEW.secret
     AND OLD.created_at=NEW.created_at AND NEW.revoked_at IS NOT NULL
  THEN RETURN NEW; END IF;
  RAISE EXCEPTION 'ai_budget_v11_key_immutable';
END $$"""


MAC = r"""CREATE FUNCTION public.ai_budget_v11_private_mac_valid(
  selected_key text, receipt_text text, receipt_mac text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE secret_bytes bytea; block bytea; inner_pad bytea;
  outer_pad bytea; expected bytea; supplied bytea;
  difference integer:=0; i integer;
BEGIN
  IF selected_key IS NULL OR selected_key !~ '^[A-Za-z0-9_-]{1,64}$'
     OR receipt_text IS NULL OR octet_length(receipt_text) NOT BETWEEN 1 AND 262144
     OR receipt_mac IS NULL OR receipt_mac !~ '^[0-9a-f]{64}$'
  THEN RETURN false; END IF;
  SELECT item.secret INTO secret_bytes FROM
    public.protected_business_budget_v11_verifier_keys item
    WHERE item.key_id=selected_key AND item.status='active';
  IF secret_bytes IS NULL THEN RETURN false; END IF;
  IF octet_length(secret_bytes)>64 THEN
    block:=sha256(secret_bytes);
  ELSE block:=secret_bytes; END IF;
  block:=block||decode(repeat('00',64-octet_length(block)),'hex');
  inner_pad:=decode(repeat('36',64),'hex');
  outer_pad:=decode(repeat('5c',64),'hex');
  FOR i IN 0..63 LOOP
    inner_pad:=set_byte(inner_pad,i,get_byte(inner_pad,i)#get_byte(block,i));
    outer_pad:=set_byte(outer_pad,i,get_byte(outer_pad,i)#get_byte(block,i));
  END LOOP;
  expected:=sha256(outer_pad||sha256(inner_pad||
    convert_to('teruisi:budget-v11:protected-verifier:v1','UTF8')||
    decode('00','hex')||convert_to(receipt_text,'UTF8')));
  supplied:=decode(receipt_mac,'hex');
  FOR i IN 0..31 LOOP
    difference:=difference|(get_byte(expected,i)#get_byte(supplied,i));
  END LOOP;
  RETURN difference=0;
END $$"""


VERIFY = r"""CREATE FUNCTION public.ai_budget_v11_verify_protected_receipt(
  selected_run text, selected_attempt integer, receipt_text text,
  receipt_mac text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  att public.ai_business_promotion_budget_v11_attestations%ROWTYPE;
  receipt jsonb; assertion jsonb;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_publisher'
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR receipt_text IS NULL OR octet_length(receipt_text) NOT BETWEEN 1 AND 262144
     OR receipt_mac IS NULL OR receipt_mac !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_budget_v11_protected_verifier_unavailable'; END IF;
  receipt:=receipt_text::jsonb;
  IF receipt_text IS DISTINCT FROM public.ai_v4_replay_canonical(receipt)
     OR jsonb_typeof(receipt) IS DISTINCT FROM 'object'
     OR NOT receipt ?& ARRAY['schemaVersion','purpose','keyId','runId',
       'attempt','runVersion','reportId','ownerEmail','bindingDigest',
       'attestationId','attestationSha256','attestation']
     OR receipt-ARRAY['schemaVersion','purpose','keyId','runId',
       'attempt','runVersion','reportId','ownerEmail','bindingDigest',
       'attestationId','attestationSha256','attestation']<>'{}'::jsonb
     OR receipt->>'schemaVersion' IS DISTINCT FROM
       'business-promotion-budget-v11-protected-verifier-receipt-v1'
     OR receipt->>'purpose' IS DISTINCT FROM
       'teruisi:business-promotion-budget-v11:stage-to-ready-once:v1'
     OR receipt->>'runId' IS DISTINCT FROM selected_run
     OR receipt->>'attempt' IS DISTINCT FROM selected_attempt::text
     OR jsonb_typeof(receipt->'attempt') IS DISTINCT FROM 'number'
  THEN RAISE EXCEPTION 'ai_budget_v11_protected_receipt_shape_invalid'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR SHARE;
  SELECT * INTO att FROM public.ai_business_promotion_budget_v11_attestations item
    WHERE item.run_id=selected_run AND item.attempt=selected_attempt FOR SHARE;
  IF parent.id IS NULL OR att.id IS NULL OR parent.renderer_version<>11
     OR parent.status<>'paused' OR parent.error_code<>'renderer_unpublished'
     OR parent.draft OR parent.attempt<>selected_attempt
     OR parent.progress_json::jsonb IS DISTINCT FROM jsonb_build_object(
       'stage','staged_unpublished','attempt',selected_attempt)
     OR parent.version::text IS DISTINCT FROM receipt->>'runVersion'
     OR parent.report_id IS DISTINCT FROM receipt->>'reportId'
     OR parent.owner_email IS DISTINCT FROM receipt->>'ownerEmail'
     OR parent.binding_digest IS DISTINCT FROM receipt->>'bindingDigest'
     OR parent.binding_digest IS DISTINCT FROM att.binding_digest
     OR encode(sha256(convert_to(parent.manifest_json,'UTF8')),'hex')
        IS DISTINCT FROM att.compact_json_sha256
     OR att.id IS DISTINCT FROM receipt->>'attestationId'
     OR att.attestation_sha256 IS DISTINCT FROM receipt->>'attestationSha256'
     OR encode(sha256(convert_to(att.attestation_json,'UTF8')),'hex')
        IS DISTINCT FROM att.attestation_sha256
     OR receipt->'attestation' IS DISTINCT FROM att.attestation_json::jsonb
     OR att.report_id IS DISTINCT FROM parent.report_id
     OR att.owner_email IS DISTINCT FROM parent.owner_email
  THEN RAISE EXCEPTION 'ai_budget_v11_protected_receipt_root_changed'; END IF;
  assertion:=att.attestation_json::jsonb;
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.id=parent.report_id FOR SHARE;
  SELECT * INTO flow FROM public.ai_workflow_runs item
    WHERE item.id=report.workflow_id FOR SHARE;
  IF report.id IS NULL OR flow.id IS NULL OR flow.status<>'completed'
     OR flow.completed_at IS NULL
     OR encode(sha256(convert_to(report.snapshot_json,'UTF8')),'hex')
        IS DISTINCT FROM assertion->>'reportSnapshotSha256'
     OR encode(sha256(convert_to(flow.input_json,'UTF8')),'hex')
        IS DISTINCT FROM assertion->>'workflowInputSha256'
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users actor
       WHERE actor.email=parent.owner_email AND actor.role='admin'
         AND actor.status='active' AND actor.scope IS NULL
         AND actor.version::text=assertion->>'actorVersion')
     OR (report.budget_plan_id IS NOT NULL) IS DISTINCT FROM
        att.budget_present
     OR (report.budget_plan_id IS NOT NULL AND
       (report.snapshot_json::jsonb#>>'{budgetRef,planDigest}')
         IS DISTINCT FROM att.budget_plan_digest)
  THEN RAISE EXCEPTION 'ai_budget_v11_protected_receipt_source_changed'; END IF;
  IF NOT public.ai_budget_v11_private_mac_valid(receipt->>'keyId',
      receipt_text,receipt_mac)
  THEN RAISE EXCEPTION 'ai_budget_v11_protected_receipt_mac_invalid'; END IF;
  RETURN true;
END $$"""


def _role(cursor, name):
    cursor.execute("SELECT to_regrole(%s)", [name])
    if cursor.fetchone()[0] is None:
        cursor.execute("CREATE ROLE " + name + " NOLOGIN NOINHERIT NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [name])
    if cursor.fetchone() != (False,) * 7:
        raise RuntimeError("0068 protected verifier role is not isolated")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole", [name, name])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0068 protected verifier role has members")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        installer = cursor.fetchone()[0]
        quoted_installer = schema_editor.connection.ops.quote_name(installer)
        for name in (KEY_OWNER, PUBLISHER):
            _role(cursor, name)
            cursor.execute("GRANT USAGE ON SCHEMA public TO " + name)
        cursor.execute("""CREATE TABLE public.protected_business_budget_v11_verifier_keys (
          key_id varchar(64) PRIMARY KEY,
          secret bytea NOT NULL CHECK (octet_length(secret) BETWEEN 32 AND 128),
          status varchar(8) NOT NULL CHECK (status IN ('active','revoked')),
          created_at timestamptz NOT NULL,
          revoked_at timestamptz,
          CHECK ((status='active' AND revoked_at IS NULL) OR
                 (status='revoked' AND revoked_at IS NOT NULL))
        )""")
        cursor.execute("CREATE UNIQUE INDEX ai_budget_v11_one_active_key ON " +
            KEY_TABLE + " ((status)) WHERE status='active'")
        cursor.execute("REVOKE ALL ON " + KEY_TABLE + " FROM PUBLIC")
        cursor.execute(KEY_GUARD)
        cursor.execute("REVOKE ALL ON FUNCTION public.ai_budget_v11_key_guard() "
            "FROM PUBLIC")
        cursor.execute("CREATE TRIGGER ai_budget_v11_key_guard BEFORE INSERT OR "
            "UPDATE OR DELETE ON " + KEY_TABLE + " FOR EACH ROW EXECUTE "
            "FUNCTION public.ai_budget_v11_key_guard()")
        cursor.execute("CREATE TRIGGER ai_budget_v11_key_no_truncate BEFORE "
            "TRUNCATE ON " + KEY_TABLE + " FOR EACH STATEMENT EXECUTE "
            "FUNCTION public.ai_v4_seal_ticket_no_truncate()")
        cursor.execute(MAC)
        cursor.execute("REVOKE ALL ON FUNCTION " + MAC_SIGNATURE + " FROM PUBLIC")
        # The installation role is temporarily a member only while changing
        # ownership. No membership survives commit; the key is never returned.
        cursor.execute("GRANT " + KEY_OWNER + " TO " + quoted_installer)
        cursor.execute("ALTER TABLE " + KEY_TABLE + " OWNER TO " + KEY_OWNER)
        cursor.execute("ALTER FUNCTION public.ai_budget_v11_key_guard() OWNER TO " +
            KEY_OWNER)
        cursor.execute("ALTER FUNCTION " + MAC_SIGNATURE + " OWNER TO " + KEY_OWNER)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + MAC_SIGNATURE +
            " TO " + quoted_installer)
        cursor.execute("REVOKE " + KEY_OWNER + " FROM " + quoted_installer)
        cursor.execute(VERIFY)
        cursor.execute("REVOKE ALL ON FUNCTION " + VERIFY_SIGNATURE + " FROM PUBLIC")
        cursor.execute("GRANT EXECUTE ON FUNCTION " + VERIFY_SIGNATURE +
            " TO " + PUBLISHER)
        for role in ("teruisi_ai_reader", "teruisi_ai_writer",
                "teruisi_ai_budget_v11_attestor", PUBLISHER):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + KEY_TABLE + " FROM " + role)
                cursor.execute("REVOKE ALL ON FUNCTION " + MAC_SIGNATURE +
                    " FROM " + role)
        cursor.execute("SELECT count(*) FROM " + KEY_TABLE)
        if cursor.fetchone() != (0,):
            raise RuntimeError("0068 must install with no verifier key")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + KEY_TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0068 cannot discard provisioned verifier keys")
        cursor.execute("SELECT current_user")
        installer = schema_editor.connection.ops.quote_name(cursor.fetchone()[0])
        cursor.execute("GRANT " + KEY_OWNER + " TO " + installer)
        cursor.execute("DROP FUNCTION " + VERIFY_SIGNATURE)
        cursor.execute("DROP FUNCTION " + MAC_SIGNATURE)
        cursor.execute("DROP TRIGGER ai_budget_v11_key_no_truncate ON " + KEY_TABLE)
        cursor.execute("DROP TRIGGER ai_budget_v11_key_guard ON " + KEY_TABLE)
        cursor.execute("DROP FUNCTION public.ai_budget_v11_key_guard()")
        cursor.execute("DROP TABLE " + KEY_TABLE)
        cursor.execute("REVOKE " + KEY_OWNER + " FROM " + installer)
        for role in (KEY_OWNER, PUBLISHER):
            cursor.execute("REVOKE USAGE ON SCHEMA public FROM " + role)
        # NOLOGIN role names remain as an audit record, with no grants.


def verify_catalog(cursor):
    """Pin the candidate's private key ownership and denied role surface."""
    for role in (KEY_OWNER, PUBLISHER):
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [role])
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0068 verifier role drift")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
            "roleid=%s::regrole OR member=%s::regrole", [role, role])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0068 verifier membership drift")
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(relowner),relkind "
        "FROM pg_catalog.pg_class WHERE oid=%s::regclass", [KEY_TABLE])
    if cursor.fetchone() != (KEY_OWNER, "r"):
        raise RuntimeError("0068 private key table owner drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_class c "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
        "pg_catalog.acldefault('r',c.relowner))) acl "
        "WHERE c.oid=%s::regclass AND acl.grantee<>c.relowner", [KEY_TABLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0068 private key table grant drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "CROSS JOIN LATERAL pg_catalog.aclexplode("
        "COALESCE(a.attacl,pg_catalog.acldefault('c',c.relowner))) acl "
        "WHERE c.oid=%s::regclass AND a.attnum>0 "
        "AND acl.grantee<>c.relowner", [KEY_TABLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0068 private key column grant drift")
    cursor.execute("SELECT attname,atttypid::regtype::text,atttypmod,attnotnull "
        "FROM pg_catalog.pg_attribute WHERE attrelid=%s::regclass "
        "AND attnum>0 AND NOT attisdropped ORDER BY attnum", [KEY_TABLE])
    if cursor.fetchall() != [
            ("key_id", "character varying", 68, True),
            ("secret", "bytea", -1, True),
            ("status", "character varying", 12, True),
            ("created_at", "timestamp with time zone", -1, True),
            ("revoked_at", "timestamp with time zone", -1, False)]:
        raise RuntimeError("0068 private key column drift")
    cursor.execute("SELECT conname,contype,convalidated,condeferrable,"
        "pg_catalog.pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint "
        "WHERE conrelid=%s::regclass", [KEY_TABLE])
    constraints = {name: (kind, valid, deferred, definition)
        for name, kind, valid, deferred, definition in cursor.fetchall()}
    prefix = "protected_business_budget_v11_verifier_keys"
    if set(constraints) != {prefix + suffix for suffix in (
            "_pkey", "_secret_check", "_status_check", "_check")}:
        raise RuntimeError("0068 private key constraint drift")
    if constraints[prefix + "_pkey"] != ("p", True, False, "PRIMARY KEY (key_id)"):
        raise RuntimeError("0068 private key primary key drift")
    for suffix, required in (
            ("_secret_check", ("octet_length(secret)", "32", "128")),
            ("_status_check", ("status", "active", "revoked")),
            ("_check", ("status", "active", "revoked", "revoked_at",
                "IS NULL", "IS NOT NULL"))):
        kind, valid, deferred, definition = constraints[prefix + suffix]
        if (kind != "c" or not valid or deferred or
                any(part not in definition for part in required)):
            raise RuntimeError("0068 private key check drift")
    cursor.execute("SELECT i.indisunique,i.indisvalid,i.indisready,i.indislive,"
        "i.indnkeyatts,i.indnatts,i.indkey::text,"
        "pg_catalog.pg_get_expr(i.indpred,i.indrelid),"
        "pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c "
        "ON c.oid=i.indexrelid WHERE c.oid=to_regclass(%s) "
        "AND i.indrelid=%s::regclass AND c.relkind='i'",
        ["public.ai_budget_v11_one_active_key", KEY_TABLE])
    index = cursor.fetchone()
    if index is None:
        raise RuntimeError("0068 active key unique index missing")
    predicate = (re.sub(r"::(?:character varying|text)|[()\s]", "", index[7])
        if isinstance(index[7], str) else None)
    if (index[:7] != (True, True, True, True, 1, 1, "3") or
            predicate != "status='active'" or index[8] != KEY_OWNER):
        raise RuntimeError("0068 active key unique index drift")
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(proowner),prosrc,"
        "prosecdef,proconfig FROM pg_catalog.pg_proc "
        "WHERE oid=to_regprocedure('public.ai_budget_v11_key_guard()')")
    guard = cursor.fetchone()
    if (guard is None or guard[0] != KEY_OWNER or
            guard[1] != KEY_GUARD.split("$$", 2)[1] or guard[2] is not False or
            {item.replace(" ", "") for item in (guard[3] or [])} !=
                {"search_path=pg_catalog,public"}):
        raise RuntimeError("0068 private key guard function drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_proc p "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,"
        "pg_catalog.acldefault('f',p.proowner))) acl "
        "WHERE p.oid=to_regprocedure('public.ai_budget_v11_key_guard()') "
        "AND acl.grantee<>p.proowner")
    if cursor.fetchone() != (0,):
        raise RuntimeError("0068 private key guard grant drift")
    cursor.execute("SELECT t.tgname,t.tgenabled,t.tgdeferrable,"
        "t.tginitdeferred,t.tgfoid,t.tgtype FROM pg_catalog.pg_trigger t "
        "WHERE t.tgrelid=%s::regclass AND NOT t.tgisinternal", [KEY_TABLE])
    triggers = {name: (enabled, deferred, initially, oid, event_bits)
        for name, enabled, deferred, initially, oid, event_bits in
            cursor.fetchall()}
    expected_triggers = {
        "ai_budget_v11_key_guard": ("public.ai_budget_v11_key_guard()", 31),
        "ai_budget_v11_key_no_truncate":
            ("public.ai_v4_seal_ticket_no_truncate()", 34)}
    if set(triggers) != set(expected_triggers):
        raise RuntimeError("0068 private key trigger drift")
    for name, (signature, event_bits) in expected_triggers.items():
        cursor.execute("SELECT to_regprocedure(%s)::oid", [signature])
        if triggers[name] != ("O", False, False, cursor.fetchone()[0],
                event_bits):
            raise RuntimeError("0068 private key trigger binding drift")
    for signature, owner, body in ((MAC_SIGNATURE, KEY_OWNER, MAC),
            (VERIFY_SIGNATURE, None, VERIFY)):
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(proowner),prosrc,"
            "prosecdef,proconfig FROM pg_catalog.pg_proc "
            "WHERE oid=to_regprocedure(%s)", [signature])
        value = cursor.fetchone()
        if (value is None or (owner is not None and value[0] != owner)
                or (owner is None and value[0] in {KEY_OWNER, PUBLISHER,
                    "teruisi_ai_budget_v11_attestor", "teruisi_ai_writer",
                    "teruisi_ai_reader"}) or
                value[1] != body.split("$$", 2)[1] or value[2] is not True or
                {item.replace(" ", "") for item in (value[3] or [])} !=
                    {"search_path=pg_catalog,public"}):
            raise RuntimeError("0068 verifier function drift")
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(p.proowner) "
        "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
        [VERIFY_SIGNATURE])
    verify_owner = cursor.fetchone()[0]
    for signature, allowed in ((MAC_SIGNATURE, {KEY_OWNER, verify_owner}),
            (VERIFY_SIGNATURE, {verify_owner, PUBLISHER})):
        cursor.execute("SELECT DISTINCT pg_catalog.pg_get_userbyid(acl.grantee) "
            "FROM pg_catalog.pg_proc p CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) acl "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        if {row[0] for row in cursor.fetchall()} != allowed:
            raise RuntimeError("0068 verifier function grant drift")
    for role in (PUBLISHER, "teruisi_ai_budget_v11_attestor",
            "teruisi_ai_writer", "teruisi_ai_reader"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE",
                "TRUNCATE", "REFERENCES", "TRIGGER"):
            cursor.execute("SELECT pg_catalog.has_table_privilege(%s,%s,%s)",
                [role, KEY_TABLE, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0068 private key table ACL drift")
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            cursor.execute("SELECT pg_catalog.has_any_column_privilege(%s,%s,%s)",
                [role, KEY_TABLE, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0068 private key column ACL drift")
        for signature, allowed in ((MAC_SIGNATURE, False),
                (VERIFY_SIGNATURE, role == PUBLISHER)):
            cursor.execute("SELECT pg_catalog.has_function_privilege(%s,%s,"
                "'EXECUTE')", [role, signature])
            if cursor.fetchone() != (allowed,):
                raise RuntimeError("0068 verifier function ACL drift")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant",
        "0067_business_promotion_budget_v11_attestation")]
    operations = [migrations.RunPython(install, uninstall)]
