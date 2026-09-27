"""AI domain records. Historical identifiers and immutable ledgers are preserved."""

from django.db import models
from django.utils import timezone
from .dingtalk_models import AiDingTalkSession, AiDingTalkReceipt, AiDingTalkSettings, AiDingTalkSchedule, AiDingTalkScheduleRun
from .control_models import (
    AiDataRevision,
    AiWriteAuthority,
    AiWriteReceipt,
    AiMutationAudit,
    AiMigrationRun,
)

__all__ = [
    "AiDataRevision",
    "AiWriteAuthority",
    "AiWriteReceipt",
    "AiMutationAudit",
    "AiMigrationRun",
]


class AiAgentCheckpoints(models.Model):
    id = models.TextField(primary_key=True)
    job = models.ForeignKey("AiAgentJobs", on_delete=models.CASCADE, db_column="job_id")
    ordinal = models.BigIntegerField()
    kind = models.TextField()
    state_json = models.TextField(default="{}")
    output_digest = models.TextField(default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_agent_checkpoints"
        indexes = [
            models.Index(fields=["job", "ordinal"], name="ai_c7b0f32499801f5805ef"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "ordinal"], name="ai_ae84949490ac546dfd82"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=["checkpoint", "completed", "paused", "failed"]
                ),
                name="ai_ck_b4672b98eb5d3441036d",
            ),
            models.CheckConstraint(
                condition=models.Q(ordinal__gte=1, ordinal__lte=64),
                name="ai_ck_12d449714a9f44253d07",
            ),
        ]


class AiAgentEvents(models.Model):
    id = models.TextField(primary_key=True)
    job = models.ForeignKey("AiAgentJobs", on_delete=models.CASCADE, db_column="job_id")
    owner_email = models.TextField()
    actor_email = models.TextField()
    event_type = models.TextField()
    from_status = models.TextField(default="")
    to_status = models.TextField(default="")
    job_version = models.BigIntegerField()
    details_json = models.TextField(default="{}")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_agent_events"
        indexes = [
            models.Index(
                fields=["job", "created_at", "id"], name="ai_ab857d760133f6613547"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(job_version__gte=1),
                name="ai_ck_dfd3af69a1236c401fd3",
            ),
        ]


class AiAgentJobs(models.Model):
    id = models.TextField(primary_key=True)
    owner_email = models.TextField()
    client_request_id = models.TextField()
    request_digest = models.TextField()
    scope_json = models.TextField()
    task = models.TextField()
    input_json = models.TextField(default="{}")
    state_json = models.TextField(default="{}")
    output_json = models.TextField(null=True, blank=True)
    model_id = models.TextField(default="")
    model_version = models.BigIntegerField(default=0)
    allowed_tools_json = models.TextField(default="[]")
    tool_policy_digest = models.TextField(default="")
    provider_round_count = models.BigIntegerField(default=0)
    tool_call_count = models.BigIntegerField(default=0)
    provider_dispatch_started_at = models.DateTimeField(null=True, blank=True)
    status = models.TextField(default="queued")
    phase = models.TextField(default="queued")
    step_index = models.BigIntegerField(default=0)
    version = models.BigIntegerField(default=1)
    mutation_token = models.TextField(default="")
    cancel_requested = models.BigIntegerField(default=0)
    retryable = models.BigIntegerField(default=0)
    resume_count = models.BigIntegerField(default=0)
    attempt_count = models.BigIntegerField(default=0)
    lease_token = models.TextField(default="")
    lease_epoch = models.BigIntegerField(default=0)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(default=timezone.now)
    workflow_run_id = models.TextField(null=True, blank=True)
    workflow_node_key = models.TextField(null=True, blank=True)
    error_code = models.TextField(default="")
    error_message = models.TextField(default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_agent_jobs"
        indexes = [
            models.Index(
                fields=["status", "next_run_at", "created_at", "id"],
                name="ai_832f9ca5787d58451dba",
            ),
            models.Index(
                fields=["owner_email", "created_at", "id"],
                name="ai_2902eda62ab3a5642817",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner_email", "client_request_id"],
                name="ai_949435c0734d90375ed1",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "queued",
                        "running",
                        "paused",
                        "completed",
                        "failed",
                        "cancelled",
                    ]
                ),
                name="ai_ck_dddef78d566cfc92a595",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    phase__in=[
                        "queued",
                        "executing",
                        "paused",
                        "completed",
                        "failed",
                        "cancelled",
                    ]
                ),
                name="ai_ck_89ff4fbd2a24f5ee78ce",
            ),
            models.CheckConstraint(
                condition=models.Q(cancel_requested__in=[0, 1]),
                name="ai_ck_e8dd8f0615aa2983bdca",
            ),
            models.CheckConstraint(
                condition=models.Q(retryable__in=[0, 1]),
                name="ai_ck_3f27daee6ac08c5f22ae",
            ),
            models.CheckConstraint(
                condition=models.Q(step_index__gte=0, step_index__lte=64),
                name="ai_ck_d77cf2d3862933e1a271",
            ),
            models.CheckConstraint(
                condition=models.Q(resume_count__gte=0, resume_count__lte=16),
                name="ai_ck_cb152d9e89ff4675e004",
            ),
            models.CheckConstraint(
                condition=models.Q(model_version__gte=0),
                name="ai_ck_d5135560631fb5d41c3d",
            ),
            models.CheckConstraint(
                condition=models.Q(provider_round_count__gte=0),
                name="ai_ck_0fd49976566c02854412",
            ),
            models.CheckConstraint(
                condition=models.Q(tool_call_count__gte=0),
                name="ai_ck_1c0a1d7a33bb08106c1e",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="ai_ck_9f26b73d57c765d5877d"
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_count__gte=0),
                name="ai_ck_fd7799247044ddbf3103",
            ),
            models.CheckConstraint(
                condition=models.Q(lease_epoch__gte=0),
                name="ai_ck_1d8cc6509515e484ab7c",
            ),
        ]


class AiAgentProviderDispatches(models.Model):
    id = models.TextField(primary_key=True)
    job = models.ForeignKey("AiAgentJobs", on_delete=models.PROTECT, db_column="job_id")
    dispatch_ordinal = models.BigIntegerField()
    owner_email = models.TextField()
    actor_role = models.TextField()
    model_id = models.TextField()
    model_version = models.BigIntegerField()
    tool_policy_digest = models.TextField()
    request_digest = models.TextField()
    state = models.TextField(default="calling")
    lease_epoch = models.BigIntegerField()
    reserved_at = models.DateTimeField(default=timezone.now)
    provider_called_at = models.DateTimeField(default=timezone.now)
    error_code = models.TextField(default="")
    error_message = models.TextField(default="")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_agent_provider_dispatches"
        indexes = [
            models.Index(
                fields=["state", "reserved_at"], name="ai_883deb0333ada132ceaf"
            ),
            models.Index(
                fields=["model_id", "reserved_at"], name="ai_cfacc1afa216635b87b1"
            ),
            models.Index(
                fields=["owner_email", "reserved_at"], name="ai_22cc5d4f884b3b234360"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "dispatch_ordinal"], name="ai_af5a51f7d17b4eb8cba3"
            ),
            models.CheckConstraint(
                condition=models.Q(actor_role__in=["analyst", "operator", "admin"]),
                name="ai_ck_8a931038bce003e47c7e",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=["calling", "succeeded", "failed", "unknown"]
                ),
                name="ai_ck_e3f40a8b4fec7b578deb",
            ),
            models.CheckConstraint(
                condition=models.Q(dispatch_ordinal__gte=1, dispatch_ordinal__lte=20),
                name="ai_ck_8c81787b1d253d47c20a",
            ),
            models.CheckConstraint(
                condition=models.Q(model_version__gte=1),
                name="ai_ck_b3d1ea6b36bc7f0c1747",
            ),
            models.CheckConstraint(
                condition=models.Q(lease_epoch__gte=1),
                name="ai_ck_ece78fdd86640bd40d35",
            ),
        ]


class AiAgentProviderResults(models.Model):
    dispatch = models.OneToOneField(
        "AiAgentProviderDispatches",
        on_delete=models.PROTECT,
        db_column="dispatch_id",
        primary_key=True,
    )
    response_json = models.TextField()
    response_digest = models.TextField()
    usage_json = models.TextField(default="{}")
    provider_request_id = models.TextField(default="")
    completed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_agent_provider_results"


class AiAgentToolDispatches(models.Model):
    id = models.TextField(primary_key=True)
    job = models.ForeignKey("AiAgentJobs", on_delete=models.PROTECT, db_column="job_id")
    provider_dispatch = models.ForeignKey(
        "AiAgentProviderDispatches",
        on_delete=models.PROTECT,
        db_column="provider_dispatch_id",
    )
    tool_call_ordinal = models.BigIntegerField()
    provider_call_id = models.TextField()
    tool_name = models.TextField()
    arguments_json = models.TextField()
    arguments_digest = models.TextField()
    invocation_id = models.TextField()
    state = models.TextField(default="calling")
    lease_epoch = models.BigIntegerField()
    reserved_at = models.DateTimeField(default=timezone.now)
    tool_called_at = models.DateTimeField(default=timezone.now)
    error_code = models.TextField(default="")
    error_message = models.TextField(default="")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_agent_tool_dispatches"
        indexes = [
            models.Index(
                fields=["job", "state", "tool_call_ordinal"],
                name="ai_a3878f6f2fea46297124",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["provider_dispatch", "provider_call_id"],
                name="ai_035bc417890ef5bc232c",
            ),
            models.UniqueConstraint(
                fields=["job", "tool_call_ordinal"], name="ai_0a721ad37ed1dedda213"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=["calling", "succeeded", "failed", "unknown"]
                ),
                name="ai_ck_fd6b2e24d641bfb04994",
            ),
            models.CheckConstraint(
                condition=models.Q(tool_call_ordinal__gte=1, tool_call_ordinal__lte=40),
                name="ai_ck_2f947338251f85c2774b",
            ),
            models.CheckConstraint(
                condition=models.Q(lease_epoch__gte=1),
                name="ai_ck_550b5a2f8c305f1463b6",
            ),
        ]


class AiAgentToolResults(models.Model):
    tool_dispatch = models.OneToOneField(
        "AiAgentToolDispatches",
        on_delete=models.PROTECT,
        db_column="tool_dispatch_id",
        primary_key=True,
    )
    result_json = models.TextField()
    result_digest = models.TextField()
    completed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_agent_tool_results"


class AiAnalysisRuns(models.Model):
    id = models.TextField(primary_key=True)
    owner_email = models.TextField()
    actor_role = models.TextField()
    scope_json = models.TextField()
    dataset = models.TextField()
    query_digest = models.TextField()
    plan_digest = models.TextField()
    operations_json = models.TextField()
    data_cutoff_date = models.TextField(null=True, blank=True)
    source_rows = models.BigIntegerField()
    returned_rows = models.BigIntegerField()
    truncated = models.BigIntegerField(default=0)
    result_digest = models.TextField()
    request_id = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_analysis_runs"
        indexes = [
            models.Index(
                fields=["dataset", "created_at", "id"], name="ai_e48da48139ae1b114e2b"
            ),
            models.Index(
                fields=["owner_email", "created_at", "id"],
                name="ai_e7dc0021f900936d6fbf",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    actor_role__in=["viewer", "analyst", "operator", "admin"]
                ),
                name="ai_ck_9a3acbd16c6cd80d6084",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    dataset__in=[
                        "sales_category",
                        "netshop_product_daily",
                        "netshop_promotion",
                    ]
                ),
                name="ai_ck_7729a062eb61d17420d8",
            ),
            models.CheckConstraint(
                condition=models.Q(truncated__in=[0, 1]),
                name="ai_ck_07f0097472e56ce04171",
            ),
            models.CheckConstraint(
                condition=models.Q(source_rows__gte=0),
                name="ai_ck_2c3bfc3083e6e13caa27",
            ),
            models.CheckConstraint(
                condition=models.Q(returned_rows__gte=0),
                name="ai_ck_705d545bbea302999855",
            ),
        ]


class AiArtifactDeliveries(models.Model):
    id = models.TextField(primary_key=True)
    artifact_id = models.TextField()
    request_id = models.TextField()
    actor_email = models.TextField()
    actor_role = models.TextField()
    surface = models.TextField()
    status = models.TextField()
    byte_size = models.BigIntegerField(null=True, blank=True)
    content_digest = models.TextField(null=True, blank=True)
    error_code = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_artifact_deliveries"
        indexes = [
            models.Index(
                fields=["artifact_id", "created_at"], name="ai_4d064cc0c307921427a9"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["succeeded", "failed"]),
                name="ai_ck_292fe0645a24258c8051",
            ),
        ]


class AiArtifacts(models.Model):
    id = models.TextField(primary_key=True)
    conversation_id = models.TextField()
    message_id = models.TextField()
    owner_email = models.TextField()
    kind = models.TextField()
    title = models.TextField()
    file_name = models.TextField()
    mime_type = models.TextField()
    source_tool = models.TextField()
    columns_json = models.TextField(default="[]")
    rows_json = models.TextField(default="[]")
    row_count = models.BigIntegerField(default=0)
    truncated = models.BigIntegerField(default=0)
    content_digest = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_artifacts"
        indexes = [
            models.Index(
                fields=["owner_email", "created_at"], name="ai_f9d709e901e32b06fb74"
            ),
            models.Index(
                fields=["conversation_id", "message_id", "created_at"],
                name="ai_2c8082ddc0f4671a40a3",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=["table"]),
                name="ai_ck_e716b8f3893b48e02faf",
            ),
        ]


class AiChannelCallbackEvents(models.Model):
    id = models.TextField(primary_key=True)
    channel_id = models.TextField()
    event_key = models.TextField()
    payload_digest = models.TextField()
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_channel_callback_events"
        indexes = [
            models.Index(
                fields=["channel_id", "received_at"], name="ai_4485f5e6831a6f4db86c"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["channel_id", "event_key"], name="ai_f1d89fcaec213cc6b34f"
            ),
        ]


class AiChannels(models.Model):
    id = models.TextField(primary_key=True)
    name = models.TextField()
    kind = models.TextField()
    status = models.TextField()
    send_enabled = models.BigIntegerField(default=0)
    callback_enabled = models.BigIntegerField(default=0)
    webhook_url = models.TextField(default="")
    callback_token_encrypted = models.TextField(default="")
    callback_token_suffix = models.TextField(default="")
    aes_key_encrypted = models.TextField(default="")
    aes_key_suffix = models.TextField(default="")
    receiver_id = models.TextField(default="")
    last_test_result = models.TextField(null=True, blank=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_channels"
        indexes = [
            models.Index(
                fields=["status", "kind", "updated_at"], name="ai_1b4e9210a424d924ad01"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=[
                        "dingtalk_group_bot",
                        "dingtalk_app",
                        "wechat_work_group_bot",
                        "wechat_work_app",
                    ]
                ),
                name="ai_ck_771f01e9ac2239f25848",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["enabled", "disabled"]),
                name="ai_ck_7209d1f7a80cc96461fc",
            ),
        ]


class AiChatProviderDispatches(models.Model):
    id = models.TextField(primary_key=True)
    receipt = models.ForeignKey(
        "AiChatRequestReceipts", on_delete=models.CASCADE, db_column="receipt_id"
    )
    owner_email = models.TextField()
    model_id = models.TextField()
    dispatch_ordinal = models.BigIntegerField()
    reserved_at = models.DateTimeField()
    provider_called_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_chat_provider_dispatches"
        indexes = [
            models.Index(fields=["reserved_at"], name="ai_786cb67466d1ee4f5ec4"),
            models.Index(
                fields=["model_id", "reserved_at"], name="ai_70a8c2b8996308acf5fd"
            ),
            models.Index(
                fields=["owner_email", "reserved_at"], name="ai_3cbdee118d38052410ef"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["receipt", "dispatch_ordinal"], name="ai_0cbc96dfc391e4c17f1d"
            ),
        ]


class AiChatRequestReceipts(models.Model):
    cancel_requested = models.BooleanField(default=False)
    id = models.TextField(primary_key=True)
    owner_email = models.TextField()
    client_request_id = models.TextField()
    request_digest = models.TextField()
    status = models.TextField()
    model_id = models.TextField(null=True, blank=True)
    conversation = models.ForeignKey(
        "AiConversations",
        on_delete=models.SET_NULL,
        db_column="conversation_id",
        null=True,
        blank=True,
    )
    assistant_message_id = models.TextField(null=True, blank=True)
    result_json = models.TextField(null=True, blank=True)
    error_code = models.TextField(null=True, blank=True)
    admitted_at = models.DateTimeField(null=True, blank=True)
    provider_started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_chat_request_receipts"
        indexes = [
            models.Index(
                fields=["status", "model_id", "admitted_at"],
                name="ai_e22bf0e1d0d6ac1ced76",
            ),
            models.Index(
                fields=["status", "admitted_at"], name="ai_a8db3a812b462c2ab56f"
            ),
            models.Index(
                fields=["owner_email", "admitted_at"], name="ai_e37717437f2b69490aa2"
            ),
            models.Index(
                fields=["status", "owner_email", "model_id", "provider_started_at"],
                name="ai_5a97610ffa86c2ca2004",
            ),
            models.Index(
                fields=["model_id", "provider_started_at"],
                name="ai_3ce5b3a295708160d5f0",
            ),
            models.Index(
                fields=["owner_email", "provider_started_at"],
                name="ai_32945e7100cc7a4879cf",
            ),
            models.Index(
                fields=["provider_started_at"], name="ai_c4fb16e23190cbbbe95c"
            ),
            models.Index(
                fields=["conversation", "created_at"], name="ai_155a371b6c235aebb317"
            ),
            models.Index(
                fields=["status", "updated_at"], name="ai_c8d4ceeb25be876c219a"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner_email", "client_request_id"],
                name="ai_a8ab4d361788c28e79d6",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "processing",
                        "dispatched",
                        "succeeded",
                        "failed",
                        "unknown",
                    ]
                ),
                name="ai_ck_20e4b872ffe66952794a",
            ),
        ]


class AiConversationDeletionAudits(models.Model):
    audit_id = models.TextField(primary_key=True)
    conversation_id = models.TextField()
    conversation_owner = models.TextField()
    actor_email = models.TextField()
    actor_role = models.TextField()
    reason = models.TextField()
    deleted_message_count = models.BigIntegerField(default=0)
    deleted_artifact_count = models.BigIntegerField(default=0)
    deleted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_conversation_deletion_audits"
        indexes = [
            models.Index(
                fields=["actor_email", "deleted_at"], name="ai_600b19f054b906ff74d2"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation_id"], name="ai_65fb2cfee7b65d4e55d1"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    actor_role__in=["viewer", "analyst", "operator", "admin"]
                ),
                name="ai_ck_f20c7b68ce3c252dd94e",
            ),
        ]


class AiConversationMessages(models.Model):
    execution_json = models.TextField(default="{}")
    ordinal = models.PositiveBigIntegerField(default=0, db_index=True)
    id = models.TextField(primary_key=True)
    conversation_id = models.TextField()
    role = models.TextField()
    content = models.TextField()
    message_kind = models.TextField(default="message")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_conversation_messages"
        indexes = [
            models.Index(
                fields=["conversation_id", "message_kind", "created_at"],
                name="ai_c52fee85d06a5f4a1543",
            ),
            models.Index(
                fields=["conversation_id", "created_at"], name="ai_701838ab3eade5e8d9e8"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=["user", "assistant"]),
                name="ai_ck_7c55c35874eb9f23870a",
            ),
        ]


class AiConversationScopes(models.Model):
    conversation = models.OneToOneField(
        "AiConversations",
        on_delete=models.CASCADE,
        db_column="conversation_id",
        primary_key=True,
    )
    scope_json = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_conversation_scopes"
        indexes = [
            models.Index(
                fields=["scope_json", "created_at"], name="ai_fb73bbf3175be34cc564"
            ),
        ]


class AiConversations(models.Model):
    id = models.TextField(primary_key=True)
    title = models.TextField()
    model_id = models.TextField(null=True, blank=True)
    created_by = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_conversations"
        indexes = [
            models.Index(fields=["updated_at", "id"], name="ai_56a5a65c0a8e0cdee639"),
            models.Index(
                fields=["created_by", "updated_at"], name="ai_1eb6c53b52c522d7a849"
            ),
        ]


class AiConversationWorkspace(models.Model):
    conversation = models.OneToOneField(
        AiConversations, primary_key=True, db_column="conversation_id",
        on_delete=models.CASCADE, related_name="workspace",
    )
    module_key = models.TextField()
    page_context_json = models.TextField(default="null")
    last_opened_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_conversation_workspaces"
        indexes = [models.Index(fields=["module_key", "last_opened_at"], name="ai_workspace_recent")]
        constraints = [models.CheckConstraint(
            condition=models.Q(module_key__in=["n8n_workflows", "dashboard", "shop", "market", "customer_service", "sales", "inventory", "product", "workflow", "import", "settings", "ai"]),
            name="ai_workspace_module",
        )]


class AiKnowledgeEntries(models.Model):
    id = models.TextField(primary_key=True)
    source_type = models.TextField()
    source_ref = models.TextField()
    title = models.TextField()
    content = models.TextField()
    tags_json = models.TextField(default="[]")
    allowed_roles_json = models.TextField(default="[]")
    status = models.TextField(default="active")
    version = models.BigIntegerField(default=1)
    content_digest = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_knowledge_entries"
        indexes = [
            models.Index(
                fields=["status", "source_type", "updated_at"],
                name="ai_30894b9105c2fa28fc72",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    source_type__in=[
                        "system_policy",
                        "business_metric",
                        "identity_mapping",
                    ]
                ),
                name="ai_ck_fba54d498c8affb612f4",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "disabled"]),
                name="ai_ck_b08cf6906f8cced4279d",
            ),
        ]


class AiMemoryAuditLogs(models.Model):
    id = models.TextField(primary_key=True)
    operation_id = models.TextField()
    request_id = models.TextField()
    memory_id = models.TextField()
    owner_email = models.TextField()
    actor_role = models.TextField()
    operation = models.TextField()
    status = models.TextField()
    scope_digest = models.TextField()
    before_digest = models.TextField(null=True, blank=True)
    after_digest = models.TextField(null=True, blank=True)
    result_version = models.BigIntegerField()
    policy_version = models.TextField()
    gate_results_json = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_memory_audit_logs"
        indexes = [
            models.Index(
                fields=["memory_id", "created_at", "id"], name="ai_6e705ac068ce94cf7d41"
            ),
            models.Index(
                fields=["owner_email", "created_at", "id"],
                name="ai_768eb206f958518006af",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["operation_id"], name="ai_6d46a8bbeb6813cce843"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    actor_role__in=["viewer", "analyst", "operator", "admin"]
                ),
                name="ai_ck_bd834f538f3a16733bd7",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    operation__in=["create", "update", "archive", "duplicate"]
                ),
                name="ai_ck_8a3a3f6b0e4e94f4edf0",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["succeeded", "duplicate"]),
                name="ai_ck_a400895d04c914f80c38",
            ),
        ]


class AiMemoryCommitGuards(models.Model):
    operation_id = models.TextField(primary_key=True)
    audit_present = models.BigIntegerField()

    class Meta:
        db_table = "ai_memory_commit_guards"


class AiMemoryEntries(models.Model):
    id = models.TextField(primary_key=True)
    owner_email = models.TextField()
    kind = models.TextField()
    memory_key = models.TextField()
    memory_key_normalized = models.TextField()
    content = models.TextField()
    content_digest = models.TextField()
    scope_mode = models.TextField()
    scope_json = models.TextField()
    scope_digest = models.TextField()
    status = models.TextField(default="active")
    version = models.BigIntegerField(default=1)
    source = models.TextField()
    source_conversation_id = models.TextField(null=True, blank=True)
    source_message_id = models.TextField(null=True, blank=True)
    last_operation_id = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_memory_entries"
        indexes = [
            models.Index(
                fields=["owner_email", "status", "updated_at", "id"],
                name="ai_3263a71d156dd29d4118",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner_email", "kind", "memory_key_normalized", "scope_digest"],
                condition=models.Q(status="active"),
                name="ai_memory_active_key_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=["preference", "glossary", "business_context"]
                ),
                name="ai_ck_5a7b4825a9064ca4d0c4",
            ),
            models.CheckConstraint(
                condition=models.Q(scope_mode__in=["owner", "data_scope"]),
                name="ai_ck_f36c7ca8be8e1c7b5f32",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "archived"]),
                name="ai_ck_9e9c31b8850e5f5ccad0",
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=["management_ui", "web_chat"]),
                name="ai_ck_6e7dafcb9fa4c3c38b3b",
            ),
        ]


class AiModels(models.Model):
    generation_options_json = models.TextField(default="{}")
    id = models.TextField(primary_key=True)
    version = models.BigIntegerField(default=1)
    name = models.TextField()
    protocol = models.TextField()
    model_type = models.TextField()
    model_name = models.TextField()
    base_url = models.TextField(default="")
    api_key_encrypted = models.TextField(default="")
    api_key_suffix = models.TextField(default="")
    is_default_text_model = models.BigIntegerField(default=0)
    status = models.TextField()
    timeout_ms = models.BigIntegerField(default=60000)
    max_tokens = models.BigIntegerField(default=4096)
    reasoning_mode = models.TextField(default="auto")
    temperature_milli = models.BigIntegerField(default=200)
    max_tool_rounds = models.BigIntegerField(default=6)
    max_total_tool_calls = models.BigIntegerField(default=12)
    last_test_result = models.TextField(null=True, blank=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_models"
        indexes = [
            models.Index(
                fields=["status", "model_type", "updated_at"],
                name="ai_4d0474015dbc6ea22e8f",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default_text_model"],
                condition=models.Q(
                    is_default_text_model=1, status="enabled", model_type="text"
                ),
                name="ai_default_text_model_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(protocol__in=["openai_compatible", "anthropic"]),
                name="ai_ck_f0310d6adef4714c9992",
            ),
            models.CheckConstraint(
                condition=models.Q(model_type__in=["text", "image", "vision"]),
                name="ai_ck_e987526c81400be83cb4",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["enabled", "disabled"]),
                name="ai_ck_42a2926c10e651aa60cb",
            ),
            models.CheckConstraint(
                condition=models.Q(reasoning_mode__in=["auto", "disabled"]),
                name="ai_ck_92aaec6dbb8f9b7fcfed",
            ),
        ]


class AiSpaceAdminAudits(models.Model):
    id = models.TextField(primary_key=True)
    actor_email = models.TextField()
    actor_role = models.TextField()
    action = models.TextField()
    entity_type = models.TextField()
    entity_id = models.TextField()
    before_json = models.TextField(default="{}")
    after_json = models.TextField(default="{}")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_admin_audits"
        indexes = [
            models.Index(
                fields=["entity_type", "entity_id", "created_at", "id"],
                name="ai_3444e2ca9271dcc340a7",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    action__in=[
                        "upsert_profile",
                        "delete_profile",
                        "upsert_template",
                        "delete_template",
                    ]
                ),
                name="ai_ck_911a3d907511be0a1229",
            ),
            models.CheckConstraint(
                condition=models.Q(entity_type__in=["model_profile", "template"]),
                name="ai_ck_bdace58f4c3f62b0ff58",
            ),
        ]


class AiSpaceAssetCleanupQueue(models.Model):
    object_key = models.TextField(primary_key=True)
    attempt_count = models.BigIntegerField(default=0)
    last_error = models.TextField(default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_asset_cleanup_queue"


class AiSpaceAssetFavorites(models.Model):
    pk = models.CompositePrimaryKey("asset_id", "actor_email")
    asset_id = models.TextField()
    actor_email = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_asset_favorites"
        indexes = [
            models.Index(
                fields=["actor_email", "created_at", "asset_id"],
                name="ai_976edc3d933aaab70dc5",
            ),
        ]


class AiSpaceAssetPayload(models.Model):
    asset = models.OneToOneField(
        "AiSpaceAssets", primary_key=True, on_delete=models.PROTECT, db_column="asset_id"
    )
    content = models.BinaryField()

    class Meta:
        db_table = "ai_space_asset_payloads"


class AiSpaceAssets(models.Model):
    id = models.TextField(primary_key=True)
    job = models.ForeignKey("AiSpaceJobs", on_delete=models.CASCADE, db_column="job_id")
    item = models.ForeignKey(
        "AiSpaceJobItems", on_delete=models.CASCADE, db_column="item_id"
    )
    owner_email = models.TextField()
    scope_json = models.TextField()
    scene = models.TextField()
    object_key = models.TextField()
    content_sha256 = models.TextField()
    mime_type = models.TextField()
    byte_size = models.BigIntegerField()
    width = models.BigIntegerField()
    height = models.BigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_assets"
        indexes = [
            models.Index(
                fields=["job", "created_at", "id"], name="ai_190b5234d99baa7c3b03"
            ),
            models.Index(
                fields=["owner_email", "created_at", "id"],
                name="ai_705bbfa0ae4ba8fe4734",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["object_key"], name="ai_4ca1948508ae478bc4f7"
            ),
            models.UniqueConstraint(fields=["item"], name="ai_43dd6ce03454d3ba9475"),
            models.CheckConstraint(
                condition=models.Q(
                    mime_type__in=["image/png", "image/jpeg", "image/webp"]
                ),
                name="ai_ck_b816c0c93c29a189d035",
            ),
        ]


class AiSpaceDispatchReceipts(models.Model):
    id = models.TextField(primary_key=True)
    item_id = models.TextField()
    job_id = models.TextField()
    owner_email = models.TextField()
    actor_role = models.TextField()
    model_profile_id = models.TextField()
    model_profile_version = models.BigIntegerField()
    model_name = models.TextField()
    scene = models.TextField()
    size = models.TextField()
    prompt_digest = models.TextField()
    dispatched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_dispatch_receipts"
        indexes = [
            models.Index(
                fields=["model_profile_id", "dispatched_at", "id"],
                name="ai_c73a5d344ef51ebae249",
            ),
            models.Index(
                fields=["owner_email", "dispatched_at", "id"],
                name="ai_892be7fe1ba44feef138",
            ),
        ]
        constraints = [
            models.UniqueConstraint(fields=["item_id"], name="ai_e26766e9529859782ab9"),
        ]


class AiSpaceDispatchResults(models.Model):
    dispatch = models.OneToOneField(
        "AiSpaceDispatchReceipts",
        on_delete=models.CASCADE,
        db_column="dispatch_id",
        primary_key=True,
    )
    status = models.TextField()
    provider_request_id = models.TextField(default="")
    error_code = models.TextField(default="")
    usage_json = models.TextField(default="{}")
    estimated_cost_cents = models.BigIntegerField(null=True, blank=True)
    price_version = models.TextField(default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_dispatch_results"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["succeeded", "failed"]),
                name="ai_ck_a98b5a2e9f5a8817e4e6",
            ),
        ]


class AiSpaceJobItems(models.Model):
    id = models.TextField(primary_key=True)
    job = models.ForeignKey("AiSpaceJobs", on_delete=models.CASCADE, db_column="job_id")
    ordinal = models.BigIntegerField()
    status = models.TextField(default="queued")
    attempt_count = models.BigIntegerField(default=0)
    lease_token = models.TextField(default="")
    lease_epoch = models.BigIntegerField(default=0)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    dispatch_started_at = models.DateTimeField(null=True, blank=True)
    pending_object_key = models.TextField(default="")
    provider_request_id = models.TextField(default="")
    asset_id = models.TextField(null=True, blank=True)
    error_code = models.TextField(default="")
    error_message = models.TextField(default="")
    duration_ms = models.BigIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_job_items"
        indexes = [
            models.Index(fields=["job", "ordinal"], name="ai_7c128b02f4c486c7369a"),
            models.Index(
                fields=["status", "created_at", "job", "ordinal"],
                name="ai_663582f63f22ada00455",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "ordinal"], name="ai_37c21e14159df90cecc0"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["queued", "running", "succeeded", "failed", "cancelled"]
                ),
                name="ai_ck_f08ebc8d0f85c09433ed",
            ),
            models.CheckConstraint(
                condition=models.Q(ordinal__gte=1, ordinal__lte=4),
                name="ai_ck_c8add7794011a23a88e4",
            ),
        ]


class AiSpaceJobs(models.Model):
    id = models.TextField(primary_key=True)
    client_request_id = models.TextField()
    request_digest = models.TextField()
    owner_email = models.TextField()
    scope_json = models.TextField()
    scene = models.TextField()
    template = models.ForeignKey(
        "AiSpaceTemplates", on_delete=models.PROTECT, db_column="template_id"
    )
    template_name = models.TextField()
    template_version = models.BigIntegerField()
    model_profile = models.ForeignKey(
        "AiSpaceModelProfiles", on_delete=models.PROTECT, db_column="model_profile_id"
    )
    model_profile_name = models.TextField()
    model_profile_version = models.BigIntegerField()
    model_name = models.TextField()
    product_name = models.TextField()
    brand = models.TextField(default="")
    sku = models.TextField(default="")
    selling_points = models.TextField(default="")
    final_prompt = models.TextField()
    prompt_digest = models.TextField()
    size = models.TextField()
    requested_count = models.BigIntegerField()
    succeeded_count = models.BigIntegerField(default=0)
    failed_count = models.BigIntegerField(default=0)
    cancelled_count = models.BigIntegerField(default=0)
    status = models.TextField(default="queued")
    cancel_requested = models.BigIntegerField(default=0)
    error_code = models.TextField(default="")
    error_message = models.TextField(default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_jobs"
        indexes = [
            models.Index(
                fields=["status", "cancel_requested", "created_at", "id"],
                name="ai_0eeb328373e4ced85f05",
            ),
            models.Index(
                fields=["owner_email", "created_at", "id"],
                name="ai_fe03299350705be7b14e",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner_email", "client_request_id"],
                name="ai_7bc011f5b26cafe6b08a",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    scene__in=["product_main", "product_detail", "promotion"]
                ),
                name="ai_ck_f24492b337288a462d74",
            ),
            models.CheckConstraint(
                condition=models.Q(size__in=["1024x1024", "1024x1536", "1536x1024"]),
                name="ai_ck_601b389c90c4c8770fe8",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "queued",
                        "running",
                        "succeeded",
                        "partial",
                        "failed",
                        "cancelled",
                    ]
                ),
                name="ai_ck_120ddac759ce1c0051ce",
            ),
            models.CheckConstraint(
                condition=models.Q(cancel_requested__in=[0, 1]),
                name="ai_ck_62869cd64e221ffe9a7b",
            ),
            models.CheckConstraint(
                condition=models.Q(requested_count__gte=1, requested_count__lte=4),
                name="ai_ck_e4c35b366c33b6a0e8d3",
            ),
        ]


class AiSpaceModelProfiles(models.Model):
    id = models.TextField(primary_key=True)
    name = models.TextField()
    protocol = models.TextField(default="openai_images")
    model_name = models.TextField()
    base_url = models.TextField()
    api_key_encrypted = models.TextField(default="")
    api_key_suffix = models.TextField(default="")
    status = models.TextField(default="enabled")
    version = models.BigIntegerField(default=1)
    timeout_ms = models.BigIntegerField(default=90000)
    last_success_result = models.TextField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_model_profiles"
        indexes = [
            models.Index(
                fields=["status", "updated_at", "id"], name="ai_1eee8d0d97796f513144"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["enabled", "disabled"]),
                name="ai_ck_13bf89e36f2dfd4b45a5",
            ),
            models.CheckConstraint(
                condition=models.Q(timeout_ms__gte=3000, timeout_ms__lte=120000),
                name="ai_ck_f8acd5f4279b05c419a7",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="ai_ck_74314af32937a1df5aee"
            ),
        ]


class AiSpaceSchemaUpgrades(models.Model):
    id = models.TextField(primary_key=True)
    completed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_schema_upgrades"


class AiSpaceTemplates(models.Model):
    id = models.TextField(primary_key=True)
    scene = models.TextField()
    name = models.TextField()
    prompt_template = models.TextField()
    size = models.TextField(default="1024x1024")
    model_profile = models.ForeignKey(
        "AiSpaceModelProfiles",
        on_delete=models.PROTECT,
        db_column="model_profile_id",
        null=True,
        blank=True,
    )
    version = models.BigIntegerField(default=1)
    is_enabled = models.BigIntegerField(default=1)
    is_default = models.BigIntegerField(default=0)
    updated_by = models.TextField(default="system")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_space_templates"
        indexes = [
            models.Index(
                fields=["scene", "is_enabled", "is_default", "updated_at", "id"],
                name="ai_5bdb0f0461697a1852f2",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["scene"],
                condition=models.Q(is_default=1, is_enabled=1),
                name="ai_space_default_scene_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    scene__in=["product_main", "product_detail", "promotion"]
                ),
                name="ai_ck_ce115ebcf2e3105df0ef",
            ),
            models.CheckConstraint(
                condition=models.Q(size__in=["1024x1024", "1024x1536", "1536x1024"]),
                name="ai_ck_2abcec39977e40803a41",
            ),
            models.CheckConstraint(
                condition=models.Q(is_enabled__in=[0, 1]),
                name="ai_ck_591d3946985e5edd2c37",
            ),
            models.CheckConstraint(
                condition=models.Q(is_default__in=[0, 1]),
                name="ai_ck_45aeaede187242685d54",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="ai_ck_d36cc35cfdebafb7a960"
            ),
        ]


class AiSystemSettings(models.Model):
    key = models.TextField(primary_key=True)
    value_json = models.TextField()
    updated_by = models.TextField(default="")
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_system_settings"


class AiToolAuditLogs(models.Model):
    id = models.TextField(primary_key=True)
    request_id = models.TextField()
    invocation_id = models.TextField(default="")
    provider_call_id = models.TextField(null=True, blank=True)
    actor_email = models.TextField()
    actor_role = models.TextField()
    surface = models.TextField()
    tool_name = models.TextField()
    arguments_json = models.TextField(default="{}")
    status = models.TextField()
    row_count = models.BigIntegerField(null=True, blank=True)
    duration_ms = models.BigIntegerField(null=True, blank=True)
    response_digest = models.TextField(null=True, blank=True)
    error_code = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_tool_audit_logs"
        indexes = [
            models.Index(
                fields=["invocation_id", "created_at"], name="ai_7daace5654b492099a5f"
            ),
            models.Index(
                fields=["tool_name", "created_at"], name="ai_62424d80205d6d452006"
            ),
            models.Index(
                fields=["actor_email", "created_at"], name="ai_1ca4f2459a1422c597be"
            ),
        ]


class AiWorkflowEvents(models.Model):
    id = models.TextField(primary_key=True)
    run = models.ForeignKey(
        "AiWorkflowRuns", on_delete=models.CASCADE, db_column="run_id"
    )
    node_key = models.TextField(null=True, blank=True)
    owner_email = models.TextField()
    actor_email = models.TextField()
    event_type = models.TextField()
    from_status = models.TextField(default="")
    to_status = models.TextField(default="")
    run_version = models.BigIntegerField()
    details_json = models.TextField(default="{}")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_workflow_events"
        indexes = [
            models.Index(
                fields=["run", "created_at", "id"], name="ai_161bd273c54289370bc4"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(run_version__gte=1),
                name="ai_ck_b031db0045c71aaf0523",
            ),
        ]


class AiWorkflowNodeRuns(models.Model):
    id = models.TextField(primary_key=True)
    run = models.ForeignKey(
        "AiWorkflowRuns", on_delete=models.CASCADE, db_column="run_id"
    )
    node_key = models.TextField()
    position = models.BigIntegerField()
    node_type = models.TextField()
    depends_on_json = models.TextField(default="[]")
    instruction = models.TextField()
    input_json = models.TextField(default="{}")
    output_json = models.TextField(null=True, blank=True)
    status = models.TextField(default="pending")
    version = models.BigIntegerField(default=1)
    mutation_token = models.TextField(default="")
    agent_job = models.ForeignKey(
        "AiAgentJobs",
        on_delete=models.SET_NULL,
        db_column="agent_job_id",
        null=True,
        blank=True,
    )
    reviewer_email = models.TextField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.TextField(default="")
    error_message = models.TextField(default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_workflow_node_runs"
        indexes = [
            models.Index(
                fields=["run", "position", "status"], name="ai_075dae45bd2e75bba604"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "position"], name="ai_5b3c0dc89ee139c59645"
            ),
            models.UniqueConstraint(
                fields=["run", "node_key"], name="ai_f4766765b67496b98409"
            ),
            models.CheckConstraint(
                condition=models.Q(node_type__in=["agent", "human_review"]),
                name="ai_ck_08759a66b58691b2aab9",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "pending",
                        "running",
                        "waiting_review",
                        "completed",
                        "rejected",
                        "skipped",
                        "failed",
                        "cancelled",
                    ]
                ),
                name="ai_ck_8f872e249ae8ad4d2f88",
            ),
            models.CheckConstraint(
                condition=models.Q(position__gte=0, position__lte=23),
                name="ai_ck_57d8bdb1f04bd79f5c0b",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="ai_ck_98d2fbd72e8dce677366"
            ),
        ]


class AiWorkflowRuns(models.Model):
    id = models.TextField(primary_key=True)
    owner_email = models.TextField()
    client_request_id = models.TextField()
    request_digest = models.TextField()
    scope_json = models.TextField()
    name = models.TextField()
    graph_json = models.TextField()
    graph_digest = models.TextField()
    input_json = models.TextField(default="{}")
    output_json = models.TextField(null=True, blank=True)
    model_id = models.TextField(default="")
    model_version = models.BigIntegerField(default=0)
    allowed_tools_json = models.TextField(default="[]")
    tool_policy_digest = models.TextField(default="")
    provider_round_count = models.BigIntegerField(default=0)
    tool_call_count = models.BigIntegerField(default=0)
    provider_dispatch_started_at = models.DateTimeField(null=True, blank=True)
    dry_run = models.BigIntegerField(default=0)
    status = models.TextField(default="queued")
    current_node_key = models.TextField(null=True, blank=True)
    version = models.BigIntegerField(default=1)
    mutation_token = models.TextField(default="")
    cancel_requested = models.BigIntegerField(default=0)
    retryable = models.BigIntegerField(default=0)
    resume_count = models.BigIntegerField(default=0)
    attempt_count = models.BigIntegerField(default=0)
    lease_token = models.TextField(default="")
    lease_epoch = models.BigIntegerField(default=0)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(default=timezone.now)
    error_code = models.TextField(default="")
    error_message = models.TextField(default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_workflow_runs"
        indexes = [
            models.Index(
                fields=["status", "next_run_at", "created_at", "id"],
                name="ai_b03812a90d7ef2d47569",
            ),
            models.Index(
                fields=["owner_email", "created_at", "id"],
                name="ai_a393e7db5349bfde139e",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner_email", "client_request_id"],
                name="ai_ca04cddd5d43550fc394",
            ),
            models.CheckConstraint(
                condition=models.Q(dry_run__in=[0, 1]),
                name="ai_ck_b62d38b9d3cfbfaf4ce9",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "queued",
                        "running",
                        "waiting_review",
                        "paused",
                        "completed",
                        "failed",
                        "cancelled",
                    ]
                ),
                name="ai_ck_88d5c09e66ffd44225ce",
            ),
            models.CheckConstraint(
                condition=models.Q(cancel_requested__in=[0, 1]),
                name="ai_ck_8340259e51187630a73d",
            ),
            models.CheckConstraint(
                condition=models.Q(retryable__in=[0, 1]),
                name="ai_ck_d3ba55ff2c6c3b81fd6d",
            ),
            models.CheckConstraint(
                condition=models.Q(resume_count__gte=0, resume_count__lte=16),
                name="ai_ck_883053802866b5e1af6c",
            ),
            models.CheckConstraint(
                condition=models.Q(model_version__gte=0),
                name="ai_ck_f42ef7e78ad259514e1c",
            ),
            models.CheckConstraint(
                condition=models.Q(provider_round_count__gte=0),
                name="ai_ck_b629a8f2bb81e4ea0062",
            ),
            models.CheckConstraint(
                condition=models.Q(tool_call_count__gte=0),
                name="ai_ck_9f27c7d6abd612f30219",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="ai_ck_7afef6cb6e4a100ea9d4"
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_count__gte=0),
                name="ai_ck_321e47d03f02b0bb6c74",
            ),
            models.CheckConstraint(
                condition=models.Q(lease_epoch__gte=0),
                name="ai_ck_41c8f4141e4d03cf1568",
            ),
        ]


# Closed migration inventory; never constructed from a client supplied table name.
HISTORICAL_MODELS = {
    "ai_agent_checkpoints": AiAgentCheckpoints,
    "ai_agent_events": AiAgentEvents,
    "ai_agent_jobs": AiAgentJobs,
    "ai_agent_provider_dispatches": AiAgentProviderDispatches,
    "ai_agent_provider_results": AiAgentProviderResults,
    "ai_agent_tool_dispatches": AiAgentToolDispatches,
    "ai_agent_tool_results": AiAgentToolResults,
    "ai_analysis_runs": AiAnalysisRuns,
    "ai_artifact_deliveries": AiArtifactDeliveries,
    "ai_artifacts": AiArtifacts,
    "ai_channel_callback_events": AiChannelCallbackEvents,
    "ai_channels": AiChannels,
    "ai_chat_provider_dispatches": AiChatProviderDispatches,
    "ai_chat_request_receipts": AiChatRequestReceipts,
    "ai_conversation_deletion_audits": AiConversationDeletionAudits,
    "ai_conversation_messages": AiConversationMessages,
    "ai_conversation_scopes": AiConversationScopes,
    "ai_conversations": AiConversations,
    "ai_knowledge_entries": AiKnowledgeEntries,
    "ai_memory_audit_logs": AiMemoryAuditLogs,
    "ai_memory_commit_guards": AiMemoryCommitGuards,
    "ai_memory_entries": AiMemoryEntries,
    "ai_models": AiModels,
    "ai_space_admin_audits": AiSpaceAdminAudits,
    "ai_space_asset_cleanup_queue": AiSpaceAssetCleanupQueue,
    "ai_space_asset_favorites": AiSpaceAssetFavorites,
    "ai_space_assets": AiSpaceAssets,
    "ai_space_dispatch_receipts": AiSpaceDispatchReceipts,
    "ai_space_dispatch_results": AiSpaceDispatchResults,
    "ai_space_job_items": AiSpaceJobItems,
    "ai_space_jobs": AiSpaceJobs,
    "ai_space_model_profiles": AiSpaceModelProfiles,
    "ai_space_schema_upgrades": AiSpaceSchemaUpgrades,
    "ai_space_templates": AiSpaceTemplates,
    "ai_system_settings": AiSystemSettings,
    "ai_tool_audit_logs": AiToolAuditLogs,
    "ai_workflow_events": AiWorkflowEvents,
    "ai_workflow_node_runs": AiWorkflowNodeRuns,
    "ai_workflow_runs": AiWorkflowRuns,
}


class AiPromptSettingsRevision(models.Model):
    version = models.PositiveIntegerField(primary_key=True)
    config_json = models.TextField()
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)
    restored_from = models.PositiveIntegerField(null=True)

    class Meta:
        db_table = "ai_prompt_settings_revisions"

from .report_models import AiLibraryRevision, AiExecutionGuidance, AiReportRun, AiReportDelivery  # noqa: E402,F401
from .business_models import AiBusinessEvidenceRun, AiBusinessEvidenceChunk, AiBusinessEvidenceSource, AiBusinessSourceToolReceipt, AiBusinessV3ReportIntent  # noqa: E402,F401
from .business_v4_models import AiBusinessV4Run, AiBusinessV4Source, AiBusinessV4Chunk, AiBusinessV4ToolReceipt  # noqa: E402,F401
from .business_v4_validation_models import AiBusinessV4ValidationAttempt, AiBusinessV4ValidationSegment  # noqa: E402,F401
from .business_v4_seal_models import AiBusinessV4Seal  # noqa: E402,F401
from .business_v4_seal_ticket_models import (  # noqa: E402,F401
    AiBusinessV4SealTicket, AiBusinessV4SealClaim)
from .business_v4_seal_consumption_models import AiBusinessV4SealConsumption  # noqa: E402,F401
from .business_v4_sealer_replay_progress_models import AiBusinessV4SealerReplayProgress  # noqa: E402,F401
from .business_v4_period_plan_models import AiBusinessV4PeriodPlanCandidate  # noqa: E402,F401
from .business_market_v2_material_models import AiBusinessMarketV2Material  # noqa: E402,F401
from .business_market_v2_context_models import AiBusinessMarketV2ContextProof  # noqa: E402,F401
from .business_market_v2_read_receipt_models import AiBusinessMarketV2ReadReceipt  # noqa: E402,F401
from .business_market_v2_execution_plan_models import AiBusinessMarketV2ExecutionPlan  # noqa: E402,F401
from .business_market_v2_cost_models import AiBusinessMarketV2CostLedgerCandidate  # noqa: E402,F401
from .business_market_v2_paid_models import (  # noqa: E402,F401
    AiBusinessMarketV2PaidAuthority, AiBusinessMarketV2RoundReservation,
    AiBusinessMarketV2RoundEvent)
from .business_file_models import AiBusinessFileRun, AiBusinessFileChunk, AiBusinessVolumeChunk  # noqa: E402,F401
from .business_promotion_budget_v10_attestation_models import AiBusinessPromotionBudgetV10Attestation  # noqa: E402,F401
from .business_promotion_budget_v11_attestation_models import AiBusinessPromotionBudgetV11Attestation  # noqa: E402,F401
from .business_budget_models import AiBusinessBudgetPlan  # noqa: E402,F401
from .business_screening_models import AiBusinessScreeningRun, AiBusinessScreeningPage  # noqa: E402,F401
