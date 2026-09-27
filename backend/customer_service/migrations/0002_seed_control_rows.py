from django.db import migrations


def seed_control_rows(apps, _schema_editor):
    apps.get_model("customer_service", "CustomerServiceDataRevision").objects.get_or_create(
        domain="customer-service",
        defaults={"revision": 0, "source_digest": ""},
    )
    apps.get_model("customer_service", "CustomerServiceWriteAuthority").objects.get_or_create(
        id=1,
        defaults={"status": "d1"},
    )


class Migration(migrations.Migration):
    dependencies = [("customer_service", "0001_initial")]
    operations = [migrations.RunPython(seed_control_rows, migrations.RunPython.noop)]
