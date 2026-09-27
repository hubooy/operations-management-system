"""Protected SQL-owned same-job read record; empty while execution is closed."""
from django.db import models


class AiBusinessMarketV2ReadReceipt(models.Model):
    tool_dispatch_id = models.CharField(max_length=160, primary_key=True)
    execution_report_id = models.CharField(max_length=160)
    job_id = models.CharField(max_length=160)
    provider_dispatch_id = models.CharField(max_length=160)
    receipt_json = models.TextField()
    receipt_digest = models.CharField(max_length=64)
    recorded_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_market_v2_read_receipts"
