"""Immutable SQL-owned sidecar for independently prepared market material."""
from django.db import models


class AiBusinessMarketV2Material(models.Model):
    report_id = models.CharField(max_length=160, primary_key=True)
    source_report_id = models.CharField(max_length=160)
    source_snapshot_digest = models.CharField(max_length=64)
    source_workflow_input_digest = models.CharField(max_length=64)
    selector_digest = models.CharField(max_length=64)
    algorithms_digest = models.CharField(max_length=64)
    manifest_digest = models.CharField(max_length=64)
    manifest_json_sha256 = models.CharField(max_length=64)
    summary_digest = models.CharField(max_length=64)
    table_spec_digests_json = models.TextField()
    manifest_json = models.TextField()
    summary_json = models.TextField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_market_v2_materials"
