"""Versioned methods/templates and owner-bound report execution evidence."""
from django.db import models
from django.utils import timezone


class AiLibraryRevision(models.Model):
    version = models.PositiveIntegerField(primary_key=True)
    config_json = models.TextField()
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_library_revisions"


class AiExecutionGuidance(models.Model):
    entity_id = models.CharField(max_length=160, primary_key=True)
    snapshot_json = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_execution_guidance"


class AiReportRun(models.Model):
    id = models.CharField(max_length=160, primary_key=True)
    owner_email = models.CharField(max_length=320)
    scope_json = models.TextField(default="null")
    client_request_id = models.CharField(max_length=160)
    request_digest = models.CharField(max_length=64)
    workflow = models.OneToOneField("ai_assistant.AiWorkflowRuns", on_delete=models.PROTECT)
    budget_plan = models.OneToOneField("ai_assistant.AiBusinessBudgetPlan", on_delete=models.PROTECT, null=True, blank=True)
    snapshot_json = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_report_runs"
        constraints = [models.UniqueConstraint(fields=["owner_email", "client_request_id"], name="ai_report_client_uq")]
        indexes = [models.Index(fields=["owner_email", "-created_at"], name="ai_report_owner_idx")]


class AiReportDelivery(models.Model):
    report = models.OneToOneField(AiReportRun, primary_key=True, on_delete=models.PROTECT)
    channel_id = models.CharField(max_length=160)
    channel_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=24, default="reserved")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_report_deliveries"
