"""Default-closed renderer-11 volume staging; publication remains forbidden."""
from django.db import migrations

from ai_assistant import business_promotion_budget_v11_stage_sql as candidate


class Migration(migrations.Migration):
    dependencies = [
        ("ai_assistant", "0065_business_market_v2_model_cost_reservation"),
    ]
    operations = [migrations.RunPython(candidate.install, candidate.uninstall)]
