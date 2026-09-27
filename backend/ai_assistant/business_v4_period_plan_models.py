"""Append-only, non-authoritative v4 date-envelope sidecar."""
from django.db import models
from django.utils import timezone

from .business_v4_models import AiBusinessV4Run
from .business_v4_validation_models import AiBusinessV4ValidationAttempt


class AiBusinessV4PeriodPlanCandidate(models.Model):
    run = models.OneToOneField(AiBusinessV4Run, primary_key=True,
        on_delete=models.PROTECT)
    attempt = models.ForeignKey(AiBusinessV4ValidationAttempt,
        on_delete=models.PROTECT)
    owner_email = models.CharField(max_length=320)
    actor_version = models.PositiveBigIntegerField()
    plan_digest = models.CharField(max_length=64)
    directory_digest = models.CharField(max_length=64)
    source_root = models.CharField(max_length=64)
    envelope_json = models.TextField()
    envelope_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_v4_period_plan_candidates"
