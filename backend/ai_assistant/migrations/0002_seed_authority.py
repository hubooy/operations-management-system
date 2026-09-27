from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model("ai_assistant", "AiDataRevision").objects.using(
        schema_editor.connection.alias
    ).get_or_create(domain="ai-assistant")
    apps.get_model("ai_assistant", "AiWriteAuthority").objects.using(
        schema_editor.connection.alias
    ).get_or_create(id=1)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
