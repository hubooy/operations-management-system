"""SQL-enforced v4 seal consumption receipt; no public ORM writer."""
from django.db import models


class AiBusinessV4SealConsumption(models.Model):
    ticket_id = models.UUIDField(primary_key=True)
    run_id = models.CharField(max_length=160)
    attempt_id = models.CharField(max_length=160)
    request_digest = models.CharField(max_length=64)
    evidence_version = models.BigIntegerField()
    body_digest = models.CharField(max_length=64)
    consumed_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_v4_seal_consumptions"
