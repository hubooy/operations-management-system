"""Immutable fixed budget inputs; no public creation or Agent profile enabled."""
from django.db import models
from django.utils import timezone


class AiBusinessBudgetPlan(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    owner_email = models.CharField(max_length=320)
    scope_json = models.TextField(default="null")
    evidence = models.ForeignKey("ai_assistant.AiBusinessEvidenceRun", on_delete=models.PROTECT)
    evidence_version = models.PositiveIntegerField()
    plan_json = models.TextField()
    plan_digest = models.CharField(max_length=64)
    binding_json = models.TextField()
    binding_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_budget_plans"
        indexes = [models.Index(fields=["owner_email"], name="ai_budget_owner_idx")]
