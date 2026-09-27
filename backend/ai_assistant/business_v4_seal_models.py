"""Internal append-only v4 parent seal; no report or public registration."""
from django.db import models
from django.utils import timezone

from .business_v4_models import AiBusinessV4Run
from .business_v4_validation_models import AiBusinessV4ValidationAttempt


class AiBusinessV4Seal(models.Model):
    run = models.OneToOneField(AiBusinessV4Run, primary_key=True,
        db_column="run_id", on_delete=models.PROTECT)
    attempt = models.ForeignKey(AiBusinessV4ValidationAttempt,
        on_delete=models.PROTECT)
    evidence_version = models.PositiveBigIntegerField()
    body_json = models.TextField()
    body_digest = models.CharField(max_length=64)
    body_mac = models.CharField(max_length=64)
    key_id = models.CharField(max_length=16)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_v4_seals"
