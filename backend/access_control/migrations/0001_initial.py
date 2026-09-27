from __future__ import annotations

import datetime

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone
import uuid


ROLE_CATALOG = {
    "viewer": ("查看者", "只读访问本人数据范围内的业务页面与安全查询。", 10, ["data.read"]),
    "analyst": ("分析员", "在只读数据范围内使用分析、搜索与 AI 查询能力。", 20, ["data.read", "analytics.read", "ai.query"]),
    "operator": ("运营人员", "执行已授权的运营事务、工作流和受控业务写入。", 30, ["data.read", "analytics.read", "ai.query", "operations.write", "workflow.execute"]),
    "admin": ("管理员", "管理系统配置、数据导入、用户、角色分配与权限审计。", 40, ["data.read", "analytics.read", "ai.query", "operations.write", "workflow.execute", "imports.execute", "settings.write", "access_control.manage", "access_control.audit.read"]),
}


def seed_contract(apps, schema_editor):
    AccessRole = apps.get_model("access_control", "AccessRole")
    AppUser = apps.get_model("access_control", "AppUser")
    Revision = apps.get_model("access_control", "AccessControlDataRevision")
    Authority = apps.get_model("access_control", "AccessControlWriteAuthority")
    for code, (label, description, rank, permissions) in ROLE_CATALOG.items():
        AccessRole.objects.update_or_create(code=code, defaults={"label": label, "description": description, "rank": rank, "permissions": permissions, "version": 1})
    now = timezone.now()
    AppUser.objects.get_or_create(email="dengweizhang321@gmail.com", defaults={"display_name": "系统管理员", "role_id": "admin", "status": "active", "scope": None, "version": 1, "created_at": now, "updated_at": now})
    Revision.objects.get_or_create(domain="access-control", defaults={"revision": 0, "source_digest": "0" * 64})
    Authority.objects.get_or_create(id=1, defaults={"status": "d1"})


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(name="AccessControlDataRevision", fields=[("domain", models.CharField(max_length=32, primary_key=True, serialize=False)), ("revision", models.PositiveBigIntegerField(default=0)), ("source_digest", models.CharField(default="", max_length=64)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "access_control_data_revisions"}),
        migrations.CreateModel(name="AccessControlMigrationRun", fields=[("id", models.CharField(max_length=64, primary_key=True, serialize=False)), ("mode", models.CharField(max_length=16)), ("status", models.CharField(max_length=32)), ("source_path_digest", models.CharField(max_length=64)), ("source_snapshot_digest", models.CharField(max_length=64)), ("target_snapshot_digest", models.CharField(default="", max_length=64)), ("source_counts", models.JSONField(default=dict)), ("target_counts", models.JSONField(default=dict)), ("approved_run_id", models.CharField(default="", max_length=64)), ("consumed_by_run_id", models.CharField(default="", max_length=64)), ("manifest", models.JSONField(default=dict)), ("created_at", models.DateTimeField(auto_now_add=True)), ("completed_at", models.DateTimeField(blank=True, null=True))], options={"db_table": "access_control_migration_runs"}),
        migrations.CreateModel(name="AccessControlWriteAuthority", fields=[("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)), ("status", models.CharField(default="d1", max_length=16)), ("authority_epoch", models.UUIDField(blank=True, null=True)), ("cutover_id", models.CharField(default="", max_length=128)), ("migration_verify_run_id", models.CharField(default="", max_length=64)), ("activated_at", models.DateTimeField(blank=True, null=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "access_control_write_authority"}),
        migrations.CreateModel(name="AccessControlWriteRequestReceipt", fields=[("request_id", models.CharField(max_length=128, primary_key=True, serialize=False)), ("body_sha256", models.CharField(max_length=64)), ("query_sha256", models.CharField(max_length=64)), ("method", models.CharField(max_length=8)), ("path", models.CharField(max_length=400)), ("actor_email", models.CharField(max_length=320)), ("status", models.CharField(default="processing", max_length=32)), ("response_status", models.PositiveIntegerField(default=0)), ("response_payload", models.JSONField(default=dict)), ("created_at", models.DateTimeField(auto_now_add=True)), ("completed_at", models.DateTimeField(blank=True, null=True))], options={"db_table": "access_control_write_request_receipts"}),
        migrations.CreateModel(name="AccessRole", fields=[("code", models.CharField(max_length=32, primary_key=True, serialize=False)), ("label", models.CharField(max_length=50)), ("description", models.CharField(max_length=300)), ("rank", models.PositiveSmallIntegerField(unique=True)), ("permissions", models.JSONField(default=list)), ("version", models.PositiveBigIntegerField(default=1)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "access_control_roles", "ordering": ["rank"]}),
        migrations.CreateModel(name="PermissionAuditEvent", fields=[("sequence", models.BigAutoField(primary_key=True, serialize=False)), ("event_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)), ("request_id", models.CharField(db_index=True, max_length=128)), ("actor_email", models.CharField(db_index=True, max_length=320)), ("actor_role", models.CharField(max_length=32)), ("target_email", models.CharField(db_index=True, max_length=320)), ("action", models.CharField(db_index=True, max_length=64)), ("before_state", models.JSONField(blank=True, null=True)), ("after_state", models.JSONField(blank=True, null=True)), ("before_digest", models.CharField(max_length=64)), ("after_digest", models.CharField(max_length=64)), ("reason", models.CharField(default="", max_length=200)), ("source", models.CharField(default="api", max_length=32)), ("occurred_at", models.DateTimeField()), ("migration_generation", models.CharField(db_index=True, default="", max_length=64))], options={"db_table": "access_control_permission_audits", "indexes": [models.Index(fields=["target_email", "occurred_at"], name="access_audit_target_time_idx"), models.Index(fields=["action", "occurred_at"], name="access_audit_action_time_idx")]}),
        migrations.CreateModel(name="AppUser", fields=[("email", models.CharField(max_length=320, primary_key=True, serialize=False)), ("display_name", models.CharField(max_length=200)), ("status", models.CharField(default="active", max_length=16)), ("scope", models.JSONField(blank=True, null=True)), ("version", models.PositiveBigIntegerField(default=1)), ("created_at", models.DateTimeField()), ("updated_at", models.DateTimeField()), ("migration_generation", models.CharField(db_index=True, default="", max_length=64)), ("role", models.ForeignKey(db_column="role", on_delete=django.db.models.deletion.PROTECT, related_name="users", to="access_control.accessrole"))], options={"db_table": "access_control_users", "indexes": [models.Index(fields=["role", "status"], name="access_user_role_status_idx"), models.Index(fields=["status", "updated_at"], name="access_user_status_time_idx")]}),
        migrations.RunPython(seed_contract, migrations.RunPython.noop),
    ]
