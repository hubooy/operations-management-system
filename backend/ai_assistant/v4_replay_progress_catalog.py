"""Frozen 0047 catalog checks shared by AI readiness and consistent backup."""
from importlib import import_module


TABLE = "public.ai_business_v4_sealer_replay_progress"
SEALER = "teruisi_ai_seal_writer"


def verify(cursor, error_type=ValueError, *, finance_enabled=False,
           read_cast_enabled=False, prior_claim_qualified=False):
    migration = import_module(
        "ai_assistant.migrations.0047_business_v4_sealer_replay_progress")
    finance_migration = (import_module(
        "ai_assistant.migrations.0048_business_v4_finance_replay_progress")
        if finance_enabled else None)
    read_cast_migration = (import_module(
        "ai_assistant.migrations.0050_business_v4_replay_read_cast")
        if read_cast_enabled else None)
    prior_claim_migration = (import_module(
        "ai_assistant.migrations.0051_business_v4_prior_claim_column")
        if prior_claim_qualified else None)

    def need(condition, reason):
        if not condition:
            raise error_type("AI v4 replay progress " + reason)

    if finance_enabled:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
            "app='ai_assistant' AND name='0048_business_v4_finance_replay_progress')")
        need(cursor.fetchone() == (True,), "finance extension receipt missing")
    if read_cast_enabled:
        need(finance_enabled, "read cast requires finance replay predecessor")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
            "app='ai_assistant' AND name='0050_business_v4_replay_read_cast')")
        need(cursor.fetchone() == (True,), "read cast receipt missing")
    if prior_claim_qualified:
        need(read_cast_enabled, "qualified prior claim requires read cast predecessor")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
            "app='ai_assistant' AND "
            "name='0051_business_v4_prior_claim_column')")
        need(cursor.fetchone() == (True,), "qualified prior claim receipt missing")

    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [SEALER])
    need(cursor.fetchone() == (False,) * 7, "sealer role is not closed")

    cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c WHERE c.oid=to_regclass(%s)", [TABLE])
    owner = cursor.fetchone()
    need(owner is not None and owner[0] not in {
        "teruisi_ai_reader", "teruisi_ai_writer", SEALER}, "table owner drift")
    for role in ("teruisi_ai_reader", "teruisi_ai_writer", SEALER):
        cursor.execute("SELECT to_regrole(%s)", [role])
        if cursor.fetchone()[0] is None:
            need(role != SEALER, "sealer role missing")
            continue
        cursor.execute("SELECT has_any_column_privilege(%s,%s,'SELECT'),"
            "has_any_column_privilege(%s,%s,'INSERT'),"
            "has_any_column_privilege(%s,%s,'UPDATE'),"
            "has_table_privilege(%s,%s,'DELETE'),"
            "has_table_privilege(%s,%s,'TRUNCATE')",
            [role, TABLE] * 5)
        need(cursor.fetchone() == (False,) * 5, "table ACL drift")

    definitions = (
        (migration.CANONICAL, migration.CANONICAL_SQL, False,
         "search_path=pg_catalog", (False, False, False)),
        ("public.ai_v4_sealer_replay_progress_guard()", migration.GUARD,
         False, "search_path=pg_catalog,public", (False, False, False)),
        (migration.WRITE, prior_claim_migration.RECORD if prior_claim_migration
         else finance_migration.RECORD if finance_migration else migration.RECORD, True,
         "search_path=pg_catalog,public", (True, False, False)),
        (migration.READ, read_cast_migration.READ if read_cast_migration else migration.READ_SQL, True,
         "search_path=pg_catalog,public", (True, False, False)),
    )
    for signature, definition, security_definer, search_path, allowed in definitions:
        cursor.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,"
            "l.lanname,pg_catalog.pg_get_userbyid(p.proowner) "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
            "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        need(row is not None and row[1] == definition.split("$$")[1]
             and row[2] is security_definer and row[4] == "plpgsql"
             and row[5] not in {"teruisi_ai_reader", "teruisi_ai_writer", SEALER}
             and {item.replace(" ", "") for item in (row[3] or [])}
                == {search_path}, "function body or ownership drift")
        for role, expected in zip((SEALER, "teruisi_ai_writer",
                                   "teruisi_ai_reader"), allowed):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is None:
                need(role != SEALER and expected is False,
                     "required function role missing")
                continue
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                [role, row[0]])
            need(cursor.fetchone() == (expected,), "function ACL drift")

    cursor.execute("SELECT t.tgname,t.tgtype,t.tgenabled,t.tgfoid "
        "FROM pg_catalog.pg_trigger t WHERE t.tgrelid=to_regclass(%s) "
        "AND NOT t.tgisinternal", [TABLE])
    triggers = {name: (kind, enabled, oid)
        for name, kind, enabled, oid in cursor.fetchall()}
    need(set(triggers) == {"ai_v4_replay_progress_guard",
        "ai_v4_replay_progress_no_truncate"}, "trigger inventory drift")
    for name, kind, function in (
        ("ai_v4_replay_progress_guard", 31,
         "public.ai_v4_sealer_replay_progress_guard()"),
        ("ai_v4_replay_progress_no_truncate", 34,
         "public.ai_v4_seal_ticket_no_truncate()"),
    ):
        cursor.execute("SELECT to_regprocedure(%s)::oid", [function])
        target = cursor.fetchone()
        need(target is not None and triggers[name] == (kind, "O", target[0]),
             "trigger binding drift")

    cursor.execute("SELECT pg_catalog.pg_get_constraintdef(c.oid) "
        "FROM pg_catalog.pg_constraint c WHERE c.conrelid=to_regclass(%s) "
        "AND c.contype='u'", [TABLE])
    unique = [row[0] for row in cursor.fetchall()]
    need(unique == ["UNIQUE (attempt_id, source_id, segment_index)"],
         "attempt/source/segment uniqueness drift")

    cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
        [SEALER,
         "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)"])
    need(cursor.fetchone() == (False,), "direct seal commit reopened")
