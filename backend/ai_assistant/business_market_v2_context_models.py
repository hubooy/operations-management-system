"""Protected SQL-owned market context proof row, not an Agent result."""
from django.db import models


class AiBusinessMarketV2ContextProof(models.Model):
    execution_report_id = models.CharField(max_length=160, primary_key=True)
    proof_json = models.TextField()
    proof_digest = models.CharField(max_length=64)
    recorded_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_market_v2_context_proofs"
