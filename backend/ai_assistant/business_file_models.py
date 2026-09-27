from django.db import models
from django.utils import timezone


class AiBusinessFileRun(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    owner_email = models.CharField(max_length=320)
    scope_json = models.TextField(default="null")
    report = models.ForeignKey("AiReportRun", on_delete=models.PROTECT)
    draft = models.BooleanField(default=False)
    renderer_version = models.PositiveIntegerField(default=1)
    binding_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default="queued")
    version = models.PositiveIntegerField(default=1)
    attempt = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(default=timezone.now)
    stored_bytes = models.PositiveBigIntegerField(default=0)
    progress_json = models.TextField(default="{}")
    manifest_json = models.TextField(default="{}")
    error_code = models.CharField(max_length=64, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_file_runs"
        constraints = [models.UniqueConstraint(fields=["report", "draft", "renderer_version", "binding_digest"], name="ai_business_file_binding_uq")]
        indexes = [models.Index(fields=["owner_email", "-created_at"], name="ai_business_file_owner_idx"),
                   models.Index(fields=["status", "lease_until"], name="ai_business_file_queue_idx")]


class AiBusinessFileChunk(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    run = models.ForeignKey(AiBusinessFileRun, on_delete=models.PROTECT)
    attempt = models.PositiveIntegerField()
    format = models.CharField(max_length=4)
    sequence = models.PositiveIntegerField()
    content = models.BinaryField()
    content_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_file_chunks"
        constraints = [models.UniqueConstraint(fields=["run", "attempt", "format", "sequence"], name="ai_business_file_chunk_uq")]


class AiBusinessVolumeChunk(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    run = models.ForeignKey(AiBusinessFileRun, on_delete=models.PROTECT)
    attempt = models.PositiveIntegerField()
    volume_index = models.PositiveIntegerField()
    format = models.CharField(max_length=4)
    sequence = models.PositiveIntegerField()
    content = models.BinaryField()
    content_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_volume_chunks"
        constraints = [models.UniqueConstraint(fields=["run", "attempt", "volume_index", "format", "sequence"], name="ai_business_volume_chunk_uq")]
