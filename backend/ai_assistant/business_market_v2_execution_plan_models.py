"""SQL-owned proposed execution plan; no runnable Agent workflow."""
from django.db import models


class AiBusinessMarketV2ExecutionPlan(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    execution_report_id = models.CharField(max_length=160, unique=True)
    plan_json = models.TextField()
    plan_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_market_v2_execution_plans"
