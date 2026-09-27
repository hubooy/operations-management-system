"""PostgreSQL channel state; business facts stay in their owning domains."""
from django.db import models
from django.utils import timezone


class AiDingTalkSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    identity_json = models.TextField()
    enabled = models.BooleanField(default=False)
    groups_json = models.TextField(default="[]")
    version = models.PositiveIntegerField(default=1)
    updated_by = models.CharField(max_length=320)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_dingtalk_settings"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="ai_ding_settings_singleton"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="ai_ding_settings_version"),
        ]


class AiDingTalkSession(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    config_digest = models.CharField(max_length=64)
    corp_id = models.CharField(max_length=160)
    robot_code = models.CharField(max_length=160)
    sender_id = models.CharField(max_length=160)
    conversation_type = models.CharField(max_length=1)
    external_conversation_id = models.CharField(max_length=256)
    owner_email = models.CharField(max_length=320)
    scope_json = models.TextField(default="null")
    conversation = models.OneToOneField("AiConversations", null=True, on_delete=models.PROTECT, related_name="dingtalk_session")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_dingtalk_sessions"
        constraints = [models.CheckConstraint(condition=models.Q(conversation_type__in=["1", "2"]), name="ai_ding_session_type")]


class AiDingTalkReceipt(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    session = models.ForeignKey(AiDingTalkSession, on_delete=models.PROTECT)
    payload_digest = models.CharField(max_length=64)
    prompt = models.TextField()
    reply = models.TextField(default="")
    status = models.CharField(max_length=16, default="queued")
    ack_status = models.CharField(max_length=16, default="pending")
    error_code = models.CharField(max_length=64, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_dingtalk_receipts"
        indexes = [models.Index(fields=["status", "created_at"], name="ai_ding_queue"), models.Index(fields=["session", "status"], name="ai_ding_session_queue")]
        constraints = [
            models.CheckConstraint(condition=models.Q(status__in=["queued", "running", "ready", "sending", "sent", "unknown", "denied", "failed"]), name="ai_ding_receipt_status"),
            models.CheckConstraint(condition=models.Q(ack_status__in=["pending", "sending", "sent", "unknown"]), name="ai_ding_ack_status"),
        ]


class AiDingTalkSchedule(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    name = models.CharField(max_length=100)
    prompt = models.TextField()
    content_type = models.CharField(max_length=16, default="text")
    source_ref = models.CharField(max_length=160, default="")
    cadence = models.CharField(max_length=8)
    hour = models.PositiveSmallIntegerField()
    minute = models.PositiveSmallIntegerField()
    day = models.PositiveSmallIntegerField(default=1)
    target_type = models.CharField(max_length=8)
    target_id = models.CharField(max_length=256)
    sender_id = models.CharField(max_length=160)
    owner_email = models.CharField(max_length=320)
    enabled = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    next_run_at = models.DateTimeField(null=True)
    last_run_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_dingtalk_schedules"
        indexes = [models.Index(fields=["enabled", "next_run_at"], name="ai_ding_schedule_due")]


class AiDingTalkScheduleRun(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    schedule = models.ForeignKey(AiDingTalkSchedule, on_delete=models.PROTECT)
    schedule_version = models.PositiveIntegerField()
    scheduled_at = models.DateTimeField()
    status = models.CharField(max_length=16, default="running")
    error_code = models.CharField(max_length=64, default="")
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "ai_dingtalk_schedule_runs"
        indexes = [models.Index(fields=["schedule", "created_at"], name="ai_ding_schedule_history")]
        constraints = [models.UniqueConstraint(fields=["schedule", "scheduled_at"], name="ai_ding_schedule_slot_uq")]
