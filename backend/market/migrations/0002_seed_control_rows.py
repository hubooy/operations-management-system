import hashlib

from django.db import migrations


def seed(apps, schema_editor):
    revision = apps.get_model("market", "MarketDataRevision")
    authority = apps.get_model("market", "MarketWriteAuthority")
    projection = apps.get_model("market", "MarketNetshopProjectionControl")
    revision.objects.get_or_create(
        domain="market",
        defaults={"revision": 0, "source_digest": hashlib.sha256(b"market-empty-v1").hexdigest()},
    )
    authority.objects.get_or_create(id=1, defaults={"status": "d1"})
    projection.objects.get_or_create(id=1)


class Migration(migrations.Migration):
    dependencies = [("market", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
