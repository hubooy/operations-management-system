from django.db import migrations


def change_constraint(schema_editor, versions):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("ALTER TABLE ai_business_file_runs DROP CONSTRAINT ai_business_file_bound")
        cursor.execute("ALTER TABLE ai_business_file_runs ADD CONSTRAINT ai_business_file_bound CHECK (scope_json='null' AND version>=1 AND attempt<=5 AND renderer_version IN ("+versions+") AND stored_bytes<=1073741824 AND octet_length(progress_json)<=4096 AND octet_length(manifest_json)<=131072 AND status IN ('queued','building','paused','ready','cancelled'))")


def allow_offline_renderer(apps, schema_editor):
    change_constraint(schema_editor, "1,2")


def restore_legacy_constraint(apps, schema_editor):
    if apps.get_model("ai_assistant", "AiBusinessFileRun").objects.filter(renderer_version=2).exists():
        raise RuntimeError("存在不可改写的 v2 文件任务，不能回退渲染约束")
    change_constraint(schema_editor, "1")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0016_business_files")]
    operations = [migrations.RunPython(allow_offline_renderer, restore_legacy_constraint)]
