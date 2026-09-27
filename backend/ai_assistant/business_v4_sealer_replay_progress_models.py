"""Inert, append-only v4 promotion segment replay receipt model."""
from django.db import models


class AiBusinessV4SealerReplayProgress(models.Model):
    id = models.UUIDField(primary_key=True)
    ticket_id = models.UUIDField()
    run_id = models.CharField(max_length=160)
    attempt_id = models.CharField(max_length=160)
    source_id = models.CharField(max_length=160)
    source_root = models.CharField(max_length=64)
    segment_index = models.PositiveIntegerField()
    segment_proof_digest = models.CharField(max_length=64)
    key_id = models.CharField(max_length=16)
    actor_email = models.CharField(max_length=320)
    actor_version = models.PositiveBigIntegerField()
    previous_candidate_digest = models.CharField(max_length=64)
    candidate_digest = models.CharField(max_length=64)
    candidate_json = models.TextField()
    recorded_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_v4_sealer_replay_progress"
