"""Immutable, atomically published screening pages; no runtime registration."""
from django.db import models
from django.utils import timezone


class AiBusinessScreeningRun(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    report = models.ForeignKey("ai_assistant.AiReportRun", on_delete=models.PROTECT)
    evidence = models.ForeignKey("ai_assistant.AiBusinessEvidenceRun", on_delete=models.PROTECT)
    owner_email = models.CharField(max_length=320)
    scope_json = models.TextField(default="null")
    binding_json = models.TextField()
    binding_digest = models.CharField(max_length=64)
    manifest_json = models.TextField()
    manifest_digest = models.CharField(max_length=64)
    selection_plan_digest = models.CharField(max_length=64)
    pure_result_digest = models.CharField(max_length=64)
    service_result_digest = models.CharField(max_length=64)
    content_root_digest = models.CharField(max_length=64)
    algorithm_version = models.CharField(max_length=64)
    selection_policy = models.CharField(max_length=64)
    storage_schema = models.CharField(max_length=64)
    capacity_profile = models.CharField(max_length=64)
    page_count = models.PositiveIntegerField()
    stored_bytes = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_screening_runs"
        indexes = [models.Index(fields=["owner_email", "-created_at"], name="ai_screen_owner_idx")]
        constraints = [models.UniqueConstraint(fields=["report", "binding_digest", "selection_plan_digest", "storage_schema"],
            name="ai_screen_binding_uq")]


class AiBusinessScreeningPage(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    run = models.ForeignKey(AiBusinessScreeningRun, on_delete=models.PROTECT)
    sequence = models.PositiveIntegerField()
    kind = models.CharField(max_length=16)
    partition_key = models.CharField(max_length=64, default="")
    offset = models.PositiveIntegerField()
    returned = models.PositiveIntegerField()
    total = models.PositiveIntegerField()
    next_offset = models.PositiveIntegerField(null=True)
    payload_json = models.TextField()
    payload_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_screening_pages"
        constraints = [models.UniqueConstraint(fields=["run", "sequence"], name="ai_screen_page_sequence_uq"),
            models.UniqueConstraint(fields=["run", "kind", "partition_key", "offset"], name="ai_screen_page_offset_uq")]
