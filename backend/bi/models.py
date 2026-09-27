from __future__ import annotations

from django.db import models
from django.utils import timezone


class BiMigrationRun(models.Model):
    """Audit evidence for adopting the existing PostgreSQL source authorities.

    BI owns no copied sales or inventory facts.  This row records the exact
    source contract that was checked before the public dashboard was routed to
    the BI reader.
    """

    id = models.CharField(primary_key=True, max_length=64)
    plan_id = models.CharField(max_length=72, unique=True)
    status = models.CharField(max_length=16, default="applied")
    contract_version = models.CharField(max_length=64)
    source_digest = models.CharField(max_length=64)
    source_revisions_json = models.JSONField(default=dict)
    source_counts_json = models.JSONField(default=dict)
    source_snapshot_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "bi_migration_runs"
        indexes = [
            models.Index(fields=["status", "created_at"], name="bi_migration_status_idx"),
        ]
