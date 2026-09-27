"""Default-empty, SQL-owned rate/FX and human-cap proposals; no paid grant."""
import re
from django.db import migrations

from ai_assistant import business_market_v2_authority_sql as authority


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        from importlib import import_module
        import_module("ai_assistant.migrations.0069_business_market_v2_paid_round_rehearsal").verify_catalog(cursor)
        import_module("ai_assistant.migrations.0071_business_v4_report_source_link").verify_catalog(cursor)
        for table in (authority.RATE, authority.CAP, authority.REVOKE):
            cursor.execute("SELECT to_regclass(%s)", [table])
            if cursor.fetchone()[0] is not None:
                raise RuntimeError("0072 authority proposal table already exists")
        for role in (authority.RATE_ROLE, authority.CAP_ROLE,
                     authority.REVOKE_ROLE):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is None:
                cursor.execute("CREATE ROLE " + role + " NOLOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
            cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
                "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname=%s", [role])
            if cursor.fetchone() != (False,) * 7:
                raise RuntimeError("0072 authority proposal role widened")
            cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
                "roleid=%s::regrole OR member=%s::regrole", [role, role])
            if cursor.fetchone() != (0,):
                raise RuntimeError("0072 authority proposal role membership drift")
            cursor.execute("GRANT USAGE ON SCHEMA public TO " + role)
        cursor.execute("""CREATE TABLE public.protected_business_market_v2_rate_proposals (
          id varchar(64) PRIMARY KEY,
          plan_id varchar(64) NOT NULL UNIQUE REFERENCES
            public.ai_business_market_v2_execution_plans(id) ON DELETE RESTRICT,
          cost_ledger_id varchar(64) NOT NULL UNIQUE REFERENCES
            public.ai_business_market_v2_cost_ledger_candidates(id) ON DELETE RESTRICT,
          report_id varchar(160) NOT NULL REFERENCES
            public.ai_report_runs(id) ON DELETE RESTRICT,
          owner_email varchar(320) NOT NULL,
          owner_version bigint NOT NULL CHECK (owner_version>=1),
          model_id varchar(160) NOT NULL,
          model_version bigint NOT NULL CHECK (model_version>=1),
          proposal_json text NOT NULL CHECK (octet_length(proposal_json)<=16384),
          proposal_digest varchar(64) NOT NULL CHECK (
            proposal_digest ~ '^[0-9a-f]{64}$'),
          status varchar(64) NOT NULL CHECK (
            status='pending_independent_source_verification'),
          created_at timestamptz NOT NULL
        )""")
        cursor.execute("""CREATE TABLE public.protected_business_market_v2_cap_proposals (
          id varchar(64) PRIMARY KEY,
          rate_id varchar(64) NOT NULL UNIQUE REFERENCES
            public.protected_business_market_v2_rate_proposals(id) ON DELETE RESTRICT,
          plan_id varchar(64) NOT NULL UNIQUE REFERENCES
            public.ai_business_market_v2_execution_plans(id) ON DELETE RESTRICT,
          cost_ledger_id varchar(64) NOT NULL REFERENCES
            public.ai_business_market_v2_cost_ledger_candidates(id) ON DELETE RESTRICT,
          report_id varchar(160) NOT NULL REFERENCES
            public.ai_report_runs(id) ON DELETE RESTRICT,
          owner_email varchar(320) NOT NULL,
          owner_version bigint NOT NULL CHECK (owner_version>=1),
          cap_claim_cents bigint NOT NULL CHECK (cap_claim_cents>0),
          proposal_json text NOT NULL CHECK (octet_length(proposal_json)<=16384),
          proposal_digest varchar(64) NOT NULL CHECK (
            proposal_digest ~ '^[0-9a-f]{64}$'),
          status varchar(64) NOT NULL CHECK (status='pending_explicit_human_approval'),
          created_at timestamptz NOT NULL
        )""")
        cursor.execute("""CREATE TABLE public.protected_business_market_v2_authority_revocations (
          id varchar(64) PRIMARY KEY,
          target_kind varchar(8) NOT NULL CHECK (target_kind IN ('rate','cap')),
          target_id varchar(64) NOT NULL,
          reason_digest varchar(64) NOT NULL CHECK (
            reason_digest ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL,
          UNIQUE (target_kind,target_id)
        )""")
        for table in (authority.RATE, authority.CAP, authority.REVOKE):
            cursor.execute("REVOKE ALL ON TABLE " + table + " FROM PUBLIC")
            for role in (authority.RATE_ROLE, authority.CAP_ROLE,
                         authority.REVOKE_ROLE, authority.READER,
                         authority.WRITER):
                cursor.execute("REVOKE ALL ON TABLE " + table + " FROM " + role)
        for definition in (authority.GUARD, authority.RATE_SQL,
                authority.CAP_SQL, authority.REVOKE_SQL, authority.READ_SQL):
            cursor.execute(definition)
        for signature in ("public.ai_market_v2_authority_proposal_guard()",
                authority.RATE_SIG, authority.CAP_SIG, authority.REVOKE_SIG,
                authority.READ_SIG):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            for role in (authority.RATE_ROLE, authority.CAP_ROLE,
                         authority.REVOKE_ROLE, authority.READER,
                         authority.WRITER):
                cursor.execute("REVOKE ALL ON FUNCTION " + signature +
                    " FROM " + role)
        for signature, role in ((authority.RATE_SIG, authority.RATE_ROLE),
                (authority.CAP_SIG, authority.CAP_ROLE),
                (authority.REVOKE_SIG, authority.REVOKE_ROLE),
                (authority.READ_SIG, authority.READER)):
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature+
                " TO " + role)
        for table, prefix in ((authority.RATE, "rate"),
                (authority.CAP, "cap"), (authority.REVOKE, "revoke")):
            cursor.execute("CREATE TRIGGER ai_market_v2_authority_"+prefix+
                "_guard BEFORE INSERT OR UPDATE OR DELETE ON "+table+
                " FOR EACH ROW EXECUTE FUNCTION "
                "public.ai_market_v2_authority_proposal_guard()")
            cursor.execute("CREATE TRIGGER ai_market_v2_authority_"+prefix+
                "_no_truncate BEFORE TRUNCATE ON "+table+
                " FOR EACH STATEMENT EXECUTE FUNCTION "
                "public.ai_v4_seal_ticket_no_truncate()")
        verify_catalog(cursor)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in (authority.RATE, authority.CAP, authority.REVOKE):
            cursor.execute("SELECT EXISTS(SELECT 1 FROM " + table + ")")
            if cursor.fetchone() != (False,):
                raise RuntimeError("0072 cannot discard recorded authority proposals")
        for table, prefix in ((authority.REVOKE, "revoke"),
                (authority.CAP, "cap"), (authority.RATE, "rate")):
            for suffix in ("no_truncate", "guard"):
                cursor.execute("DROP TRIGGER ai_market_v2_authority_"+prefix+
                    "_"+suffix+" ON "+table)
        for signature in (authority.READ_SIG, authority.REVOKE_SIG,
                authority.CAP_SIG, authority.RATE_SIG,
                "public.ai_market_v2_authority_proposal_guard()"):
            cursor.execute("DROP FUNCTION " + signature)
        for table in (authority.REVOKE, authority.CAP, authority.RATE):
            cursor.execute("DROP TABLE " + table)
        # Keep NOLOGIN roles without callable functions.


def verify_catalog(cursor):
    roles = (authority.RATE_ROLE, authority.CAP_ROLE, authority.REVOKE_ROLE)
    for role in roles:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [role])
        if cursor.fetchone() != (False,) * 7:
            raise ValueError("0072 authority role widened")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
            "roleid=%s::regrole OR member=%s::regrole", [role, role])
        if cursor.fetchone() != (0,):
            raise ValueError("0072 authority role membership drift")
    for table, prefix in ((authority.RATE, "rate"),
            (authority.CAP, "cap"), (authority.REVOKE, "revoke")):
        cursor.execute("SELECT relkind,pg_catalog.pg_get_userbyid(relowner) "
            "FROM pg_catalog.pg_class WHERE oid=to_regclass(%s)", [table])
        row = cursor.fetchone()
        if row is None or row[0] != "r":
            raise ValueError("0072 authority table missing")
        columns = {
            authority.RATE: [("id", "character varying(64)"),
                ("plan_id", "character varying(64)"),
                ("cost_ledger_id", "character varying(64)"),
                ("report_id", "character varying(160)"),
                ("owner_email", "character varying(320)"),
                ("owner_version", "bigint"),
                ("model_id", "character varying(160)"),
                ("model_version", "bigint"), ("proposal_json", "text"),
                ("proposal_digest", "character varying(64)"),
                ("status", "character varying(64)"),
                ("created_at", "timestamp with time zone")],
            authority.CAP: [("id", "character varying(64)"),
                ("rate_id", "character varying(64)"),
                ("plan_id", "character varying(64)"),
                ("cost_ledger_id", "character varying(64)"),
                ("report_id", "character varying(160)"),
                ("owner_email", "character varying(320)"),
                ("owner_version", "bigint"), ("cap_claim_cents", "bigint"),
                ("proposal_json", "text"),
                ("proposal_digest", "character varying(64)"),
                ("status", "character varying(64)"),
                ("created_at", "timestamp with time zone")],
            authority.REVOKE: [("id", "character varying(64)"),
                ("target_kind", "character varying(8)"),
                ("target_id", "character varying(64)"),
                ("reason_digest", "character varying(64)"),
                ("created_at", "timestamp with time zone")],
        }[table]
        cursor.execute("SELECT attname,format_type(atttypid,atttypmod),attnotnull "
            "FROM pg_catalog.pg_attribute WHERE attrelid=to_regclass(%s) "
            "AND attnum>0 AND NOT attisdropped ORDER BY attnum", [table])
        if cursor.fetchall() != [(name, kind, True) for name, kind in columns]:
            raise ValueError("0072 authority columns drift")
        for role in (*roles, authority.READER, authority.WRITER):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE",
                              "TRUNCATE", "REFERENCES", "TRIGGER"):
                cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                    [role, table, privilege])
                if cursor.fetchone() != (False,):
                    raise ValueError("0072 authority table privilege widened")
            for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
                cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                    [role, table, privilege])
                if cursor.fetchone() != (False,):
                    raise ValueError("0072 authority column privilege widened")
        cursor.execute("SELECT tgname,tgenabled,tgtype,tgfoid::regprocedure::text "
            "FROM pg_catalog.pg_trigger WHERE tgrelid=to_regclass(%s) "
            "AND NOT tgisinternal", [table])
        found = set(cursor.fetchall())
        cursor.execute("SELECT to_regprocedure(%s)::text", [
            "public.ai_market_v2_authority_proposal_guard()"])
        guard = cursor.fetchone()[0]
        cursor.execute("SELECT to_regprocedure(%s)::text", [
            "public.ai_v4_seal_ticket_no_truncate()"])
        no_truncate = cursor.fetchone()[0]
        if found != {("ai_market_v2_authority_"+prefix+"_guard", "O", 31, guard),
                ("ai_market_v2_authority_"+prefix+"_no_truncate", "O", 34,
                 no_truncate)}:
            raise ValueError("0072 authority trigger drift")
        cursor.execute("SELECT contype,pg_catalog.pg_get_constraintdef(oid),"
            "confrelid::regclass::text,confdeltype,convalidated "
            "FROM pg_catalog.pg_constraint WHERE conrelid=to_regclass(%s)",
            [table])
        constraints = cursor.fetchall()
        keys = {
            authority.RATE: {"PRIMARY KEY (id)", "UNIQUE (plan_id)",
                "UNIQUE (cost_ledger_id)"},
            authority.CAP: {"PRIMARY KEY (id)", "UNIQUE (rate_id)",
                "UNIQUE (plan_id)"},
            authority.REVOKE: {"PRIMARY KEY (id)",
                "UNIQUE (target_kind, target_id)"},
        }[table]
        fks = {
            authority.RATE: {"plan_id":
                "public.ai_business_market_v2_execution_plans",
                "cost_ledger_id":
                    "public.ai_business_market_v2_cost_ledger_candidates",
                "report_id": "public.ai_report_runs"},
            authority.CAP: {"rate_id": authority.RATE,
                "plan_id": "public.ai_business_market_v2_execution_plans",
                "cost_ledger_id":
                    "public.ai_business_market_v2_cost_ledger_candidates",
                "report_id": "public.ai_report_runs"},
            authority.REVOKE: {},
        }[table]
        checks = {authority.RATE: {"owner_version", "model_version",
            "proposal_json", "proposal_digest", "status"},
            authority.CAP: {"owner_version", "cap_claim_cents",
                "proposal_json", "proposal_digest", "status"},
            authority.REVOKE: {"target_kind", "reason_digest"}}[table]
        if (len(constraints) != len(keys) + len(fks) + len(checks)
                or {definition for kind, definition, _, _, valid
                    in constraints if kind in ("p", "u") and valid} != keys):
            raise ValueError("0072 authority key/constraint drift")
        seen_fks = set()
        seen_checks = set()
        for kind, definition, target, delete_rule, valid in constraints:
            if kind == "f":
                match = re.match(r"FOREIGN KEY \(([^)]+)\) REFERENCES ",
                    definition)
                column = match.group(1) if match else None
                wanted = fks.get(column)
                cursor.execute("SELECT to_regclass(%s)::text", [wanted])
                if (wanted is None or target != cursor.fetchone()[0]
                        or delete_rule != "r" or not valid
                        or "ON DELETE RESTRICT" not in definition
                        or column in seen_fks):
                    raise ValueError("0072 authority FK target drift")
                seen_fks.add(column)
            elif kind == "c":
                names = [name for name in checks
                    if re.search(r"\b" + name + r"\b", definition)]
                if len(names) != 1 or not valid or names[0] in seen_checks:
                    raise ValueError("0072 authority CHECK drift")
                name = names[0]
                compact = re.sub(r"[\s()]", "", definition).lower()
                literals = set(re.findall(r"'([^']+)'", definition))
                if (name in ("owner_version", "model_version")
                        and name+">=1" not in compact
                        or name == "cap_claim_cents" and
                        "cap_claim_cents>0" not in compact
                        or name == "proposal_json" and
                        "octet_lengthproposal_json<=16384" not in compact
                        or name in ("proposal_digest", "reason_digest") and
                        "^[0-9a-f]{64}$" not in literals
                        or name == "status" and literals != ({
                            "pending_independent_source_verification"} if
                            table == authority.RATE else {
                            "pending_explicit_human_approval"})
                        or name == "target_kind" and literals != {"rate", "cap"}):
                    raise ValueError("0072 authority CHECK definition drift")
                seen_checks.add(name)
        if seen_fks != set(fks) or seen_checks != checks:
            raise ValueError("0072 authority constraint missing")
    with_owner = (('public.ai_market_v2_authority_proposal_guard()',
        authority.GUARD, False, None),
        (authority.RATE_SIG, authority.RATE_SQL, True, authority.RATE_ROLE),
        (authority.CAP_SIG, authority.CAP_SQL, True, authority.CAP_ROLE),
        (authority.REVOKE_SIG, authority.REVOKE_SQL, True,
            authority.REVOKE_ROLE),
        (authority.READ_SIG, authority.READ_SQL, True, authority.READER))
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(relowner) FROM "
        "pg_catalog.pg_class WHERE oid=to_regclass(%s)", [authority.RATE])
    owner = cursor.fetchone()[0]
    for signature, definition, definer, grant in with_owner:
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner),l.lanname FROM "
            "pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON "
            "l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split('$$', 2)[1]
                or row[1] is not definer or row[3] != owner
                or row[4] != 'plpgsql'
                or {item.replace(' ', '') for item in (row[2] or [])}
                    != {'search_path=pg_catalog,public'}):
            raise ValueError("0072 authority function drift")
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(a.grantee),"
            "a.privilege_type FROM pg_catalog.pg_proc p,"
            "LATERAL pg_catalog.aclexplode(p.proacl) a "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        expected = {(owner, 'EXECUTE')}
        if grant is not None:
            expected.add((grant, 'EXECUTE'))
        if set(cursor.fetchall()) != expected:
            raise ValueError("0072 authority function ACL widened")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0071_business_v4_report_source_link")]
    operations = [migrations.RunPython(install, uninstall)]
