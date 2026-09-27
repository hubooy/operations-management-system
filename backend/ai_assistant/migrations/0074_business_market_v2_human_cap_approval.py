"""Default-closed administrator CNY ceiling for one 0065 requirement ledger.

No tariff is adopted, no funds are reserved, and no provider call is allowed.
The ordinary production migration/backup paths must reject this protected
candidate until its own restricted-role and restore contract is accepted.
"""
import re
from django.db import migrations

from ai_assistant import business_market_v2_human_cap_sql as cap


def _body(definition):
    return definition.split("AS $$", 1)[1].rsplit("$$", 1)[0]


def _constraints(cursor, table):
    cursor.execute("SELECT contype,pg_catalog.pg_get_constraintdef(oid),"
        "confrelid::regclass::text,confdeltype,convalidated "
        "FROM pg_catalog.pg_constraint WHERE conrelid=%s::regclass",
        [table])
    rows = cursor.fetchall()
    keys = ({"PRIMARY KEY (id)", "UNIQUE (ledger_id)"}
        if table == cap.APPROVAL else
        {"PRIMARY KEY (id)", "UNIQUE (approval_id)"})
    fks = ({"ledger_id": "ai_business_market_v2_cost_ledger_candidates",
        "plan_id": "ai_business_market_v2_execution_plans",
        "report_id": "ai_report_runs"} if table == cap.APPROVAL else
        {"approval_id": cap.APPROVAL.removeprefix("public.")})
    checks = ({"id", "owner_version", "model_version",
        "model_config_digest", "ledger_digest", "plan_digest",
        "report_snapshot_digest", "approved_cap_cents", "request_json",
        "request_digest", "expires_at"} if table == cap.APPROVAL else
        {"id", "actor_version", "reason_digest"})
    if (len(rows) != len(keys) + len(fks) + len(checks)
            or {definition for kind, definition, _, _, valid in rows
                if kind in ("p", "u") and valid} != keys):
        raise RuntimeError("0074 protected cap key/constraint drift")
    seen_fks, seen_checks = set(), set()
    for kind, definition, target, delete_rule, valid in rows:
        if kind == "f":
            match = re.match(r"FOREIGN KEY \(([^)]+)\) REFERENCES ", definition)
            column = match.group(1) if match else None
            wanted = fks.get(column)
            cursor.execute("SELECT to_regclass(%s)::text",
                ["public." + wanted] if wanted else [None])
            expected_target = cursor.fetchone()[0]
            if (wanted is None or target != expected_target or delete_rule != "r"
                    or not valid or "ON DELETE RESTRICT" not in definition
                    or column in seen_fks):
                raise RuntimeError("0074 protected cap FK drift")
            seen_fks.add(column)
        elif kind == "c":
            names = [name for name in checks
                if re.search(r"\b" + name + r"\b", definition)]
            # expires_at>created_at is the only CHECK naming two columns.
            if len(names) != 1 or not valid or names[0] in seen_checks:
                raise RuntimeError("0074 protected cap CHECK drift")
            name = names[0]
            compact = re.sub(r"[\s()]", "", definition).lower()
            if (name in {"owner_version", "model_version", "actor_version"}
                    and name + ">=1" not in compact
                    or name in {"id", "model_config_digest", "ledger_digest",
                        "plan_digest", "report_snapshot_digest",
                        "request_digest", "reason_digest"}
                    and "^[0-9a-f]{64}$" not in definition
                    or name == "request_json" and
                        "octet_lengthrequest_json<=4096" not in compact
                    or name == "approved_cap_cents" and not (
                        "approved_cap_cents>=1" in compact and
                        "approved_cap_cents<=100000000" in compact or
                        "approved_cap_centsbetween1and100000000" in compact)
                    or name == "expires_at" and
                        "expires_at>created_at" not in compact):
                raise RuntimeError("0074 protected cap CHECK definition drift")
            seen_checks.add(name)
    if seen_fks != set(fks) or seen_checks != checks:
        raise RuntimeError("0074 protected cap constraint missing")


def verify_catalog(cursor):
    """Reject a widened privilege or altered guard/function after install."""
    for table in (cap.APPROVAL, cap.REVOCATION):
        _constraints(cursor, table)
        cursor.execute("SELECT c.relowner FROM pg_catalog.pg_class c "
            "WHERE c.oid=to_regclass(%s)", [table])
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("0074 protected cap table missing")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_class c, "
            "LATERAL aclexplode(c.relacl) acl WHERE c.oid=%s::regclass "
            "AND acl.grantee<>c.relowner", [table])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0074 protected cap table ACL drift")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_attribute a "
            "WHERE a.attrelid=%s::regclass AND a.attnum>0 "
            "AND NOT a.attisdropped AND a.attacl IS NOT NULL", [table])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0074 protected cap column ACL drift")
        cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
            [cap.WRITER, table, "SELECT,INSERT,UPDATE,DELETE,TRUNCATE"])
        if cursor.fetchone() != (False,):
            raise RuntimeError("0074 writer has direct cap table privilege")
        cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
            ["teruisi_ai_reader", table,
             "SELECT,INSERT,UPDATE,DELETE,TRUNCATE"])
        if cursor.fetchone() != (False,):
            raise RuntimeError("0074 reader has cap table privilege")
        cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
            [cap.WRITER, table, "SELECT,INSERT,UPDATE"])
        if cursor.fetchone() != (False,):
            raise RuntimeError("0074 writer has cap column privilege")
        cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
            ["teruisi_ai_reader", table, "SELECT,INSERT,UPDATE"])
        if cursor.fetchone() != (False,):
            raise RuntimeError("0074 reader has cap column privilege")
        cursor.execute("SELECT t.tgname,t.tgenabled,t.tgtype,t.tgfoid,t.tgqual "
            "FROM pg_catalog.pg_trigger t WHERE t.tgrelid=%s::regclass "
            "AND NOT t.tgisinternal", [table])
        triggers = {name: (enabled, kind, oid, predicate)
            for name, enabled, kind, oid, predicate in cursor.fetchall()}
        if set(triggers) != {"ai_market_human_cap_guard",
                "ai_market_human_cap_no_truncate"}:
            raise RuntimeError("0074 cap trigger inventory drift")
        cursor.execute("SELECT to_regprocedure(%s)::oid", [cap.GUARD_SIG])
        guard_oid = cursor.fetchone()[0]
        cursor.execute("SELECT to_regprocedure(%s)::oid",
            ["public.ai_v4_seal_ticket_no_truncate()"])
        truncate_oid = cursor.fetchone()[0]
        if (triggers["ai_market_human_cap_guard"] !=
                ("O", 31, guard_oid, None) or
                triggers["ai_market_human_cap_no_truncate"] !=
                ("O", 34, truncate_oid, None)):
            raise RuntimeError("0074 cap trigger binding drift")
    cursor.execute("SELECT relowner FROM pg_catalog.pg_class WHERE "
        "oid=%s::regclass", [cap.APPROVAL])
    owner = cursor.fetchone()[0]
    cursor.execute("SELECT relowner FROM pg_catalog.pg_class WHERE "
        "oid='public.ai_business_market_v2_cost_ledger_candidates'::regclass")
    if cursor.fetchone() != (owner,):
        raise RuntimeError("0074 owner differs from frozen 0065 ledger owner")
    for signature, definition in (
            (cap.GUARD_SIG, cap.GUARD),
            (cap.MODEL_SIG, cap.MODEL),
            (cap.PREVIEW_SIG, cap.PREVIEW),
            (cap.APPROVE_SIG, cap.APPROVE),
            (cap.REVOKE_SIG, cap.REVOKE),
            (cap.OUTCOME_SIG, cap.OUTCOME)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,p.proowner "
            "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
            [signature])
        state = cursor.fetchone()
        if (state is None or state[0] != _body(definition)
                or state[1] is not (signature != cap.GUARD_SIG)
                or [str(v).replace(" ", "") for v in (state[2] or [])]
                    != ["search_path=pg_catalog,public"]
                or state[3] != owner):
            raise RuntimeError("0074 protected function drift")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_proc p, "
            "LATERAL aclexplode(p.proacl) acl WHERE "
            "p.oid=to_regprocedure(%s) AND acl.grantee<>p.proowner "
            "AND acl.grantee<>%s::regrole", [signature, cap.WRITER])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0074 protected function ACL widened")
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ["teruisi_ai_reader", signature])
        if cursor.fetchone() != (False,):
            raise RuntimeError("0074 reader can execute protected cap function")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_proc p, "
            "LATERAL aclexplode(p.proacl) acl WHERE "
            "p.oid=to_regprocedure(%s) AND acl.grantee=0 "
            "AND acl.privilege_type='EXECUTE'", [signature])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0074 public cap function grant drift")
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
            [cap.WRITER, signature])
        if cursor.fetchone() != (signature in {
                cap.PREVIEW_SIG, cap.APPROVE_SIG,
                cap.REVOKE_SIG, cap.OUTCOME_SIG},):
            raise RuntimeError("0074 writer function grant drift")
    cursor.execute("SELECT relowner FROM pg_catalog.pg_class WHERE "
        "oid=%s::regclass", [cap.REVOCATION])
    if cursor.fetchone() != (owner,):
        raise RuntimeError("0074 cap owner drift")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        from importlib import import_module
        import_module("ai_assistant.migrations.0073_business_promotion_budget_v11_login_attestation").verify_catalog(cursor)
        import_module("ai_assistant.migrations.0072_business_market_v2_authority_proposals").verify_catalog(cursor)
        for table in (cap.APPROVAL, cap.REVOCATION):
            cursor.execute("SELECT to_regclass(%s)", [table])
            if cursor.fetchone()[0] is not None:
                raise RuntimeError("0074 protected cap table already exists")
        cursor.execute("""CREATE TABLE public.protected_business_market_v2_human_cap_approvals (
          id varchar(64) PRIMARY KEY CHECK (id ~ '^[0-9a-f]{64}$'),
          ledger_id varchar(64) NOT NULL UNIQUE REFERENCES
            public.ai_business_market_v2_cost_ledger_candidates(id) ON DELETE RESTRICT,
          plan_id varchar(64) NOT NULL REFERENCES
            public.ai_business_market_v2_execution_plans(id) ON DELETE RESTRICT,
          report_id varchar(160) NOT NULL REFERENCES
            public.ai_report_runs(id) ON DELETE RESTRICT,
          owner_email varchar(320) NOT NULL,
          owner_version bigint NOT NULL CHECK (owner_version>=1),
          model_id varchar(160) NOT NULL,
          model_version bigint NOT NULL CHECK (model_version>=1),
          model_config_digest varchar(64) NOT NULL CHECK (
            model_config_digest ~ '^[0-9a-f]{64}$'),
          ledger_digest varchar(64) NOT NULL CHECK (
            ledger_digest ~ '^[0-9a-f]{64}$'),
          plan_digest varchar(64) NOT NULL CHECK (
            plan_digest ~ '^[0-9a-f]{64}$'),
          report_snapshot_digest varchar(64) NOT NULL CHECK (
            report_snapshot_digest ~ '^[0-9a-f]{64}$'),
          approved_cap_cents bigint NOT NULL CHECK (
            approved_cap_cents BETWEEN 1 AND 100000000),
          expires_at timestamptz NOT NULL,
          request_json text NOT NULL CHECK (octet_length(request_json)<=4096),
          request_digest varchar(64) NOT NULL CHECK (
            request_digest ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL,
          CHECK (expires_at>created_at)
        )""")
        cursor.execute("""CREATE TABLE public.protected_business_market_v2_human_cap_revocations (
          id varchar(64) PRIMARY KEY CHECK (id ~ '^[0-9a-f]{64}$'),
          approval_id varchar(64) NOT NULL UNIQUE REFERENCES
            public.protected_business_market_v2_human_cap_approvals(id)
            ON DELETE RESTRICT,
          actor_email varchar(320) NOT NULL,
          actor_version bigint NOT NULL CHECK (actor_version>=1),
          reason_digest varchar(64) NOT NULL CHECK (
            reason_digest ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL
        )""")
        for table in (cap.APPROVAL, cap.REVOCATION):
            cursor.execute("REVOKE ALL ON TABLE " + table + " FROM PUBLIC")
            for role in (cap.WRITER, "teruisi_ai_reader"):
                cursor.execute("REVOKE ALL ON TABLE " + table + " FROM " + role)
        for definition in (cap.GUARD, cap.MODEL, cap.PREVIEW, cap.APPROVE,
                cap.REVOKE, cap.OUTCOME):
            cursor.execute(definition)
        for signature in (cap.GUARD_SIG, cap.MODEL_SIG, cap.PREVIEW_SIG,
                cap.APPROVE_SIG,
                cap.REVOKE_SIG, cap.OUTCOME_SIG):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            cursor.execute("REVOKE ALL ON FUNCTION " + signature +
                " FROM teruisi_ai_reader,teruisi_ai_writer")
        for signature in (cap.PREVIEW_SIG, cap.APPROVE_SIG,
                cap.REVOKE_SIG, cap.OUTCOME_SIG):
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                " TO " + cap.WRITER)
        for table in (cap.APPROVAL, cap.REVOCATION):
            cursor.execute("CREATE TRIGGER ai_market_human_cap_guard "
                "BEFORE INSERT OR UPDATE OR DELETE ON " + table +
                " FOR EACH ROW EXECUTE FUNCTION " + cap.GUARD_SIG)
            cursor.execute("CREATE TRIGGER ai_market_human_cap_no_truncate "
                "BEFORE TRUNCATE ON " + table +
                " FOR EACH STATEMENT EXECUTE FUNCTION "
                "public.ai_v4_seal_ticket_no_truncate()")
        verify_catalog(cursor)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in (cap.REVOCATION, cap.APPROVAL):
            cursor.execute("SELECT EXISTS(SELECT 1 FROM " + table + ")")
            if cursor.fetchone() != (False,):
                raise RuntimeError("0074 cannot reverse recorded cap approval")
            cursor.execute("DROP TRIGGER ai_market_human_cap_guard ON " + table)
            cursor.execute("DROP TRIGGER ai_market_human_cap_no_truncate ON " + table)
        for signature in (cap.OUTCOME_SIG, cap.REVOKE_SIG,
                cap.APPROVE_SIG, cap.PREVIEW_SIG,
                cap.MODEL_SIG, cap.GUARD_SIG):
            cursor.execute("DROP FUNCTION " + signature)
        for table in (cap.REVOCATION, cap.APPROVAL):
            cursor.execute("DROP TABLE " + table)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant",
        "0073_business_promotion_budget_v11_login_attestation")]
    operations = [migrations.RunPython(install, uninstall)]
