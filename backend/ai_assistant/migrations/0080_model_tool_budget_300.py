"""Raise existing chat models that were pinned at the former maximum."""
from django.db import migrations
from django.db.models import F
from django.utils import timezone


def raise_budget(apps, schema_editor):
    model = apps.get_model("ai_assistant", "AiModels")
    model.objects.filter(model_type__in=["text", "vision"], max_total_tool_calls=74).update(
        max_total_tool_calls=300,
        version=F("version") + 1,
        updated_at=timezone.now(),
    )


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0079_business_market_v6_source_ticket")]
    operations = [migrations.RunPython(raise_budget, migrations.RunPython.noop)]
