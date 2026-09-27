from django.db import migrations


SCOPE_KEYS = {
    "stock": "b1dda3405306702bed118060f189eb3837be5e07dcec8df8684b12edf4840704",
    "age": "ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d",
}


def seed_control_rows(apps, _schema_editor):
    apps.get_model("inventory", "InventoryDataRevision").objects.get_or_create(
        domain="inventory",
        defaults={"revision": 0, "source_digest": ""},
    )
    apps.get_model("inventory", "InventoryWriteAuthority").objects.get_or_create(
        id=1,
        defaults={"status": "d1"},
    )
    apps.get_model("inventory", "InventoryOperatingSettings").objects.get_or_create(id=1)
    scope_model = apps.get_model("inventory", "InventoryImportScopeHead")
    for dataset, scope_key in SCOPE_KEYS.items():
        scope_model.objects.get_or_create(
            dataset=dataset,
            defaults={
                "scope_key": scope_key,
                "state_token": "0" * 64,
                "status": "ready",
            },
        )


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial")]
    operations = [migrations.RunPython(seed_control_rows, migrations.RunPython.noop)]
