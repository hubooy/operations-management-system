from __future__ import annotations

import uuid

from django.db import models


class AccessRole(models.Model):
    code = models.CharField(primary_key=True, max_length=32)
    label = models.CharField(max_length=50)
    description = models.CharField(max_length=300)
    rank = models.PositiveSmallIntegerField(unique=True)
    permissions = models.JSONField(default=list)
    version = models.PositiveBigIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "access_control_roles"
        ordering = ["rank"]


class AppUser(models.Model):
    email = models.CharField(primary_key=True, max_length=320)
    display_name = models.CharField(max_length=200)
    role = models.ForeignKey(
        AccessRole,
        db_column="role",
        on_delete=models.PROTECT,
        related_name="users",
    )
    status = models.CharField(max_length=16, default="active")
    scope = models.JSONField(null=True, blank=True)
    version = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "access_control_users"
        indexes = [
            models.Index(fields=["role", "status"], name="access_user_role_status_idx"),
            models.Index(fields=["status", "updated_at"], name="access_user_status_time_idx"),
        ]


class PermissionAuditEvent(models.Model):
    sequence = models.BigAutoField(primary_key=True)
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_id = models.CharField(max_length=128, db_index=True)
    actor_email = models.CharField(max_length=320, db_index=True)
    actor_role = models.CharField(max_length=32)
    target_email = models.CharField(max_length=320, db_index=True)
    action = models.CharField(max_length=64, db_index=True)
    before_state = models.JSONField(null=True, blank=True)
    after_state = models.JSONField(null=True, blank=True)
    before_digest = models.CharField(max_length=64)
    after_digest = models.CharField(max_length=64)
    reason = models.CharField(max_length=200, default="")
    source = models.CharField(max_length=32, default="api")
    occurred_at = models.DateTimeField()
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "access_control_permission_audits"
        indexes = [
            models.Index(fields=["target_email", "occurred_at"], name="access_audit_target_time_idx"),
            models.Index(fields=["action", "occurred_at"], name="access_audit_action_time_idx"),
        ]


class AccessControlDataRevision(models.Model):
    domain = models.CharField(primary_key=True, max_length=32)
    revision = models.PositiveBigIntegerField(default=0)
    source_digest = models.CharField(max_length=64, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "access_control_data_revisions"


class AccessControlWriteAuthority(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default="d1")
    authority_epoch = models.UUIDField(null=True, blank=True)
    cutover_id = models.CharField(max_length=128, default="")
    migration_verify_run_id = models.CharField(max_length=64, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "access_control_write_authority"


class AccessControlWriteRequestReceipt(models.Model):
    request_id = models.CharField(primary_key=True, max_length=128)
    body_sha256 = models.CharField(max_length=64)
    query_sha256 = models.CharField(max_length=64)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=400)
    actor_email = models.CharField(max_length=320)
    status = models.CharField(max_length=32, default="processing")
    response_status = models.PositiveIntegerField(default=0)
    response_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "access_control_write_request_receipts"


class AccessControlMigrationRun(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    mode = models.CharField(max_length=16)
    status = models.CharField(max_length=32)
    source_path_digest = models.CharField(max_length=64)
    source_snapshot_digest = models.CharField(max_length=64)
    target_snapshot_digest = models.CharField(max_length=64, default="")
    source_counts = models.JSONField(default=dict)
    target_counts = models.JSONField(default=dict)
    approved_run_id = models.CharField(max_length=64, default="")
    consumed_by_run_id = models.CharField(max_length=64, default="")
    manifest = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "access_control_migration_runs"
