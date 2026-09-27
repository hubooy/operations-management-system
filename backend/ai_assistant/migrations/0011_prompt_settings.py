"""Append-only, bounded AI guidance revisions; no business formula changes."""
from django.db import migrations, models
import django.utils.timezone


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON ai_prompt_settings_revisions FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
        cursor.execute("CREATE TRIGGER ai_immutable_evidence BEFORE UPDATE OR DELETE ON ai_prompt_settings_revisions FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()")
        cursor.execute("ALTER TABLE ai_prompt_settings_revisions ADD CONSTRAINT ai_prompt_revision_bound CHECK (version>=1 AND octet_length(config_json)<=65536 AND (restored_from IS NULL OR restored_from<version))")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0010_dingtalk_schedules")]
    operations = [migrations.CreateModel(name="AiPromptSettingsRevision", fields=[
        ("version", models.PositiveIntegerField(primary_key=True, serialize=False)),
        ("config_json", models.TextField()), ("created_by", models.CharField(max_length=320)),
        ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
        ("restored_from", models.PositiveIntegerField(null=True)),
    ], options={"db_table": "ai_prompt_settings_revisions"}), migrations.RunPython(install)]
