from django.db import migrations


def seed_control_rows(apps, _schema_editor):
    apps.get_model("workflow", "WorkflowDataRevision").objects.get_or_create(
        domain="workflow",
        defaults={"revision": 0, "source_digest": ""},
    )
    apps.get_model("workflow", "WorkflowWriteAuthority").objects.get_or_create(
        id=1,
        defaults={"status": "disabled"},
    )


class Migration(migrations.Migration):
    dependencies = [("workflow", "0001_initial")]
    operations = [migrations.RunPython(seed_control_rows, migrations.RunPython.noop)]
