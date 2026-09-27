"""Append-only v4 source-validation witnesses; parent seal remains blocked."""
from django.db import models
from django.utils import timezone

from .business_v4_models import AiBusinessV4Run, AiBusinessV4Source


class AiBusinessV4ValidationAttempt(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    run = models.ForeignKey(AiBusinessV4Run, on_delete=models.PROTECT)
    run_version = models.PositiveBigIntegerField()
    plan_digest = models.CharField(max_length=64)
    directory_digest = models.CharField(max_length=64)
    actor_email = models.CharField(max_length=320)
    actor_version = models.PositiveBigIntegerField()
    key_id = models.CharField(max_length=16)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_v4_validation_attempts"
        indexes = [models.Index(fields=["run", "-created_at"],
            name="ai_v4_attempt_run_idx")]


class AiBusinessV4ValidationSegment(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    attempt = models.ForeignKey(AiBusinessV4ValidationAttempt, on_delete=models.PROTECT)
    run = models.ForeignKey(AiBusinessV4Run, on_delete=models.PROTECT)
    source = models.ForeignKey(AiBusinessV4Source, on_delete=models.PROTECT)
    segment_index = models.PositiveIntegerField()
    start_sequence = models.PositiveIntegerField()
    end_sequence = models.PositiveIntegerField()
    source_version = models.PositiveBigIntegerField()
    source_ref = models.CharField(max_length=64)
    source_revision = models.CharField(max_length=128)
    previous_segment_digest = models.CharField(max_length=64)
    progress_json = models.TextField()
    progress_digest = models.CharField(max_length=64)
    proof_digest = models.CharField(max_length=64)
    proof_mac = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_v4_validation_segments"
        constraints = [models.UniqueConstraint(fields=["attempt", "source", "segment_index"],
            name="ai_v4_segment_seq_uq")]
        indexes = [models.Index(fields=["attempt", "source", "-segment_index"],
            name="ai_v4_segment_next_idx")]
