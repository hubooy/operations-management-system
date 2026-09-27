"""SQL-owned renderer-11 unpublished attestation inventory model."""
from django.db import models


class AiBusinessPromotionBudgetV11Attestation(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    run = models.ForeignKey("AiBusinessFileRun", on_delete=models.PROTECT)
    attempt = models.PositiveIntegerField()
    report = models.ForeignKey("AiReportRun", on_delete=models.PROTECT)
    owner_email = models.CharField(max_length=320)
    binding_digest = models.CharField(max_length=64)
    compact_json_sha256 = models.CharField(max_length=64)
    full_manifest_sha256 = models.CharField(max_length=64)
    full_manifest_digest = models.CharField(max_length=64)
    file_descriptors_json = models.TextField()
    file_descriptors_digest = models.CharField(max_length=64)
    approved_content_digest = models.CharField(max_length=64)
    human_review_digest = models.CharField(max_length=64)
    budget_present = models.BooleanField()
    budget_plan_digest = models.CharField(max_length=64, null=True)
    budget_proof_digest = models.CharField(max_length=64)
    slim_proof_digest = models.CharField(max_length=64)
    file_byte_verification_digest = models.CharField(max_length=64)
    html_rows_digest = models.CharField(max_length=64)
    xlsx_opc_formula_digest = models.CharField(max_length=64)
    owning_verification_digest = models.CharField(max_length=64)
    attestation_sha256 = models.CharField(max_length=64, unique=True)
    attestation_json = models.TextField()
    recorded_at = models.DateTimeField()

    class Meta:
        db_table = "ai_business_promotion_budget_v11_attestations"
        constraints = [models.UniqueConstraint(fields=["run", "attempt"],
            name="ai_budget_v11_attest_attempt_uq")]
