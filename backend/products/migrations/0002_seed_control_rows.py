from django.db import migrations


SHIPPING_SCOPE_KEY = "f0796d659d78eadb83280aff923663095d2a72709d99e3f4fae988b943afea63"


def seed_control_rows(apps, _schema_editor):
    apps.get_model("products", "ProductDataRevision").objects.get_or_create(
        domain="products",
        defaults={"revision": 0, "source_digest": ""},
    )
    apps.get_model("products", "ProductWriteAuthority").objects.get_or_create(
        id=1,
        defaults={"status": "d1"},
    )
    apps.get_model("products", "ProductInventoryProjectionControl").objects.get_or_create(
        id=1
    )
    apps.get_model("products", "ProductImportScopeHead").objects.get_or_create(
        scope_key=SHIPPING_SCOPE_KEY,
        defaults={"state_token": "0" * 64, "status": "ready"},
    )


class Migration(migrations.Migration):
    dependencies = [("products", "0001_initial")]
    operations = [migrations.RunPython(seed_control_rows, migrations.RunPython.noop)]

