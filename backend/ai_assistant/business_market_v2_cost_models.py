"""Protected cost requirement row; zero funds reserved until real approval."""
from django.db import models


class AiBusinessMarketV2CostLedgerCandidate(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    plan_id = models.CharField(max_length=64, unique=True)
    owner_email = models.CharField(max_length=320)
    model_id = models.CharField(max_length=160)
    model_version = models.BigIntegerField()
    tariff_digest = models.CharField(max_length=64)
    envelope_digest = models.CharField(max_length=64)
    candidate_json = models.TextField()
    candidate_digest = models.CharField(max_length=64)
    required_cents = models.BigIntegerField()
    cap_claim_cents = models.BigIntegerField()
    reserved_cents = models.BigIntegerField()
    status = models.CharField(max_length=64)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_market_v2_cost_ledger_candidates"
