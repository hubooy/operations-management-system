"""Candidate metadata tables only. Historical data is never auto-initialized."""
from django.db import migrations, models


def seed(apps, schema_editor):
    apps.get_model("market", "MarketAnalysisOptionsState").objects.get_or_create(id=1)


def reverse_guard(apps, schema_editor):
    state = apps.get_model("market", "MarketAnalysisOptionsState")
    option = apps.get_model("market", "MarketAnalysisOption")
    if option.objects.exists() or state.objects.exclude(status="not_ready", generation="").exists():
        raise RuntimeError("Market options have been initialized; explicit verified reset required before reversal")


def add_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute('''ALTER TABLE market_analysis_options ADD CONSTRAINT mkt_options_shape_ck CHECK (
      ranking_dimension IN ('SKU','SPU') AND length(category) BETWEEN 1 AND 200
      AND length(scope) BETWEEN 1 AND 200 AND length(price_band_filter) BETWEEN 1 AND 200
      AND first_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND last_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
      AND first_date <= last_date AND entry_digest ~ '^[0-9a-f]{64}$'
      AND octet_length(entry_json) BETWEEN 1 AND 16384 AND octet_length(search_casefold) <= 8000)''')
    schema_editor.execute('''ALTER TABLE market_analysis_options_state ADD CONSTRAINT mkt_options_state_ck CHECK (
      id=1 AND status IN ('not_ready','ready','blocked') AND identity_count BETWEEN 0 AND 10000
      AND stored_bytes BETWEEN 0 AND 16777216
      AND (status <> 'ready' OR (generation ~ '^[0-9a-f]{32}$' AND directory_digest ~ '^[0-9a-f]{64}$'
           AND stored_bytes >= 2 AND length(source_revision)>0 AND reason='')))''')
    # The composite identity's three 200-scalar text components fit PostgreSQL's
    # btree key bound even at four UTF-8 bytes/scalar; C matches contract order.
    schema_editor.execute('''CREATE UNIQUE INDEX mkt_options_page_idx ON market_analysis_options
      (category COLLATE "C", scope COLLATE "C", ranking_dimension COLLATE "C", price_band_filter COLLATE "C")''')


def drop_guards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute('DROP INDEX IF EXISTS mkt_options_page_idx')
        schema_editor.execute('ALTER TABLE market_analysis_options DROP CONSTRAINT IF EXISTS mkt_options_shape_ck')
        schema_editor.execute('ALTER TABLE market_analysis_options_state DROP CONSTRAINT IF EXISTS mkt_options_state_ck')


class Migration(migrations.Migration):
    dependencies = [("market", "0005_filter_facet_indexes")]
    operations = [
        migrations.CreateModel(name="MarketAnalysisOption", fields=[
            ("id", models.BigAutoField(primary_key=True, serialize=False)),
            ("category", models.CharField(max_length=200)), ("scope", models.CharField(max_length=200)),
            ("ranking_dimension", models.CharField(max_length=3)), ("price_band_filter", models.CharField(max_length=200)),
            ("first_date", models.CharField(max_length=10)), ("last_date", models.CharField(max_length=10)),
            ("entry_json", models.TextField()), ("entry_digest", models.CharField(max_length=64)),
            ("search_casefold", models.CharField(max_length=2000)),
        ], options={"db_table": "market_analysis_options", "constraints": [models.UniqueConstraint(
            fields=("category", "scope", "ranking_dimension", "price_band_filter"), name="mkt_options_identity_uq")]}),
        migrations.CreateModel(name="MarketAnalysisOptionsState", fields=[
            ("id", models.PositiveSmallIntegerField(primary_key=True, default=1, serialize=False)),
            ("status", models.CharField(max_length=16, default="not_ready")),
            ("generation", models.CharField(max_length=64, default="")),
            ("directory_digest", models.CharField(max_length=64, default="")),
            ("identity_count", models.PositiveIntegerField(default=0)), ("stored_bytes", models.PositiveIntegerField(default=0)),
            ("source_revision", models.CharField(max_length=160, default="")),
            ("reason", models.CharField(max_length=64, default="not_initialized")), ("updated_at", models.DateTimeField(auto_now=True)),
        ], options={"db_table": "market_analysis_options_state"}),
        migrations.RunPython(seed, migrations.RunPython.noop),
        migrations.RunPython(add_guards, drop_guards),
        migrations.RunPython(migrations.RunPython.noop, reverse_guard),
    ]
