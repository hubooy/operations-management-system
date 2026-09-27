"""Exact ERP source metadata index; no automatic fact scan or initialization."""
from django.db import migrations, models


def seed(apps, schema_editor):
    apps.get_model("sales", "SalesAnalysisOptionsState").objects.get_or_create(id=1)


def reverse_guard(apps, schema_editor):
    if apps.get_model("sales", "SalesAnalysisOption").objects.exists() or apps.get_model("sales", "SalesAnalysisOptionsState").objects.exclude(status="not_ready", generation="").exists():
        raise RuntimeError("ERP source options initialized; explicit verified reset required before reversal")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql": return
    schema_editor.execute('''ALTER TABLE sales_analysis_options ADD CONSTRAINT sales_options_shape_ck CHECK (
      length(platform) BETWEEN 1 AND 200 AND length(shop) BETWEEN 1 AND 200 AND length(channel) BETWEEN 1 AND 200
      AND first_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND last_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
      AND first_date<=last_date AND row_count BETWEEN 1 AND 9007199254740991
      AND entry_digest ~ '^[0-9a-f]{64}$' AND octet_length(entry_json) BETWEEN 1 AND 16384)''')
    schema_editor.execute('''ALTER TABLE sales_analysis_options_state ADD CONSTRAINT sales_options_state_ck CHECK (
      id=1 AND status IN ('not_ready','ready','blocked') AND identity_count BETWEEN 0 AND 10000
      AND stored_bytes BETWEEN 0 AND 16777216 AND source_sales_revision BETWEEN -1 AND 9007199254740991
      AND (status<>'ready' OR (generation ~ '^[0-9a-f]{32}$' AND directory_digest ~ '^[0-9a-f]{64}$'
        AND stored_bytes>=2 AND source_sales_revision>=0 AND reason='')))''')
    schema_editor.execute('''CREATE UNIQUE INDEX sales_options_page_idx ON sales_analysis_options
      (platform COLLATE "C", shop COLLATE "C", channel COLLATE "C")''')


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql": return
    schema_editor.execute('DROP INDEX IF EXISTS sales_options_page_idx')
    schema_editor.execute('ALTER TABLE sales_analysis_options DROP CONSTRAINT IF EXISTS sales_options_shape_ck')
    schema_editor.execute('ALTER TABLE sales_analysis_options_state DROP CONSTRAINT IF EXISTS sales_options_state_ck')


class Migration(migrations.Migration):
    dependencies = [("sales", "0009_postgres_raw_upload_payload")]
    operations = [
        migrations.CreateModel(name="SalesAnalysisOption", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("platform",models.CharField(max_length=200)),("shop",models.CharField(max_length=200)),("channel",models.CharField(max_length=200)),
            ("first_date",models.CharField(max_length=10)),("last_date",models.CharField(max_length=10)),("row_count",models.BigIntegerField()),
            ("entry_json",models.TextField()),("entry_digest",models.CharField(max_length=64)),
        ],options={"db_table":"sales_analysis_options","constraints":[models.UniqueConstraint(fields=("platform","shop","channel"),name="sales_options_identity_uq")]}),
        migrations.CreateModel(name="SalesAnalysisOptionsState",fields=[
            ("id",models.PositiveSmallIntegerField(primary_key=True,default=1,serialize=False)),
            ("status",models.CharField(max_length=16,default="not_ready")),("generation",models.CharField(max_length=64,default="")),
            ("directory_digest",models.CharField(max_length=64,default="")),("identity_count",models.PositiveIntegerField(default=0)),
            ("stored_bytes",models.PositiveIntegerField(default=0)),("source_sales_revision",models.BigIntegerField(default=-1)),
            ("reason",models.CharField(max_length=64,default="not_initialized")),("updated_at",models.DateTimeField(auto_now=True)),
        ],options={"db_table":"sales_analysis_options_state"}),
        migrations.RunPython(seed,migrations.RunPython.noop), migrations.RunPython(install,uninstall),
        migrations.RunPython(migrations.RunPython.noop,reverse_guard),
    ]
