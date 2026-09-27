"""SQL-owned, non-public v4 seal capability records."""
from django.db import models


class AiBusinessV4SealTicket(models.Model):
    id = models.UUIDField(primary_key=True)
    run_id = models.CharField(max_length=160)
    attempt_id = models.CharField(max_length=160)
    actor_email = models.CharField(max_length=320)
    actor_version = models.BigIntegerField()
    parent_version = models.BigIntegerField()
    plan_digest = models.CharField(max_length=64)
    directory_digest = models.CharField(max_length=64)
    source_root = models.CharField(max_length=64)
    source_count = models.IntegerField()
    request_digest = models.CharField(max_length=64)
    nonce_hash = models.CharField(max_length=64)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_v4_seal_tickets"


class AiBusinessV4SealClaim(models.Model):
    ticket_id = models.UUIDField(primary_key=True)
    claim_hash = models.CharField(max_length=64)
    claimed_at = models.DateTimeField()
    lease_until = models.DateTimeField()

    class Meta:
        db_table = "ai_business_v4_seal_claims"
