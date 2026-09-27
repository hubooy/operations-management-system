"""SQL-owned rehearsal sidecars; ordinary ORM roles have no table DML."""
from django.db import models


class AiBusinessMarketV2PaidAuthority(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    plan_id = models.CharField(max_length=64, unique=True)
    cost_ledger_id = models.CharField(max_length=64, unique=True)
    owner_email = models.CharField(max_length=320)
    authority_json = models.TextField()
    authority_digest = models.CharField(max_length=64)
    approved_cap_cents = models.BigIntegerField()
    status = models.CharField(max_length=64)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_market_v2_paid_authorities"


class AiBusinessMarketV2RoundReservation(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    authority_id = models.CharField(max_length=64)
    plan_id = models.CharField(max_length=64)
    role = models.CharField(max_length=64)
    round_number = models.IntegerField()
    request_digest = models.CharField(max_length=64)
    intent_digest = models.CharField(max_length=64)
    max_cost_cents = models.BigIntegerField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_market_v2_round_reservations"


class AiBusinessMarketV2RoundEvent(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    reservation_id = models.CharField(max_length=64)
    event_kind = models.CharField(max_length=32)
    intent_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_market_v2_round_events"
