"""Closed append-only digest sidecar; legacy finance facts are untouched."""
from django.db import migrations, models
import django.db.models.deletion


TABLES = (
    "finance_raw_column_evidence_months",
    "finance_raw_column_evidence_columns",
    "finance_raw_column_evidence_cells",
)
GUARD = "public.finance_raw_column_evidence_immutable()"
GUARD_BODY = "BEGIN RAISE EXCEPTION 'finance_raw_evidence_append_only'; END"
APP_ROLES = ("teruisi_finance_reader", "teruisi_finance_writer",
    "teruisi_ai_reader", "teruisi_ai_writer")


def verify_catalog(cursor):
    cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,p.proacl,"
        "p.proowner FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
        [GUARD])
    function = cursor.fetchone()
    if (function is None or function[0].strip() != GUARD_BODY
            or function[1] is not False
            or [str(item).replace(" ", "") for item in (function[2] or [])
                if str(item).startswith("search_path=")]
                != ["search_path=pg_catalog,public"]
            or function[3] is None):
        raise RuntimeError("finance raw evidence function body or ACL drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_proc p, "
        "LATERAL aclexplode(p.proacl) acl "
        "WHERE p.oid=to_regprocedure(%s) AND acl.grantee=0 "
        "AND acl.privilege_type='EXECUTE'", [GUARD])
    if cursor.fetchone() != (0,):
        raise RuntimeError("finance raw evidence function is public")
    for table in TABLES:
        cursor.execute("SELECT to_regclass(%s)", ["public." + table])
        if cursor.fetchone()[0] is None:
            raise RuntimeError("finance raw evidence table missing")
        cursor.execute("SELECT t.tgname,t.tgenabled,t.tgtype,t.tgqual,"
            "p.proname,p.prosecdef,p.proconfig,p.proowner FROM pg_catalog.pg_trigger t "
            "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
            "WHERE t.tgrelid=%s::regclass AND NOT t.tgisinternal",
            ["public." + table])
        found = {name: (enabled, kind, predicate, function, definer,
            config, owner) for name, enabled, kind, predicate, function,
            definer, config, owner in cursor.fetchall()}
        if set(found) != {"fin_raw_evidence_immutable_row",
                "fin_raw_evidence_immutable_truncate"} or any(
                state[0] != "O" or state[1] != (27 if name.endswith("_row") else 34)
                or state[2] is not None
                or state[3] != "finance_raw_column_evidence_immutable"
                or state[4] is not False or state[6] != function[4]
                or [str(item).replace(" ", "") for item in (state[5] or [])
                    if str(item).startswith("search_path=")]
                    != ["search_path=pg_catalog,public"]
                for name, state in found.items()):
            raise RuntimeError("finance raw evidence immutable trigger drift")
        cursor.execute("SELECT c.relowner,p.proowner FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_proc p ON p.oid=to_regprocedure(%s) "
            "WHERE c.oid=%s::regclass", [GUARD, "public." + table])
        if cursor.fetchone() != (function[4], function[4]):
            raise RuntimeError("finance raw evidence owner drift")
        cursor.execute("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)",
            [list(APP_ROLES)])
        present_roles = [name for (name,) in cursor.fetchall()]
        for role in present_roles:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE",
                    "TRUNCATE", "REFERENCES", "TRIGGER"):
                cursor.execute("SELECT pg_catalog.has_table_privilege(%s,%s,%s)",
                    [role, "public." + table, privilege])
                if cursor.fetchone() != (False,):
                    raise RuntimeError("finance raw evidence application table ACL drift")
            cursor.execute("SELECT count(*) FROM pg_catalog.pg_attribute a "
                "WHERE a.attrelid=%s::regclass AND a.attnum>0 "
                "AND NOT a.attisdropped AND ("
                "pg_catalog.has_column_privilege(%s,%s,a.attname,'SELECT') OR "
                "pg_catalog.has_column_privilege(%s,%s,a.attname,'INSERT') OR "
                "pg_catalog.has_column_privilege(%s,%s,a.attname,'UPDATE'))",
                ["public." + table, role, "public." + table, role,
                 "public." + table, role, "public." + table])
            if cursor.fetchone() != (0,):
                raise RuntimeError("finance raw evidence application column ACL drift")
    cursor.execute("SELECT count(*) FROM django_migrations WHERE app='finance' "
        "AND name='0005_raw_column_evidence_v2'")
    # During the migration transaction the receipt is not yet inserted.
    if cursor.fetchone()[0] not in (0, 1):
        raise RuntimeError("finance raw evidence migration receipt drift")


def install_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE FUNCTION " + GUARD + " RETURNS trigger "
            "LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$ "
            + GUARD_BODY + " $$")
        cursor.execute("REVOKE ALL ON FUNCTION " + GUARD + " FROM PUBLIC")
        for table in TABLES:
            cursor.execute("REVOKE ALL ON TABLE public." + table + " FROM PUBLIC")
            cursor.execute("CREATE TRIGGER fin_raw_evidence_immutable_row "
                "BEFORE UPDATE OR DELETE ON public." + table + " FOR EACH ROW "
                "EXECUTE FUNCTION " + GUARD)
            cursor.execute("CREATE TRIGGER fin_raw_evidence_immutable_truncate "
                "BEFORE TRUNCATE ON public." + table + " FOR EACH STATEMENT "
                "EXECUTE FUNCTION " + GUARD)
        verify_catalog(cursor)


def uninstall_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute("SELECT EXISTS(SELECT 1 FROM public." + table + ")")
            if cursor.fetchone() != (False,):
                raise RuntimeError("finance raw evidence cannot reverse nonempty sidecar")
            cursor.execute("DROP TRIGGER fin_raw_evidence_immutable_row ON public." + table)
            cursor.execute("DROP TRIGGER fin_raw_evidence_immutable_truncate ON public." + table)
        cursor.execute("DROP FUNCTION " + GUARD)


class Migration(migrations.Migration):
    dependencies = [("finance", "0004_finance_revision_monotonic")]

    operations = [
        migrations.CreateModel(
            name="FinanceRawEvidenceMonth",
            fields=[
                ("id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("month", models.CharField(max_length=7)),
                ("finance_revision", models.BigIntegerField()),
                ("finance_source_digest", models.CharField(max_length=64)),
                ("raw_file_hash", models.CharField(max_length=64)),
                ("batch_content_hash", models.CharField(max_length=64)),
                ("batch_published_state_token", models.CharField(max_length=64)),
                ("candidate_digest", models.CharField(max_length=64)),
                ("evidence_digest", models.CharField(max_length=64)),
                ("header_digest", models.CharField(max_length=64)),
                ("cells_digest", models.CharField(max_length=64)),
                ("column_chain_digest", models.CharField(max_length=64)),
                ("cell_chain_digest", models.CharField(max_length=64)),
                ("collision_digest", models.CharField(max_length=64)),
                ("column_count", models.PositiveIntegerField()),
                ("cell_count", models.PositiveIntegerField()),
                ("cross_group_same_name_risk", models.BooleanField(default=False)),
                ("ambiguity_flags_json", models.JSONField(default=list)),
                ("raw_workbook_bytes_verified", models.BooleanField(default=False)),
                ("stable_netshop_shop_identity_verified", models.BooleanField(default=False)),
                ("mapping_authority_verified", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("batch", models.ForeignKey(db_column="batch_id",
                    on_delete=django.db.models.deletion.PROTECT,
                    to="finance.financeimportbatch")),
            ],
            options={"db_table": "finance_raw_column_evidence_months",
                "constraints": [
                    models.UniqueConstraint(fields=("month", "batch"),
                        name="fin_raw_month_batch_uq"),
                    models.CheckConstraint(condition=models.Q(raw_workbook_bytes_verified=False),
                        name="fin_raw_unverified_bytes"),
                    models.CheckConstraint(condition=models.Q(stable_netshop_shop_identity_verified=False),
                        name="fin_raw_unverified_shop"),
                    models.CheckConstraint(condition=models.Q(mapping_authority_verified=False),
                        name="fin_raw_unverified_mapping"),
                ]},
        ),
        migrations.CreateModel(
            name="FinanceRawEvidenceColumn",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("column_index", models.PositiveIntegerField()),
                ("column_digest", models.CharField(max_length=64)),
                ("evidence", models.ForeignKey(db_column="evidence_id",
                    on_delete=django.db.models.deletion.PROTECT,
                    to="finance.financerawevidencemonth")),
            ],
            options={"db_table": "finance_raw_column_evidence_columns",
                "constraints": [models.UniqueConstraint(
                    fields=("evidence", "column_index"), name="fin_raw_column_uq")]},
        ),
        migrations.CreateModel(
            name="FinanceRawEvidenceCell",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("section", models.CharField(max_length=32)),
                ("row_index", models.PositiveIntegerField()),
                ("column_index", models.PositiveIntegerField()),
                ("cell_digest", models.CharField(max_length=64)),
                ("evidence", models.ForeignKey(db_column="evidence_id",
                    on_delete=django.db.models.deletion.PROTECT,
                    to="finance.financerawevidencemonth")),
            ],
            options={"db_table": "finance_raw_column_evidence_cells",
                "constraints": [models.UniqueConstraint(fields=("evidence", "section",
                    "row_index", "column_index"), name="fin_raw_cell_uq")]},
        ),
        migrations.RunPython(install_guards, uninstall_guards),
    ]
