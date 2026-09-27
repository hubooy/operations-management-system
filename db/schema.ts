import { sql } from "drizzle-orm";
import { index, integer, real, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

/** Configured AI model endpoints; credentials are stored encrypted. */
export const aiModels = sqliteTable("ai_models", {
  id: text("id").primaryKey(),
  version: integer("version").notNull().default(1),
  name: text("name").notNull(),
  protocol: text("protocol").notNull(),
  modelType: text("model_type").notNull(),
  modelName: text("model_name").notNull(),
  baseUrl: text("base_url").notNull().default(""),
  apiKeyEncrypted: text("api_key_encrypted").notNull().default(""),
  apiKeySuffix: text("api_key_suffix").notNull().default(""),
  isDefaultTextModel: integer("is_default_text_model", { mode: "boolean" }).notNull().default(false),
  status: text("status").notNull(),
  lastTestResult: text("last_test_result"),
  lastTestedAt: text("last_tested_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("ai_models_default_text_uq").on(table.isDefaultTextModel).where(sql`${table.isDefaultTextModel} = 1 AND ${table.status} = 'enabled' AND ${table.modelType} = 'text'`),
  index("ai_models_status_idx").on(table.status, table.modelType, table.updatedAt),
]);

export const aiChannels = sqliteTable("ai_channels", {
  id: text("id").primaryKey(), name: text("name").notNull(), kind: text("kind").notNull(), status: text("status").notNull(),
  sendEnabled: integer("send_enabled", { mode: "boolean" }).notNull().default(false), callbackEnabled: integer("callback_enabled", { mode: "boolean" }).notNull().default(false),
  webhookUrl: text("webhook_url").notNull().default(""), callbackTokenEncrypted: text("callback_token_encrypted").notNull().default(""), callbackTokenSuffix: text("callback_token_suffix").notNull().default(""),
  aesKeyEncrypted: text("aes_key_encrypted").notNull().default(""), aesKeySuffix: text("aes_key_suffix").notNull().default(""), receiverId: text("receiver_id").notNull().default(""), lastTestResult: text("last_test_result"), lastTestedAt: text("last_tested_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`), updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("ai_channels_status_idx").on(table.status, table.kind, table.updatedAt)]);

/** Idempotency receipts for signed chat-platform callbacks. Callback payloads are never retained. */
export const aiChannelCallbackEvents = sqliteTable("ai_channel_callback_events", {
  id: text("id").primaryKey(),
  channelId: text("channel_id").notNull(),
  eventKey: text("event_key").notNull(),
  payloadDigest: text("payload_digest").notNull(),
  receivedAt: text("received_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("ai_channel_callback_events_channel_event_uq").on(table.channelId, table.eventKey),
  index("ai_channel_callback_events_received_idx").on(table.channelId, table.receivedAt),
]);

export const aiConversations = sqliteTable("ai_conversations", {
  id: text("id").primaryKey(), title: text("title").notNull(), modelId: text("model_id"), createdBy: text("created_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`), updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("ai_conversations_creator_updated_idx").on(table.createdBy, table.updatedAt)]);

/** Immutable authorization scope captured when an AI conversation is created. */
export const aiConversationScopes = sqliteTable("ai_conversation_scopes", {
  conversationId: text("conversation_id").primaryKey().references(() => aiConversations.id, { onDelete: "cascade" }),
  scopeJson: text("scope_json").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("ai_conversation_scopes_scope_created_idx").on(table.scopeJson, table.createdAt)]);

export const aiConversationMessages = sqliteTable("ai_conversation_messages", {
  id: text("id").primaryKey(), conversationId: text("conversation_id").notNull(), role: text("role").notNull(), content: text("content").notNull(),
  messageKind: text("message_kind").notNull().default("message"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("ai_conversation_messages_conversation_idx").on(table.conversationId, table.createdAt),
  index("ai_conversation_messages_context_idx").on(table.conversationId, table.messageKind, table.createdAt),
]);

/** Durable idempotency fence for chat requests that may incur provider charges. */
export const aiChatRequestReceipts = sqliteTable("ai_chat_request_receipts", {
  id: text("id").primaryKey(),
  ownerEmail: text("owner_email").notNull(),
  clientRequestId: text("client_request_id").notNull(),
  requestDigest: text("request_digest").notNull(),
  status: text("status").notNull(),
  modelId: text("model_id"),
  conversationId: text("conversation_id").references(() => aiConversations.id, { onDelete: "set null" }),
  assistantMessageId: text("assistant_message_id"),
  resultJson: text("result_json"),
  errorCode: text("error_code"),
  admittedAt: text("admitted_at"),
  providerStartedAt: text("provider_started_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  completedAt: text("completed_at"),
}, (table) => [
  uniqueIndex("ai_chat_request_receipts_owner_client_uq").on(table.ownerEmail, table.clientRequestId),
  index("ai_chat_request_receipts_status_updated_idx").on(table.status, table.updatedAt),
  index("ai_chat_request_receipts_conversation_idx").on(table.conversationId, table.createdAt),
  index("ai_chat_request_receipts_provider_started_idx").on(table.providerStartedAt),
  index("ai_chat_request_receipts_owner_dispatch_idx").on(table.ownerEmail, table.providerStartedAt),
  index("ai_chat_request_receipts_model_dispatch_idx").on(table.modelId, table.providerStartedAt),
  index("ai_chat_request_receipts_status_owner_model_idx").on(table.status, table.ownerEmail, table.modelId, table.providerStartedAt),
  index("ai_chat_request_receipts_owner_admitted_idx").on(table.ownerEmail, table.admittedAt),
  index("ai_chat_request_receipts_status_admitted_idx").on(table.status, table.admittedAt),
  index("ai_chat_request_receipts_status_model_admitted_idx").on(table.status, table.modelId, table.admittedAt),
]);

/** One durable budget charge immediately before each AI chat provider HTTP request. */
export const aiChatProviderDispatches = sqliteTable("ai_chat_provider_dispatches", {
  id: text("id").primaryKey(),
  receiptId: text("receipt_id").notNull().references(() => aiChatRequestReceipts.id, { onDelete: "cascade" }),
  ownerEmail: text("owner_email").notNull(),
  modelId: text("model_id").notNull(),
  dispatchOrdinal: integer("dispatch_ordinal").notNull(),
  reservedAt: text("reserved_at").notNull(),
  providerCalledAt: text("provider_called_at"),
}, (table) => [
  uniqueIndex("ai_chat_provider_dispatches_receipt_ordinal_uq").on(table.receiptId, table.dispatchOrdinal),
  index("ai_chat_provider_dispatches_owner_reserved_idx").on(table.ownerEmail, table.reservedAt),
  index("ai_chat_provider_dispatches_model_reserved_idx").on(table.modelId, table.reservedAt),
  index("ai_chat_provider_dispatches_reserved_idx").on(table.reservedAt),
]);

/** Append-only proof that an authorized actor deleted an AI conversation; raw messages are never retained. */
export const aiConversationDeletionAudits = sqliteTable("ai_conversation_deletion_audits", {
  auditId: text("audit_id").primaryKey(),
  conversationId: text("conversation_id").notNull().unique(),
  conversationOwner: text("conversation_owner").notNull(),
  actorEmail: text("actor_email").notNull(),
  actorRole: text("actor_role").notNull(),
  reason: text("reason").notNull(),
  deletedMessageCount: integer("deleted_message_count").notNull().default(0),
  deletedArtifactCount: integer("deleted_artifact_count").notNull().default(0),
  deletedAt: text("deleted_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("ai_conversation_deletion_audits_actor_deleted_idx").on(table.actorEmail, table.deletedAt)]);

/** Explicit, owner-only global memories. Content remains untrusted data at retrieval time. */
export const aiMemoryEntries = sqliteTable("ai_memory_entries", {
  id: text("id").primaryKey(),
  ownerEmail: text("owner_email").notNull(),
  kind: text("kind").notNull(),
  memoryKey: text("memory_key").notNull(),
  memoryKeyNormalized: text("memory_key_normalized").notNull(),
  content: text("content").notNull(),
  contentDigest: text("content_digest").notNull(),
  scopeMode: text("scope_mode").notNull(),
  scopeJson: text("scope_json").notNull(),
  scopeDigest: text("scope_digest").notNull(),
  status: text("status").notNull().default("active"),
  version: integer("version").notNull().default(1),
  source: text("source").notNull(),
  sourceConversationId: text("source_conversation_id"),
  sourceMessageId: text("source_message_id"),
  lastOperationId: text("last_operation_id").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  archivedAt: text("archived_at"),
}, (table) => [
  uniqueIndex("ai_memory_entries_active_key_uq")
    .on(table.ownerEmail, table.kind, table.memoryKeyNormalized, table.scopeDigest)
    .where(sql`${table.status} = 'active'`),
  index("ai_memory_entries_owner_status_updated_idx")
    .on(table.ownerEmail, table.status, table.updatedAt, table.id),
]);

/** Append-only audit metadata; raw memory content is deliberately excluded. */
export const aiMemoryAuditLogs = sqliteTable("ai_memory_audit_logs", {
  id: text("id").primaryKey(),
  operationId: text("operation_id").notNull().unique(),
  requestId: text("request_id").notNull(),
  memoryId: text("memory_id").notNull(),
  ownerEmail: text("owner_email").notNull(),
  actorRole: text("actor_role").notNull(),
  operation: text("operation").notNull(),
  status: text("status").notNull(),
  scopeDigest: text("scope_digest").notNull(),
  beforeDigest: text("before_digest"),
  afterDigest: text("after_digest"),
  resultVersion: integer("result_version").notNull(),
  policyVersion: text("policy_version").notNull(),
  gateResultsJson: text("gate_results_json").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("ai_memory_audit_logs_owner_created_idx").on(table.ownerEmail, table.createdAt, table.id),
  index("ai_memory_audit_logs_memory_created_idx").on(table.memoryId, table.createdAt, table.id),
]);

/** Transient transaction fence proving that a memory mutation emitted its audit row. */
export const aiMemoryCommitGuards = sqliteTable("ai_memory_commit_guards", {
  operationId: text("operation_id").primaryKey(),
  auditPresent: integer("audit_present").notNull(),
});

/** Redacted owner-only receipts for deterministic analysis-sandbox runs. */
export const aiAnalysisRuns = sqliteTable("ai_analysis_runs", {
  id: text("id").primaryKey(),
  ownerEmail: text("owner_email").notNull(),
  actorRole: text("actor_role").notNull(),
  scopeJson: text("scope_json").notNull(),
  dataset: text("dataset").notNull(),
  queryDigest: text("query_digest").notNull(),
  planDigest: text("plan_digest").notNull(),
  operationsJson: text("operations_json").notNull(),
  dataCutoffDate: text("data_cutoff_date"),
  sourceRows: integer("source_rows").notNull(),
  returnedRows: integer("returned_rows").notNull(),
  truncated: integer("truncated", { mode: "boolean" }).notNull().default(false),
  resultDigest: text("result_digest").notNull(),
  requestId: text("request_id").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("ai_analysis_runs_owner_created_idx").on(table.ownerEmail, table.createdAt, table.id),
  index("ai_analysis_runs_dataset_created_idx").on(table.dataset, table.createdAt, table.id),
]);

/** Durable owner-scoped Agent jobs. The stored payload is passive JSON only. */
export const aiAgentJobs = sqliteTable("ai_agent_jobs", {
  id: text("id").primaryKey(),
  ownerEmail: text("owner_email").notNull(),
  clientRequestId: text("client_request_id").notNull(),
  requestDigest: text("request_digest").notNull(),
  scopeJson: text("scope_json").notNull(),
  task: text("task").notNull(),
  inputJson: text("input_json").notNull().default("{}"),
  stateJson: text("state_json").notNull().default("{}"),
  outputJson: text("output_json"),
  status: text("status").notNull().default("queued"),
  phase: text("phase").notNull().default("queued"),
  stepIndex: integer("step_index").notNull().default(0),
  version: integer("version").notNull().default(1),
  mutationToken: text("mutation_token").notNull().default(""),
  cancelRequested: integer("cancel_requested", { mode: "boolean" }).notNull().default(false),
  retryable: integer("retryable", { mode: "boolean" }).notNull().default(false),
  resumeCount: integer("resume_count").notNull().default(0),
  attemptCount: integer("attempt_count").notNull().default(0),
  leaseToken: text("lease_token").notNull().default(""),
  leaseEpoch: integer("lease_epoch").notNull().default(0),
  leaseExpiresAt: text("lease_expires_at"),
  nextRunAt: text("next_run_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  modelId: text("model_id").notNull().default(""),
  modelVersion: integer("model_version").notNull().default(0),
  allowedToolsJson: text("allowed_tools_json").notNull().default("[]"),
  toolPolicyDigest: text("tool_policy_digest").notNull().default(""),
  providerRoundCount: integer("provider_round_count").notNull().default(0),
  toolCallCount: integer("tool_call_count").notNull().default(0),
  providerDispatchStartedAt: text("provider_dispatch_started_at"),
  workflowRunId: text("workflow_run_id"),
  workflowNodeKey: text("workflow_node_key"),
  errorCode: text("error_code").notNull().default(""),
  errorMessage: text("error_message").notNull().default(""),
  startedAt: text("started_at"),
  completedAt: text("completed_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("ai_agent_jobs_owner_client_request_uq").on(table.ownerEmail, table.clientRequestId),
  index("ai_agent_jobs_owner_created_idx").on(table.ownerEmail, table.createdAt, table.id),
  index("ai_agent_jobs_runnable_idx").on(table.status, table.nextRunAt, table.createdAt, table.id),
  uniqueIndex("ai_agent_jobs_workflow_node_uq")
    .on(table.workflowRunId, table.workflowNodeKey)
    .where(sql`${table.workflowRunId} IS NOT NULL AND ${table.workflowNodeKey} IS NOT NULL`),
]);

export const aiAgentCheckpoints = sqliteTable("ai_agent_checkpoints", {
  id: text("id").primaryKey(),
  jobId: text("job_id").notNull().references(() => aiAgentJobs.id, { onDelete: "cascade" }),
  ordinal: integer("ordinal").notNull(),
  kind: text("kind").notNull(),
  stateJson: text("state_json").notNull().default("{}"),
  outputDigest: text("output_digest").notNull().default(""),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("ai_agent_checkpoints_job_ordinal_uq").on(table.jobId, table.ordinal),
  index("ai_agent_checkpoints_job_idx").on(table.jobId, table.ordinal),
]);

export const aiAgentEvents = sqliteTable("ai_agent_events", {
  id: text("id").primaryKey(),
  jobId: text("job_id").notNull().references(() => aiAgentJobs.id, { onDelete: "cascade" }),
  ownerEmail: text("owner_email").notNull(),
  actorEmail: text("actor_email").notNull(),
  eventType: text("event_type").notNull(),
  fromStatus: text("from_status").notNull().default(""),
  toStatus: text("to_status").notNull().default(""),
  jobVersion: integer("job_version").notNull(),
  detailsJson: text("details_json").notNull().default("{}"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("ai_agent_events_job_created_idx").on(table.jobId, table.createdAt, table.id)]);

/** Irreversible, at-most-once boundary immediately before each Agent provider HTTP request. */
export const aiAgentProviderDispatches = sqliteTable("ai_agent_provider_dispatches", {
  id: text("id").primaryKey(),
  jobId: text("job_id").notNull().references(() => aiAgentJobs.id),
  dispatchOrdinal: integer("dispatch_ordinal").notNull(),
  ownerEmail: text("owner_email").notNull(),
  actorRole: text("actor_role").notNull(),
  modelId: text("model_id").notNull(),
  modelVersion: integer("model_version").notNull(),
  toolPolicyDigest: text("tool_policy_digest").notNull(),
  requestDigest: text("request_digest").notNull(),
  state: text("state").notNull().default("calling"),
  leaseEpoch: integer("lease_epoch").notNull(),
  reservedAt: text("reserved_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  providerCalledAt: text("provider_called_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  errorCode: text("error_code").notNull().default(""),
  errorMessage: text("error_message").notNull().default(""),
  completedAt: text("completed_at"),
}, (table) => [
  uniqueIndex("ai_agent_provider_dispatches_job_ordinal_uq").on(table.jobId, table.dispatchOrdinal),
  index("ai_agent_provider_dispatches_owner_reserved_idx").on(table.ownerEmail, table.reservedAt),
  index("ai_agent_provider_dispatches_model_reserved_idx").on(table.modelId, table.reservedAt),
  index("ai_agent_provider_dispatches_state_reserved_idx").on(table.state, table.reservedAt),
]);

/** Immutable normalized provider response, persisted before a job checkpoint can advance. */
export const aiAgentProviderResults = sqliteTable("ai_agent_provider_results", {
  dispatchId: text("dispatch_id").primaryKey().references(() => aiAgentProviderDispatches.id),
  responseJson: text("response_json").notNull(),
  responseDigest: text("response_digest").notNull(),
  usageJson: text("usage_json").notNull().default("{}"),
  providerRequestId: text("provider_request_id").notNull().default(""),
  completedAt: text("completed_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

/** Irreversible boundary before a model-requested, registry-approved read-only tool call. */
export const aiAgentToolDispatches = sqliteTable("ai_agent_tool_dispatches", {
  id: text("id").primaryKey(),
  jobId: text("job_id").notNull().references(() => aiAgentJobs.id),
  providerDispatchId: text("provider_dispatch_id").notNull().references(() => aiAgentProviderDispatches.id),
  toolCallOrdinal: integer("tool_call_ordinal").notNull(),
  providerCallId: text("provider_call_id").notNull(),
  toolName: text("tool_name").notNull(),
  argumentsJson: text("arguments_json").notNull(),
  argumentsDigest: text("arguments_digest").notNull(),
  invocationId: text("invocation_id").notNull(),
  state: text("state").notNull().default("calling"),
  leaseEpoch: integer("lease_epoch").notNull(),
  reservedAt: text("reserved_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  toolCalledAt: text("tool_called_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  errorCode: text("error_code").notNull().default(""),
  errorMessage: text("error_message").notNull().default(""),
  completedAt: text("completed_at"),
}, (table) => [
  uniqueIndex("ai_agent_tool_dispatches_job_ordinal_uq").on(table.jobId, table.toolCallOrdinal),
  uniqueIndex("ai_agent_tool_dispatches_provider_call_uq").on(table.providerDispatchId, table.providerCallId),
  index("ai_agent_tool_dispatches_job_state_idx").on(table.jobId, table.state, table.toolCallOrdinal),
]);

/** Immutable normalized tool result, persisted before a job checkpoint can advance. */
export const aiAgentToolResults = sqliteTable("ai_agent_tool_results", {
  toolDispatchId: text("tool_dispatch_id").primaryKey().references(() => aiAgentToolDispatches.id),
  resultJson: text("result_json").notNull(),
  resultDigest: text("result_digest").notNull(),
  completedAt: text("completed_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const aiWorkflowRuns = sqliteTable("ai_workflow_runs", {
  id: text("id").primaryKey(),
  ownerEmail: text("owner_email").notNull(),
  clientRequestId: text("client_request_id").notNull(),
  requestDigest: text("request_digest").notNull(),
  scopeJson: text("scope_json").notNull(),
  name: text("name").notNull(),
  graphJson: text("graph_json").notNull(),
  graphDigest: text("graph_digest").notNull(),
  inputJson: text("input_json").notNull().default("{}"),
  outputJson: text("output_json"),
  dryRun: integer("dry_run", { mode: "boolean" }).notNull().default(false),
  status: text("status").notNull().default("queued"),
  currentNodeKey: text("current_node_key"),
  version: integer("version").notNull().default(1),
  mutationToken: text("mutation_token").notNull().default(""),
  cancelRequested: integer("cancel_requested", { mode: "boolean" }).notNull().default(false),
  retryable: integer("retryable", { mode: "boolean" }).notNull().default(false),
  resumeCount: integer("resume_count").notNull().default(0),
  attemptCount: integer("attempt_count").notNull().default(0),
  leaseToken: text("lease_token").notNull().default(""),
  leaseEpoch: integer("lease_epoch").notNull().default(0),
  leaseExpiresAt: text("lease_expires_at"),
  nextRunAt: text("next_run_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  modelId: text("model_id").notNull().default(""),
  modelVersion: integer("model_version").notNull().default(0),
  allowedToolsJson: text("allowed_tools_json").notNull().default("[]"),
  toolPolicyDigest: text("tool_policy_digest").notNull().default(""),
  providerRoundCount: integer("provider_round_count").notNull().default(0),
  toolCallCount: integer("tool_call_count").notNull().default(0),
  providerDispatchStartedAt: text("provider_dispatch_started_at"),
  errorCode: text("error_code").notNull().default(""),
  errorMessage: text("error_message").notNull().default(""),
  startedAt: text("started_at"),
  completedAt: text("completed_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("ai_workflow_runs_owner_client_request_uq").on(table.ownerEmail, table.clientRequestId),
  index("ai_workflow_runs_owner_created_idx").on(table.ownerEmail, table.createdAt, table.id),
  index("ai_workflow_runs_runnable_idx").on(table.status, table.nextRunAt, table.createdAt, table.id),
]);

export const aiWorkflowNodeRuns = sqliteTable("ai_workflow_node_runs", {
  id: text("id").primaryKey(),
  runId: text("run_id").notNull().references(() => aiWorkflowRuns.id, { onDelete: "cascade" }),
  nodeKey: text("node_key").notNull(),
  position: integer("position").notNull(),
  nodeType: text("node_type").notNull(),
  dependsOnJson: text("depends_on_json").notNull().default("[]"),
  instruction: text("instruction").notNull(),
  inputJson: text("input_json").notNull().default("{}"),
  outputJson: text("output_json"),
  status: text("status").notNull().default("pending"),
  version: integer("version").notNull().default(1),
  mutationToken: text("mutation_token").notNull().default(""),
  agentJobId: text("agent_job_id").references(() => aiAgentJobs.id, { onDelete: "set null" }),
  reviewerEmail: text("reviewer_email"),
  reviewedAt: text("reviewed_at"),
  errorCode: text("error_code").notNull().default(""),
  errorMessage: text("error_message").notNull().default(""),
  startedAt: text("started_at"),
  completedAt: text("completed_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("ai_workflow_node_runs_run_key_uq").on(table.runId, table.nodeKey),
  uniqueIndex("ai_workflow_node_runs_run_position_uq").on(table.runId, table.position),
  uniqueIndex("ai_workflow_node_runs_agent_job_uq")
    .on(table.agentJobId)
    .where(sql`${table.agentJobId} IS NOT NULL`),
  index("ai_workflow_node_runs_run_position_idx").on(table.runId, table.position, table.status),
]);

export const aiWorkflowEvents = sqliteTable("ai_workflow_events", {
  id: text("id").primaryKey(),
  runId: text("run_id").notNull().references(() => aiWorkflowRuns.id, { onDelete: "cascade" }),
  nodeKey: text("node_key"),
  ownerEmail: text("owner_email").notNull(),
  actorEmail: text("actor_email").notNull(),
  eventType: text("event_type").notNull(),
  fromStatus: text("from_status").notNull().default(""),
  toStatus: text("to_status").notNull().default(""),
  runVersion: integer("run_version").notNull(),
  detailsJson: text("details_json").notNull().default("{}"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("ai_workflow_events_run_created_idx").on(table.runId, table.createdAt, table.id)]);

/** Independent image-generation providers for the AI Space workspace. */
export const aiSpaceModelProfiles = sqliteTable("ai_space_model_profiles", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  protocol: text("protocol").notNull().default("openai_images"),
  modelName: text("model_name").notNull(),
  baseUrl: text("base_url").notNull(),
  apiKeyEncrypted: text("api_key_encrypted").notNull().default(""),
  apiKeySuffix: text("api_key_suffix").notNull().default(""),
  status: text("status").notNull().default("enabled"),
  version: integer("version").notNull().default(1),
  timeoutMs: integer("timeout_ms").notNull().default(90_000),
  lastSuccessResult: text("last_success_result"),
  lastSuccessAt: text("last_success_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("ai_space_model_profiles_status_updated_idx").on(table.status, table.updatedAt, table.id)]);

/** Versioned, administrator-managed prompt templates for bounded product-image scenes. */
export const aiSpaceTemplates = sqliteTable("ai_space_templates", {
  id: text("id").primaryKey(),
  scene: text("scene").notNull(),
  name: text("name").notNull(),
  promptTemplate: text("prompt_template").notNull(),
  size: text("size").notNull().default("1024x1024"),
  modelProfileId: text("model_profile_id").references(() => aiSpaceModelProfiles.id, { onDelete: "restrict" }),
  version: integer("version").notNull().default(1),
  isEnabled: integer("is_enabled", { mode: "boolean" }).notNull().default(true),
  isDefault: integer("is_default", { mode: "boolean" }).notNull().default(false),
  updatedBy: text("updated_by").notNull().default("system"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("ai_space_templates_default_scene_uq").on(table.scene).where(sql`${table.isDefault} = 1 AND ${table.isEnabled} = 1`),
  index("ai_space_templates_scene_enabled_idx").on(table.scene, table.isEnabled, table.isDefault, table.updatedAt, table.id),
]);

/** Immutable generation intent plus authorization scope captured at submission time. */
export const aiSpaceJobs = sqliteTable("ai_space_jobs", {
  id: text("id").primaryKey(),
  clientRequestId: text("client_request_id").notNull(),
  requestDigest: text("request_digest").notNull(),
  ownerEmail: text("owner_email").notNull(),
  scopeJson: text("scope_json").notNull(),
  scene: text("scene").notNull(),
  templateId: text("template_id").notNull().references(() => aiSpaceTemplates.id, { onDelete: "restrict" }),
  templateName: text("template_name").notNull(),
  templateVersion: integer("template_version").notNull(),
  modelProfileId: text("model_profile_id").notNull().references(() => aiSpaceModelProfiles.id, { onDelete: "restrict" }),
  modelProfileName: text("model_profile_name").notNull(),
  modelProfileVersion: integer("model_profile_version").notNull(),
  modelName: text("model_name").notNull(),
  productName: text("product_name").notNull(),
  brand: text("brand").notNull().default(""),
  sku: text("sku").notNull().default(""),
  sellingPoints: text("selling_points").notNull().default(""),
  finalPrompt: text("final_prompt").notNull(),
  promptDigest: text("prompt_digest").notNull(),
  size: text("size").notNull(),
  requestedCount: integer("requested_count").notNull(),
  succeededCount: integer("succeeded_count").notNull().default(0),
  failedCount: integer("failed_count").notNull().default(0),
  cancelledCount: integer("cancelled_count").notNull().default(0),
  status: text("status").notNull().default("queued"),
  cancelRequested: integer("cancel_requested", { mode: "boolean" }).notNull().default(false),
  errorCode: text("error_code").notNull().default(""),
  errorMessage: text("error_message").notNull().default(""),
  startedAt: text("started_at"),
  completedAt: text("completed_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("ai_space_jobs_owner_client_request_uq").on(table.ownerEmail, table.clientRequestId),
  index("ai_space_jobs_owner_created_idx").on(table.ownerEmail, table.createdAt, table.id),
  index("ai_space_jobs_status_created_idx").on(table.status, table.cancelRequested, table.createdAt, table.id),
]);

/** Per-image execution state with fencing to prevent stale Worker completion. */
export const aiSpaceJobItems = sqliteTable("ai_space_job_items", {
  id: text("id").primaryKey(),
  jobId: text("job_id").notNull().references(() => aiSpaceJobs.id, { onDelete: "cascade" }),
  ordinal: integer("ordinal").notNull(),
  status: text("status").notNull().default("queued"),
  attemptCount: integer("attempt_count").notNull().default(0),
  leaseToken: text("lease_token").notNull().default(""),
  leaseEpoch: integer("lease_epoch").notNull().default(0),
  leaseExpiresAt: text("lease_expires_at"),
  dispatchStartedAt: text("dispatch_started_at"),
  pendingObjectKey: text("pending_object_key").notNull().default(""),
  providerRequestId: text("provider_request_id").notNull().default(""),
  assetId: text("asset_id"),
  errorCode: text("error_code").notNull().default(""),
  errorMessage: text("error_message").notNull().default(""),
  durationMs: integer("duration_ms"),
  startedAt: text("started_at"),
  completedAt: text("completed_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("ai_space_job_items_job_ordinal_uq").on(table.jobId, table.ordinal),
  index("ai_space_job_items_runnable_idx").on(table.status, table.createdAt, table.jobId, table.ordinal),
  index("ai_space_job_items_job_idx").on(table.jobId, table.ordinal),
]);

/** Validated image metadata; the image bytes remain private in R2. */
export const aiSpaceAssets = sqliteTable("ai_space_assets", {
  id: text("id").primaryKey(),
  jobId: text("job_id").notNull().references(() => aiSpaceJobs.id, { onDelete: "cascade" }),
  itemId: text("item_id").notNull().references(() => aiSpaceJobItems.id, { onDelete: "cascade" }).unique(),
  ownerEmail: text("owner_email").notNull(),
  scopeJson: text("scope_json").notNull(),
  scene: text("scene").notNull(),
  objectKey: text("object_key").notNull().unique(),
  contentSha256: text("content_sha256").notNull(),
  mimeType: text("mime_type").notNull(),
  byteSize: integer("byte_size").notNull(),
  width: integer("width").notNull(),
  height: integer("height").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("ai_space_assets_owner_created_idx").on(table.ownerEmail, table.createdAt, table.id),
  index("ai_space_assets_job_idx").on(table.jobId, table.createdAt, table.id),
]);

export const aiSpaceAssetFavorites = sqliteTable("ai_space_asset_favorites", {
  assetId: text("asset_id").notNull().references(() => aiSpaceAssets.id, { onDelete: "cascade" }),
  actorEmail: text("actor_email").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("ai_space_asset_favorites_asset_actor_uq").on(table.assetId, table.actorEmail),
  index("ai_space_asset_favorites_actor_created_idx").on(table.actorEmail, table.createdAt, table.assetId),
]);

/** Best-effort deletion outbox for orphaned AI Space objects. */
export const aiSpaceAssetCleanupQueue = sqliteTable("ai_space_asset_cleanup_queue", {
  objectKey: text("object_key").primaryKey(),
  attemptCount: integer("attempt_count").notNull().default(0),
  lastError: text("last_error").notNull().default(""),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

/** Durable markers make crash-safe runtime upgrades repeatable across Worker isolates. */
export const aiSpaceSchemaUpgrades = sqliteTable("ai_space_schema_upgrades", {
  id: text("id").primaryKey(),
  completedAt: text("completed_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

/** Append-only, redacted audit receipts for AI Space administrator mutations. */
export const aiSpaceAdminAudits = sqliteTable("ai_space_admin_audits", {
  id: text("id").primaryKey(),
  actorEmail: text("actor_email").notNull(),
  actorRole: text("actor_role").notNull(),
  action: text("action").notNull(),
  entityType: text("entity_type").notNull(),
  entityId: text("entity_id").notNull(),
  beforeJson: text("before_json").notNull().default("{}"),
  afterJson: text("after_json").notNull().default("{}"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("ai_space_admin_audits_entity_created_idx").on(table.entityType, table.entityId, table.createdAt, table.id)]);

/** Unique pre-dispatch receipts enforce provider-call quotas on the actual Shanghai business day. */
export const aiSpaceDispatchReceipts = sqliteTable("ai_space_dispatch_receipts", {
  id: text("id").primaryKey(),
  itemId: text("item_id").notNull().unique(),
  jobId: text("job_id").notNull(),
  ownerEmail: text("owner_email").notNull(),
  actorRole: text("actor_role").notNull(),
  modelProfileId: text("model_profile_id").notNull(),
  modelProfileVersion: integer("model_profile_version").notNull(),
  modelName: text("model_name").notNull(),
  scene: text("scene").notNull(),
  size: text("size").notNull(),
  promptDigest: text("prompt_digest").notNull(),
  dispatchedAt: text("dispatched_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("ai_space_dispatch_receipts_owner_day_idx").on(table.ownerEmail, table.dispatchedAt, table.id),
  index("ai_space_dispatch_receipts_profile_day_idx").on(table.modelProfileId, table.dispatchedAt, table.id),
]);

/** One bounded provider outcome per immutable dispatch receipt; never stores prompts or image bytes. */
export const aiSpaceDispatchResults = sqliteTable("ai_space_dispatch_results", {
  dispatchId: text("dispatch_id").primaryKey().references(() => aiSpaceDispatchReceipts.id, { onDelete: "cascade" }),
  status: text("status").notNull(),
  providerRequestId: text("provider_request_id").notNull().default(""),
  errorCode: text("error_code").notNull().default(""),
  usageJson: text("usage_json").notNull().default("{}"),
  estimatedCostCents: integer("estimated_cost_cents"),
  priceVersion: text("price_version").notNull().default(""),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

/** Administrator-managed operating thresholds used by inventory analysis. */
export const systemSettings = sqliteTable(
  "system_settings",
  {
    key: text("key").primaryKey(),
    valueJson: text("value_json").notNull(),
    updatedBy: text("updated_by").notNull().default(""),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
);

/** Durable work-plan items shown by the operations collaboration workspace. */
export const workflowTasks = sqliteTable(
  "workflow_tasks",
  {
    id: text("id").primaryKey(),
    title: text("title").notNull(),
    workContent: text("work_content").notNull().default(""),
    category: text("category").notNull().default("工作计划"),
    owner: text("owner").notNull().default(""),
    shopName: text("shop_name").notNull().default(""),
    startDate: text("start_date").notNull().default(""),
    dueDate: text("due_date").notNull().default(""),
    status: text("status").notNull(),
    priority: text("priority").notNull(),
    createdBy: text("created_by").notNull().default(""),
    updatedBy: text("updated_by").notNull().default(""),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    index("workflow_tasks_status_created_idx").on(table.status, table.createdAt),
  ],
);

/** Optimistic-concurrency and soft-delete state kept separate for legacy workflow task upgrades. */
export const workflowTaskStates = sqliteTable("workflow_task_states", {
  taskId: text("task_id").primaryKey().references(() => workflowTasks.id, { onDelete: "cascade" }),
  version: integer("version").notNull().default(1),
  mutationToken: text("mutation_token").notNull().default(""),
  deletedAt: text("deleted_at"),
  deletedBy: text("deleted_by"),
}, (table) => [index("workflow_task_states_deleted_idx").on(table.deletedAt, table.taskId)]);

/** Prevents deleted default tasks from being seeded again after a refresh. */
export const workflowTaskBootstrap = sqliteTable(
  "workflow_task_bootstrap",
  {
    key: text("key").primaryKey(),
    seededAt: text("seeded_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
);

export const workflowTaskComments = sqliteTable("workflow_task_comments", {
  id: text("id").primaryKey(),
  taskId: text("task_id").notNull().references(() => workflowTasks.id, { onDelete: "cascade" }),
  content: text("content").notNull(),
  createdBy: text("created_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("workflow_task_comments_task_created_idx").on(table.taskId, table.createdAt, table.id)]);

export const workflowTaskActivityLogs = sqliteTable("workflow_task_activity_logs", {
  id: text("id").primaryKey(),
  taskId: text("task_id").notNull().references(() => workflowTasks.id, { onDelete: "cascade" }),
  action: text("action").notNull(),
  summary: text("summary").notNull(),
  metadataJson: text("metadata_json").notNull().default("{}"),
  actorEmail: text("actor_email").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("workflow_task_activity_task_created_idx").on(table.taskId, table.createdAt, table.id)]);

export const workflowTaskReminders = sqliteTable("workflow_task_reminders", {
  id: text("id").primaryKey(),
  taskId: text("task_id").notNull().references(() => workflowTasks.id, { onDelete: "cascade" }),
  remindAt: text("remind_at").notNull(),
  note: text("note").notNull().default(""),
  status: text("status").notNull().default("pending"),
  createdBy: text("created_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("workflow_task_reminders_task_status_time_idx").on(table.taskId, table.status, table.remindAt)]);

export const workflowTaskTemplates = sqliteTable("workflow_task_templates", {
  id: text("id").primaryKey(), name: text("name").notNull(), description: text("description").notNull().default(""),
  title: text("title").notNull().default(""), workContent: text("work_content").notNull().default(""),
  category: text("category").notNull().default("工作计划"), owner: text("owner").notNull().default(""), shopName: text("shop_name").notNull().default(""),
  startOffsetDays: integer("start_offset_days").notNull().default(0), dueOffsetDays: integer("due_offset_days").notNull().default(0),
  priority: text("priority").notNull().default("normal"), active: integer("active", { mode: "boolean" }).notNull().default(true),
  createdBy: text("created_by").notNull(), updatedBy: text("updated_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`), updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("workflow_task_templates_active_updated_idx").on(table.active, table.updatedAt, table.id)]);

/** Optimistic-concurrency state for reusable task templates. */
export const workflowTaskTemplateStates = sqliteTable("workflow_task_template_states", {
  templateId: text("template_id").primaryKey().references(() => workflowTaskTemplates.id, { onDelete: "cascade" }),
  version: integer("version").notNull().default(1),
  mutationToken: text("mutation_token").notNull().default(""),
});

export const workflowTaskEntityLinks = sqliteTable("workflow_task_entity_links", {
  id: text("id").primaryKey(), taskId: text("task_id").notNull().references(() => workflowTasks.id, { onDelete: "cascade" }),
  entityType: text("entity_type").notNull(), entityId: text("entity_id").notNull(), label: text("label").notNull(), url: text("url").notNull().default(""),
  createdBy: text("created_by").notNull(), createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("workflow_task_entity_links_task_entity_uq").on(table.taskId, table.entityType, table.entityId),
  index("workflow_task_entity_links_task_created_idx").on(table.taskId, table.createdAt, table.id),
]);

export const workflowTaskAttachments = sqliteTable("workflow_task_attachments", {
  id: text("id").primaryKey(), taskId: text("task_id").notNull().references(() => workflowTasks.id, { onDelete: "cascade" }),
  fileName: text("file_name").notNull(), mimeType: text("mime_type").notNull(), sizeBytes: integer("size_bytes").notNull(), sha256: text("sha256").notNull(),
  objectKey: text("object_key").notNull().unique(), createdBy: text("created_by").notNull(), createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("workflow_task_attachments_task_created_idx").on(table.taskId, table.createdAt, table.id)]);

/** Durable outbox for best-effort deletion across D1 and private R2 storage. */
export const workflowAttachmentCleanupQueue = sqliteTable("workflow_attachment_cleanup_queue", {
  objectKey: text("object_key").primaryKey(), attempts: integer("attempts").notNull().default(0), lastError: text("last_error").notNull().default(""),
  enqueuedAt: text("enqueued_at").notNull().default(sql`CURRENT_TIMESTAMP`), updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("workflow_attachment_cleanup_queue_updated_idx").on(table.updatedAt, table.objectKey)]);

/** Durable records shared by store inspection, review care and product launch workspaces. */
export const workflowOperationRecords = sqliteTable(
  "workflow_operation_records",
  {
    id: text("id").primaryKey(),
    recordType: text("record_type").notNull(),
    title: text("title").notNull(),
    status: text("status").notNull(),
    priority: text("priority").notNull().default("normal"),
    platform: text("platform").notNull().default(""),
    channel: text("channel").notNull().default(""),
    shopName: text("shop_name").notNull(),
    owner: text("owner").notNull().default(""),
    occurredAt: text("occurred_at").notNull(),
    dueAt: text("due_at"),
    content: text("content").notNull().default(""),
    source: text("source").notNull().default("manual"),
    sourceRef: text("source_ref").notNull().default(""),
    referenceCode: text("reference_code").notNull().default(""),
    version: integer("version").notNull().default(1),
    mutationToken: text("mutation_token").notNull().default(""),
    createdBy: text("created_by").notNull(),
    updatedBy: text("updated_by").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    deletedAt: text("deleted_at"),
    deletedBy: text("deleted_by"),
  },
  (table) => [
    index("workflow_operation_records_type_status_time_idx").on(table.recordType, table.status, table.occurredAt, table.id),
    index("workflow_operation_records_shop_type_time_idx").on(table.shopName, table.recordType, table.occurredAt, table.id),
  ],
);

/** Metadata-only activity trail; free-form record content is deliberately not duplicated. */
export const workflowOperationActivities = sqliteTable(
  "workflow_operation_activities",
  {
    id: text("id").primaryKey(),
    recordId: text("record_id").notNull(),
    action: text("action").notNull(),
    actorEmail: text("actor_email").notNull(),
    actorRole: text("actor_role").notNull(),
    fromVersion: integer("from_version"),
    toVersion: integer("to_version").notNull(),
    detailJson: text("detail_json").notNull().default("{}"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    index("workflow_operation_activities_record_created_idx").on(table.recordId, table.toVersion, table.id),
  ],
);

/** Durable, metadata-only audit trail for future AI tool executions. */
export const aiToolAuditLogs = sqliteTable(
  "ai_tool_audit_logs",
  {
    id: text("id").primaryKey(),
    requestId: text("request_id").notNull(),
    actorEmail: text("actor_email").notNull(),
    actorRole: text("actor_role").notNull(),
    surface: text("surface").notNull(),
    toolName: text("tool_name").notNull(),
    argumentsJson: text("arguments_json").notNull().default("{}"),
    status: text("status").notNull(),
    rowCount: integer("row_count"),
    durationMs: integer("duration_ms"),
    responseDigest: text("response_digest"),
    errorCode: text("error_code"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    index("ai_tool_audit_logs_actor_created_idx").on(table.actorEmail, table.createdAt),
    index("ai_tool_audit_logs_tool_created_idx").on(table.toolName, table.createdAt),
  ],
);

/** Audit trail for immutable warehouse-stock snapshot imports. */
export const inventoryImportBatches = sqliteTable(
  "inventory_import_batches",
  {
    id: text("id").primaryKey(),
    source: text("source").notNull(),
    fileName: text("file_name").notNull(),
    fileSizeBytes: integer("file_size_bytes").notNull(),
    fileHash: text("file_hash").notNull(),
    sheetName: text("sheet_name").notNull(),
    snapshotDate: text("snapshot_date").notNull(),
    status: text("status").notNull(),
    rowCount: integer("row_count").notNull().default(0),
    insertedCount: integer("inserted_count").notNull().default(0),
    warningCount: integer("warning_count").notNull().default(0),
    warningsJson: text("warnings_json").notNull().default("[]"),
    totalsJson: text("totals_json").notNull().default("{}"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    completedAt: text("completed_at"),
  },
  (table) => [
    uniqueIndex("inventory_import_batches_file_hash_uq").on(table.fileHash),
    index("inventory_import_batches_completed_at_idx").on(table.completedAt),
  ],
);

/** Analysis-safe stock facts for a single imported snapshot. */
export const inventoryStockLines = sqliteTable(
  "inventory_stock_lines",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    batchId: text("batch_id").notNull(),
    rowKey: text("row_key").notNull(),
    sourceRowNumber: integer("source_row_number").notNull(),
    snapshotDate: text("snapshot_date").notNull(),
    warehouse: text("warehouse").notNull(),
    warehouseType: text("warehouse_type").notNull(),
    productCode: text("product_code").notNull(),
    productName: text("product_name").notNull(),
    brand: text("brand").notNull().default(""),
    specification: text("specification").notNull(),
    barcode: text("barcode").notNull(),
    category: text("category").notNull(),
    onHandQuantity: integer("on_hand_quantity").notNull(),
    availableQuantity: integer("available_quantity").notNull(),
    lockedQuantity: integer("locked_quantity").notNull(),
    inTransitQuantity: integer("in_transit_quantity").notNull(),
    unitCostCents: integer("unit_cost_cents").notNull(),
    inventoryAgeDays: integer("inventory_age_days"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("inventory_stock_lines_batch_row_uq").on(table.batchId, table.rowKey),
    index("inventory_stock_lines_batch_idx").on(table.batchId),
    index("inventory_stock_lines_product_idx").on(table.productCode),
    index("inventory_stock_lines_warehouse_idx").on(table.warehouse),
  ],
);

/** Per-row rolling sales counters supplied by a warehouse-age analysis export. */
export const inventoryAgeMetrics = sqliteTable(
  "inventory_age_metrics",
  {
    batchId: text("batch_id").notNull(),
    rowKey: text("row_key").notNull(),
    sales7dQuantity: integer("sales_7d_quantity").notNull().default(0),
    sales30dQuantity: integer("sales_30d_quantity").notNull().default(0),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("inventory_age_metrics_batch_row_uq").on(table.batchId, table.rowKey),
    index("inventory_age_metrics_batch_idx").on(table.batchId),
  ],
);

/** Temporary metadata for resumable inventory snapshot uploads. */
export const inventoryImportUploads = sqliteTable(
  "inventory_import_uploads",
  {
    id: text("id").primaryKey(),
    fingerprint: text("fingerprint").notNull(),
    fileName: text("file_name").notNull(),
    fileSizeBytes: integer("file_size_bytes").notNull(),
    chunkSizeBytes: integer("chunk_size_bytes").notNull(),
    chunkCount: integer("chunk_count").notNull(),
    receivedChunkCount: integer("received_chunk_count").notNull().default(0),
    receivedBytes: integer("received_bytes").notNull().default(0),
    status: text("status").notNull().default("uploading"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    expiresAt: text("expires_at").notNull(),
  },
  (table) => [
    uniqueIndex("inventory_import_uploads_fingerprint_uq").on(table.fingerprint),
    index("inventory_import_uploads_expires_at_idx").on(table.expiresAt),
  ],
);

export const inventoryImportUploadChunks = sqliteTable(
  "inventory_import_upload_chunks",
  {
    uploadId: text("upload_id").notNull(),
    chunkIndex: integer("chunk_index").notNull(),
    objectKey: text("object_key").notNull(),
    sizeBytes: integer("size_bytes").notNull(),
    sha256: text("sha256").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("inventory_import_upload_chunks_upload_chunk_uq").on(table.uploadId, table.chunkIndex),
    index("inventory_import_upload_chunks_upload_id_idx").on(table.uploadId),
  ],
);

/** Stored completion payload makes chunked upload completion safe to retry. */
export const inventoryImportUploadResults = sqliteTable(
  "inventory_import_upload_results",
  {
    uploadId: text("upload_id").primaryKey(),
    resultJson: text("result_json").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
);

/** Audit trail shared by product-master, inventory-age, and combo imports. */
export const erpReferenceImportBatches = sqliteTable(
  "erp_reference_import_batches",
  {
    id: text("id").primaryKey(),
    sourceKey: text("source_key").notNull(),
    sourceLabel: text("source_label").notNull(),
    fileName: text("file_name").notNull(),
    fileSizeBytes: integer("file_size_bytes").notNull(),
    fileHash: text("file_hash").notNull(),
    sheetName: text("sheet_name").notNull(),
    snapshotDate: text("snapshot_date"),
    status: text("status").notNull(),
    rowCount: integer("row_count").notNull().default(0),
    insertedCount: integer("inserted_count").notNull().default(0),
    updatedCount: integer("updated_count").notNull().default(0),
    excludedCount: integer("excluded_count").notNull().default(0),
    warningCount: integer("warning_count").notNull().default(0),
    warningsJson: text("warnings_json").notNull().default("[]"),
    totalsJson: text("totals_json").notNull().default("{}"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    completedAt: text("completed_at"),
  },
  (table) => [
    uniqueIndex("erp_reference_import_batches_source_hash_uq").on(table.sourceKey, table.fileHash),
    index("erp_reference_import_batches_source_created_idx").on(table.sourceKey, table.createdAt),
  ],
);

/** Current 吉客云 product master, updated idempotently by product code. */
export const erpProductMaster = sqliteTable(
  "erp_product_master",
  {
    productCode: text("product_code").primaryKey(),
    productName: text("product_name").notNull(),
    brand: text("brand").notNull().default(""),
    specification: text("specification").notNull().default(""),
    barcode: text("barcode").notNull().default(""),
    category: text("category").notNull().default(""),
    supplier: text("supplier").notNull().default(""),
    productStatus: text("product_status").notNull().default(""),
    sourceRowNumber: integer("source_row_number").notNull(),
    lastImportBatchId: text("last_import_batch_id").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    index("erp_product_master_name_idx").on(table.productName),
    index("erp_product_master_barcode_idx").on(table.barcode),
  ],
);

/** Daily inventory-age facts, replaced by snapshot date on re-import. */
export const erpInventoryAgeLines = sqliteTable(
  "erp_inventory_age_lines",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    snapshotDate: text("snapshot_date").notNull(),
    warehouse: text("warehouse").notNull(),
    warehouseType: text("warehouse_type").notNull(),
    productCode: text("product_code").notNull(),
    productName: text("product_name").notNull().default(""),
    specification: text("specification").notNull().default(""),
    category: text("category").notNull().default(""),
    availableQuantity: integer("available_quantity").notNull().default(0),
    inventoryAgeDays: integer("inventory_age_days"),
    sales7dQuantity: integer("sales_7d_quantity"),
    sales30dQuantity: integer("sales_30d_quantity"),
    unitCostCents: integer("unit_cost_cents").notNull().default(0),
    stockValueCents: integer("stock_value_cents").notNull().default(0),
    sourceRowNumber: integer("source_row_number").notNull(),
    lastImportBatchId: text("last_import_batch_id").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("erp_inventory_age_snapshot_warehouse_product_uq").on(
      table.snapshotDate,
      table.warehouse,
      table.productCode,
    ),
    index("erp_inventory_age_snapshot_idx").on(table.snapshotDate),
    index("erp_inventory_age_product_idx").on(table.productCode),
  ],
);

/** Current combo bill-of-materials exported by 吉客云. */
export const erpComboItems = sqliteTable(
  "erp_combo_items",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    parentCode: text("parent_code").notNull(),
    parentName: text("parent_name").notNull().default(""),
    childCode: text("child_code").notNull(),
    childName: text("child_name").notNull().default(""),
    childQuantityMilli: integer("child_quantity_milli").notNull(),
    sourceRowNumber: integer("source_row_number").notNull(),
    lastImportBatchId: text("last_import_batch_id").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("erp_combo_items_parent_child_uq").on(table.parentCode, table.childCode),
    index("erp_combo_items_parent_idx").on(table.parentCode),
    index("erp_combo_items_child_idx").on(table.childCode),
  ],
);

/** Durable replenishment workflow created from a specific inventory snapshot. */
export const replenishmentPlanItems = sqliteTable(
  "replenishment_plan_items",
  {
    id: text("id").primaryKey(),
    sourceBatchId: text("source_batch_id").notNull(),
    productCode: text("product_code").notNull(),
    productName: text("product_name").notNull(),
    warehouse: text("warehouse").notNull(),
    suggestedQuantity: integer("suggested_quantity").notNull(),
    plannedQuantity: integer("planned_quantity").notNull(),
    coverageDaysTenths: integer("coverage_days_tenths"),
    reason: text("reason").notNull(),
    status: text("status").notNull().default("draft"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("replenishment_plan_items_draft_key_uq")
      .on(table.sourceBatchId, table.productCode, table.warehouse)
      .where(sql`${table.status} = 'draft'`),
    index("replenishment_plan_items_status_idx").on(table.status),
    index("replenishment_plan_items_product_idx").on(table.productCode),
    index("replenishment_plan_items_source_batch_idx").on(table.sourceBatchId),
  ],
);

/** One immutable audit batch per uploaded monthly financial-report file. */
export const financeImportBatches = sqliteTable(
  "finance_import_batches",
  {
    id: text("id").primaryKey(),
    source: text("source").notNull(),
    fileName: text("file_name").notNull(),
    fileSizeBytes: integer("file_size_bytes").notNull(),
    fileHash: text("file_hash").notNull(),
    status: text("status").notNull(),
    rowCount: integer("row_count").notNull().default(0),
    insertedCount: integer("inserted_count").notNull().default(0),
    duplicateCount: integer("duplicate_count").notNull().default(0),
    warningCount: integer("warning_count").notNull().default(0),
    parsedMonthCount: integer("parsed_month_count").notNull().default(0),
    importedMonthCount: integer("imported_month_count").notNull().default(0),
    skippedMonthCount: integer("skipped_month_count").notNull().default(0),
    subjectCount: integer("subject_count").notNull().default(0),
    monthsJson: text("months_json").notNull().default("[]"),
    warningsJson: text("warnings_json").notNull().default("[]"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    completedAt: text("completed_at"),
  },
  (table) => [
    uniqueIndex("finance_import_batches_file_hash_uq").on(table.fileHash),
    index("finance_import_batches_created_idx").on(table.createdAt),
  ],
);

/** D1-side single-write fence used only during the controlled finance cutover. */
export const financeWriteAuthority = sqliteTable("finance_write_authority", {
  id: integer("id").primaryKey(),
  owner: text("owner").notNull().default("d1"),
  epoch: integer("epoch").notNull().default(1),
  cutoverId: text("cutover_id").notNull().default(""),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

/** Month is the idempotency anchor: a later file cannot duplicate a closed month. */
export const financeMonths = sqliteTable(
  "finance_months",
  {
    month: text("month").primaryKey(),
    batchId: text("batch_id").notNull(),
    sheetName: text("sheet_name").notNull(),
    businessName: text("business_name").notNull(),
    sourceFileName: text("source_file_name").notNull(),
    status: text("status").notNull().default("processing"),
    shopCount: integer("shop_count").notNull().default(0),
    subjectCount: integer("subject_count").notNull().default(0),
    importedAt: text("imported_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [index("finance_months_status_month_idx").on(table.status, table.month)],
);

/** Dynamic subject-name facts avoid a schema migration when Kingdee fields change. */
export const financeLines = sqliteTable(
  "finance_lines",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    month: text("month").notNull(),
    section: text("section").notNull(),
    metricKey: text("metric_key").notNull(),
    subjectName: text("subject_name").notNull(),
    scopeKey: text("scope_key").notNull(),
    scopeType: text("scope_type").notNull(),
    scopeName: text("scope_name").notNull(),
    groupName: text("group_name").notNull().default(""),
    valueType: text("value_type").notNull(),
    amountCents: integer("amount_cents"),
    rateBps: integer("rate_bps"),
    rawValue: text("raw_value").notNull().default(""),
    sourceRowCount: integer("source_row_count").notNull().default(1),
    sortOrder: integer("sort_order").notNull().default(0),
    isTotal: integer("is_total", { mode: "boolean" }).notNull().default(false),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("finance_lines_month_section_scope_subject_uq").on(
      table.month,
      table.section,
      table.scopeKey,
      table.subjectName,
    ),
    index("finance_lines_month_section_scope_idx").on(table.month, table.section, table.scopeType, table.scopeName),
    index("finance_lines_metric_month_idx").on(table.metricKey, table.month),
    index("finance_lines_subject_month_idx").on(table.subjectName, table.month),
  ],
);

/** Legacy pre-platform targets retained only as an additive migration source. */
export const financeTargets = sqliteTable(
  "finance_targets",
  {
    id: text("id").primaryKey(),
    periodType: text("period_type").notNull(),
    periodKey: text("period_key").notNull(),
    shopName: text("shop_name").notNull().default(""),
    category: text("category").notNull().default(""),
    manager: text("manager").notNull().default(""),
    salesTargetCents: integer("sales_target_cents").notNull().default(0),
    profitTargetCents: integer("profit_target_cents").notNull().default(0),
    smallMarginBps: integer("small_margin_bps").notNull().default(0),
    inventoryCleanupTargetCents: integer("inventory_cleanup_target_cents").notNull().default(0),
    promotionFeeRatioBps: integer("promotion_fee_ratio_bps").notNull().default(0),
    stagnantInventoryTargetCents: integer("stagnant_inventory_target_cents").notNull().default(0),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("finance_targets_period_scope_uq").on(table.periodType, table.periodKey, table.shopName, table.category),
    index("finance_targets_period_idx").on(table.periodType, table.periodKey),
  ],
);

export const financeTargetVersions = sqliteTable("finance_target_versions", {
  targetId: text("target_id").primaryKey().references(() => financeTargets.id, { onDelete: "cascade" }),
  version: integer("version").notNull().default(1),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const financeTargetDeletionAudits = sqliteTable("finance_target_deletion_audits", {
  auditId: text("audit_id").primaryKey(), targetId: text("target_id").notNull(),
  periodType: text("period_type").notNull(), periodKey: text("period_key").notNull(),
  shopName: text("shop_name").notNull(), category: text("category").notNull(), actor: text("actor").notNull(),
  oldVersion: integer("old_version").notNull(), expectedVersion: integer("expected_version").notNull(),
  reason: text("reason").notNull(), deletedAt: text("deleted_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

/** Canonical platform-aware targets used by financial progress analysis. */
export const financeTargetsScoped = sqliteTable(
  "finance_targets_scoped",
  {
    id: text("id").primaryKey(),
    periodType: text("period_type").notNull(),
    periodKey: text("period_key").notNull(),
    platform: text("platform").notNull().default(""),
    shopName: text("shop_name").notNull().default(""),
    category: text("category").notNull().default(""),
    manager: text("manager").notNull().default(""),
    salesTargetCents: integer("sales_target_cents").notNull().default(0),
    profitTargetCents: integer("profit_target_cents").notNull().default(0),
    smallMarginBps: integer("small_margin_bps").notNull().default(0),
    inventoryCleanupTargetCents: integer("inventory_cleanup_target_cents").notNull().default(0),
    promotionFeeRatioBps: integer("promotion_fee_ratio_bps").notNull().default(0),
    stagnantInventoryTargetCents: integer("stagnant_inventory_target_cents").notNull().default(0),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("finance_targets_scoped_period_scope_uq").on(
      table.periodType,
      table.periodKey,
      table.platform,
      table.shopName,
      table.category,
    ),
    index("finance_targets_scoped_period_idx").on(table.periodType, table.periodKey),
    index("finance_targets_scoped_shop_idx").on(table.platform, table.shopName, table.periodType, table.periodKey),
  ],
);

export const financeTargetScopedVersions = sqliteTable("finance_target_scoped_versions", {
  targetId: text("target_id").primaryKey().references(() => financeTargetsScoped.id, { onDelete: "cascade" }),
  version: integer("version").notNull().default(1),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const financeTargetScopedDeletionAudits = sqliteTable("finance_target_scoped_deletion_audits", {
  auditId: text("audit_id").primaryKey(), targetId: text("target_id").notNull(),
  periodType: text("period_type").notNull(), periodKey: text("period_key").notNull(), platform: text("platform").notNull(),
  shopName: text("shop_name").notNull(), category: text("category").notNull(), actor: text("actor").notNull(),
  oldVersion: integer("old_version").notNull(), expectedVersion: integer("expected_version").notNull(),
  reason: text("reason").notNull(), deletedAt: text("deleted_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const financeTargetLegacyMigrations = sqliteTable("finance_target_legacy_migrations", {
  targetId: text("target_id").primaryKey(),
  migratedAt: text("migrated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

/** Auditable uploads for competitor rankings and market SKU catalog files. */
export const marketImportBatches = sqliteTable(
  "market_import_batches",
  {
    id: text("id").primaryKey(),
    sourceType: text("source_type").notNull(),
    fileName: text("file_name").notNull(),
    fileSizeBytes: integer("file_size_bytes").notNull(),
    fileHash: text("file_hash").notNull(),
    sheetName: text("sheet_name").notNull().default(""),
    status: text("status").notNull(),
    rowCount: integer("row_count").notNull().default(0),
    insertedCount: integer("inserted_count").notNull().default(0),
    updatedCount: integer("updated_count").notNull().default(0),
    warningCount: integer("warning_count").notNull().default(0),
    periodStart: text("period_start"),
    periodEnd: text("period_end"),
    warningsJson: text("warnings_json").notNull().default("[]"),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    completedAt: text("completed_at"),
  },
  (table) => [
    uniqueIndex("market_import_batches_file_hash_uq").on(table.fileHash),
    index("market_import_batches_created_idx").on(table.createdAt),
  ],
);

/** Market ranking facts; own-product flags are resolved live against SKU/SPU and sales data. */
export const marketRankingEntries = sqliteTable(
  "market_ranking_entries",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    naturalKey: text("natural_key").notNull(),
    sourceRowNumber: integer("source_row_number").notNull(),
    periodStart: text("period_start").notNull(),
    periodEnd: text("period_end").notNull(),
    category: text("category").notNull().default(""),
    scope: text("scope").notNull().default("全部"),
    priceBandFilter: text("price_band_filter").notNull().default("全部"),
    rankingDimension: text("ranking_dimension").notNull().default("SKU"),
    operationMode: text("operation_mode").notNull().default("未知"),
    subcategory: text("subcategory").notNull().default(""),
    rank: integer("rank"),
    skuCode: text("sku_code").notNull(),
    productName: text("product_name").notNull().default(""),
    brand: text("brand").notNull().default(""),
    priceCents: integer("price_cents"),
    priceLowCents: integer("price_low_cents"),
    priceHighCents: integer("price_high_cents"),
    priceEstimated: integer("price_estimated", { mode: "boolean" }).notNull().default(false),
    priceRaw: text("price_raw").notNull().default(""),
    gmvCents: integer("gmv_cents").notNull().default(0),
    gmvLowCents: integer("gmv_low_cents"),
    gmvHighCents: integer("gmv_high_cents"),
    gmvRaw: text("gmv_raw").notNull().default(""),
    quantity: integer("quantity").notNull().default(0),
    quantityLow: integer("quantity_low"),
    quantityHigh: integer("quantity_high"),
    quantityRaw: text("quantity_raw").notNull().default(""),
    pageViews: integer("page_views").notNull().default(0),
    pageViewsRaw: text("page_views_raw").notNull().default(""),
    visitors: integer("visitors").notNull().default(0),
    visitorsLow: integer("visitors_low"),
    visitorsHigh: integer("visitors_high"),
    visitorsRaw: text("visitors_raw").notNull().default(""),
    conversionBps: integer("conversion_bps"),
    conversionLowBps: integer("conversion_low_bps"),
    conversionHighBps: integer("conversion_high_bps"),
    conversionRaw: text("conversion_raw").notNull().default(""),
    cartCustomers: integer("cart_customers").notNull().default(0),
    cartCustomersRaw: text("cart_customers_raw").notNull().default(""),
    searchClicks: integer("search_clicks").notNull().default(0),
    searchClicksRaw: text("search_clicks_raw").notNull().default(""),
    imageUrl: text("image_url").notNull().default(""),
    productUrl: text("product_url").notNull().default(""),
    rawJson: text("raw_json").notNull().default("{}"),
    lastImportBatchId: text("last_import_batch_id").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("market_ranking_entries_natural_key_uq").on(table.naturalKey),
    uniqueIndex("market_entries_canonical_price_band_uq").on(table.periodStart, table.periodEnd, table.category, table.scope, table.priceBandFilter, table.rankingDimension, table.skuCode),
    index("market_entries_period_idx").on(table.periodEnd, table.periodStart),
    index("market_entries_category_idx").on(table.category, table.periodEnd),
    index("market_entries_sku_idx").on(table.skuCode, table.periodEnd),
    index("market_entries_brand_idx").on(table.brand, table.periodEnd),
    index("market_entries_dimension_idx").on(table.rankingDimension, table.operationMode, table.periodEnd),
    index("market_entries_subcategory_idx").on(table.subcategory, table.periodEnd),
    index("market_entries_representative_idx").on(table.category, table.scope, table.rankingDimension, table.skuCode, table.periodEnd, table.periodStart, table.id),
    index("market_entries_annotation_catalog_idx").on(table.category, table.skuCode, table.periodEnd, table.updatedAt, table.id),
  ],
);

export const marketSkuGmvTotals = sqliteTable("market_sku_gmv_totals", {
  skuCode: text("sku_code").primaryKey(),
  gmvTotalCents: integer("gmv_total_cents").notNull().default(0),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

/** Monthly market-positioning price snapshots; manual confirmations survive re-imports. */
export const marketPriceSnapshots = sqliteTable(
  "market_price_snapshots",
  {
    id: text("id").primaryKey(),
    category: text("category").notNull(),
    skuCode: text("sku_code").notNull(),
    rankingDimension: text("ranking_dimension").notNull().default("SKU"),
    month: text("month").notNull(),
    sourcePriceCents: integer("source_price_cents"),
    aiImagePriceCents: integer("ai_image_price_cents"),
    aiPriceType: text("ai_price_type").notNull().default(""),
    aiConfidenceBps: integer("ai_confidence_bps"),
    aiReason: text("ai_reason").notNull().default(""),
    confirmedMarketPriceCents: integer("confirmed_market_price_cents"),
    averageTransactionPriceCents: integer("average_transaction_price_cents"),
    priceLowCents: integer("price_low_cents"),
    priceHighCents: integer("price_high_cents"),
    imageContentSha256: text("image_content_sha256").notNull().default(""),
    imageUrl: text("image_url").notNull().default(""),
    confirmationStatus: text("confirmation_status").notNull().default("source_table"),
    confirmedBy: text("confirmed_by").notNull().default(""),
    confirmedAt: text("confirmed_at"),
    sourceJobItemId: text("source_job_item_id").notNull().default(""),
    promptVersionId: text("prompt_version_id").notNull().default(""),
    sourceImportBatchId: text("source_import_batch_id").notNull().default(""),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("market_price_snapshots_sku_month_uq").on(table.category, table.skuCode, table.rankingDimension, table.month),
    index("market_price_snapshots_status_idx").on(table.confirmationStatus, table.updatedAt),
    index("market_price_snapshots_hash_idx").on(table.skuCode, table.imageContentSha256, table.confirmedAt),
  ],
);

export const marketPriceBandVersions = sqliteTable("market_price_band_versions", {
  id: text("id").primaryKey(),
  category: text("category").notNull().default("*"),
  version: integer("version").notNull(),
  status: text("status").notNull().default("draft"),
  effectiveFrom: text("effective_from").notNull().default("1970-01-01"),
  createdBy: text("created_by").notNull().default(""),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  publishedBy: text("published_by").notNull().default(""),
  publishedAt: text("published_at"),
  rolledBackFromId: text("rolled_back_from_id").notNull().default(""),
  note: text("note").notNull().default(""),
}, (table) => [
  uniqueIndex("market_price_band_versions_category_version_uq").on(table.category, table.version),
  index("market_price_band_versions_lookup_idx").on(table.category, table.status, table.effectiveFrom, table.version),
]);

export const marketPriceBandItems = sqliteTable("market_price_band_items", {
  id: text("id").primaryKey(),
  versionId: text("version_id").notNull(),
  label: text("label").notNull(),
  minCents: integer("min_cents"),
  maxCents: integer("max_cents"),
  sortOrder: integer("sort_order").notNull().default(0),
}, (table) => [
  index("market_price_band_items_version_idx").on(table.versionId, table.sortOrder),
]);

export const marketMasterMappingRules = sqliteTable("market_master_mapping_rules", {
  id: text("id").primaryKey(),
  kind: text("kind").notNull(),
  category: text("category").notNull().default(""),
  sourceValue: text("source_value").notNull(),
  targetValue: text("target_value").notNull(),
  status: text("status").notNull().default("draft"),
  version: integer("version").notNull().default(1),
  effectiveFrom: text("effective_from").notNull().default("1970-01-01"),
  createdBy: text("created_by").notNull().default(""),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("market_master_mapping_rules_kind_idx").on(table.kind, table.category, table.status, table.effectiveFrom),
]);

export const marketSubcategoryTaxonomy = sqliteTable("market_subcategory_taxonomy", {
  id: text("id").primaryKey(),
  category: text("category").notNull(),
  subcategory: text("subcategory").notNull(),
  status: text("status").notNull().default("active"),
  sortOrder: integer("sort_order").notNull().default(0),
  createdBy: text("created_by").notNull().default(""),
  updatedBy: text("updated_by").notNull().default(""),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("market_subcategory_taxonomy_category_name_uq").on(table.category, table.subcategory),
  index("market_subcategory_taxonomy_lookup_idx").on(table.category, table.status, table.sortOrder),
]);

export const marketDownloadTasks = sqliteTable("market_download_tasks", {
  id: text("id").primaryKey(),
  category: text("category").notNull(),
  month: text("month").notNull(),
  rankingDimension: text("ranking_dimension").notNull(),
  status: text("status").notNull().default("planned"),
  attemptCount: integer("attempt_count").notNull().default(0),
  jdTaskId: text("jd_task_id").notNull().default(""),
  sourceFileName: text("source_file_name").notNull().default(""),
  fileHash: text("file_hash").notNull().default(""),
  rowCount: integer("row_count").notNull().default(0),
  headerValid: integer("header_valid").notNull().default(0),
  periodValid: integer("period_valid").notNull().default(0),
  categoryValid: integer("category_valid").notNull().default(0),
  dimensionValid: integer("dimension_valid").notNull().default(0),
  stagingBatchId: text("staging_batch_id").notNull().default(""),
  importBatchId: text("import_batch_id").notNull().default(""),
  validationJson: text("validation_json").notNull().default("{}"),
  errorCode: text("error_code").notNull().default(""),
  errorMessage: text("error_message").notNull().default(""),
  nextRetryAt: text("next_retry_at"),
  lastAttemptAt: text("last_attempt_at"),
  completedAt: text("completed_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("market_download_tasks_unique_uq").on(table.category, table.month, table.rankingDimension),
  index("market_download_tasks_status_idx").on(table.status, table.nextRetryAt, table.updatedAt),
]);

export const marketDownloadConfigs = sqliteTable("market_download_configs", {
  id: text("id").primaryKey(),
  category: text("category").notNull(),
  rankingDimension: text("ranking_dimension").notNull(),
  monthStart: text("month_start").notNull(),
  monthEnd: text("month_end").notNull(),
  status: text("status").notNull().default("enabled"),
  createdBy: text("created_by").notNull().default(""),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("market_download_configs_unique_uq").on(table.category, table.rankingDimension, table.monthStart, table.monthEnd),
  index("market_download_configs_status_idx").on(table.status, table.category, table.rankingDimension),
]);

export const marketMasterAuditLogs = sqliteTable("market_master_audit_logs", {
  id: text("id").primaryKey(),
  actorEmail: text("actor_email").notNull(),
  actorRole: text("actor_role").notNull(),
  action: text("action").notNull(),
  entityType: text("entity_type").notNull(),
  entityId: text("entity_id").notNull(),
  beforeJson: text("before_json").notNull().default("{}"),
  afterJson: text("after_json").notNull().default("{}"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("market_master_audit_logs_entity_idx").on(table.entityType, table.entityId, table.createdAt),
]);

/** Validated JD product image cache metadata; image bytes live in R2. */
export const marketImageCache = sqliteTable(
  "market_image_cache",
  {
    sourceUrl: text("source_url").primaryKey(),
    status: text("status").notNull().default("pending"),
    objectKey: text("object_key").notNull().default(""),
    contentSha256: text("content_sha256").notNull().default(""),
    mimeType: text("mime_type").notNull().default(""),
    sizeBytes: integer("size_bytes").notNull().default(0),
    imageSource: text("image_source").notNull().default(""),
    attemptCount: integer("attempt_count").notNull().default(0),
    errorCode: text("error_code").notNull().default(""),
    errorMessage: text("error_message").notNull().default(""),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    index("market_image_cache_object_key_idx").on(table.objectKey),
    index("market_image_cache_status_idx").on(table.status, table.updatedAt),
  ],
);

/** Immutable prompts and durable review jobs for market-SKU AI annotation. */
export const marketAnnotationPromptVersions = sqliteTable("market_annotation_prompt_versions", {
  id: text("id").primaryKey(), category: text("category").notNull(), version: integer("version").notNull(),
  parentId: text("parent_id"), source: text("source").notNull(), status: text("status").notNull().default("draft"),
  segmentsJson: text("segments_json").notNull(), promptBody: text("prompt_body").notNull(), changeNote: text("change_note").notNull().default(""),
  metricsJson: text("metrics_json").notNull().default("{}"), createdBy: text("created_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`), activatedBy: text("activated_by"), activatedAt: text("activated_at"),
}, (table) => [
  uniqueIndex("market_annotation_prompts_category_version_uq").on(table.category, table.version),
  uniqueIndex("market_annotation_prompts_active_uq").on(table.category).where(sql`${table.status} = 'active'`),
]);

export const marketAnnotationJobs = sqliteTable("market_annotation_jobs", {
  id: text("id").primaryKey(), category: text("category").notNull(), promptVersionId: text("prompt_version_id").notNull(),
  executor: text("executor").notNull(), modelId: text("model_id"), localModelName: text("local_model_name").notNull().default(""),
  workKey: text("work_key").notNull().default(""), reuseStatus: text("reuse_status").notNull().default("pending"), reuseStartedAt: text("reuse_started_at"),
  status: text("status").notNull().default("queued"), totalCount: integer("total_count").notNull().default(0),
  completedCount: integer("completed_count").notNull().default(0), failedCount: integer("failed_count").notNull().default(0),
  reviewedCount: integer("reviewed_count").notNull().default(0), committedCount: integer("committed_count").notNull().default(0),
  createdBy: text("created_by").notNull(), createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  startedAt: text("started_at"), completedAt: text("completed_at"), updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  commitTokenHash: text("commit_token_hash").notNull().default(""), commitStartedAt: text("commit_started_at"),
}, (table) => [
  index("market_annotation_jobs_category_created_idx").on(table.category, table.createdAt),
  uniqueIndex("market_annotation_jobs_active_work_uq").on(table.workKey).where(sql`${table.workKey} <> '' AND ${table.status} IN ('queued','running','failed')`),
]);

export const marketAnnotationItems = sqliteTable("market_annotation_items", {
  id: text("id").primaryKey(), jobId: text("job_id").notNull(), skuCode: text("sku_code").notNull(),
  category: text("category").notNull().default(""), scope: text("scope").notNull().default(""), rankingDimension: text("ranking_dimension").notNull().default("SKU"),
  month: text("month").notNull().default(""), imageContentSha256: text("image_content_sha256").notNull().default(""),
  productName: text("product_name").notNull().default(""), brand: text("brand").notNull().default(""),
  sourceImageUrl: text("source_image_url").notNull().default(""), resolvedImageUrl: text("resolved_image_url").notNull().default(""),
  imageSource: text("image_source").notNull().default("none"), status: text("status").notNull().default("queued"),
  aiSegment: text("ai_segment").notNull().default(""), aiImagePriceCents: integer("ai_image_price_cents"),
  aiPriceType: text("ai_price_type").notNull().default(""), aiPriceLowCents: integer("ai_price_low_cents"), aiPriceHighCents: integer("ai_price_high_cents"),
  aiConfidenceBps: integer("ai_confidence_bps"), aiReason: text("ai_reason").notNull().default(""), aiRawDigest: text("ai_raw_digest").notNull().default(""),
  reviewedSegment: text("reviewed_segment").notNull().default(""), reviewedImagePriceCents: integer("reviewed_image_price_cents"),
  reviewedPriceType: text("reviewed_price_type").notNull().default(""), reviewedPriceLowCents: integer("reviewed_price_low_cents"), reviewedPriceHighCents: integer("reviewed_price_high_cents"),
  selected: integer("selected", { mode: "boolean" }).notNull().default(false), reviewedBy: text("reviewed_by").notNull().default(""), reviewedAt: text("reviewed_at"),
  leaseTokenHash: text("lease_token_hash").notNull().default(""), leaseAgentId: text("lease_agent_id").notNull().default(""), leaseExpiresAt: text("lease_expires_at"),
  attemptCount: integer("attempt_count").notNull().default(0), errorMessage: text("error_message").notNull().default(""),
  version: integer("version").notNull().default(0), createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`), updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("market_annotation_items_job_snapshot_uq").on(table.jobId, table.category, table.scope, table.skuCode, table.rankingDimension, table.month, table.imageContentSha256),
  index("market_annotation_items_job_status_idx").on(table.jobId, table.status, table.updatedAt),
  index("market_annotation_items_job_inference_unit_idx").on(table.jobId, table.category, table.scope, table.skuCode, table.rankingDimension, table.imageContentSha256, table.status, table.id),
  index("market_annotation_items_lease_idx").on(table.leaseExpiresAt, table.status),
]);

export const marketSkuAnnotations = sqliteTable("market_sku_annotations", {
  id: text("id").primaryKey(), category: text("category").notNull(), skuCode: text("sku_code").notNull(), segment: text("segment").notNull(),
  imagePriceCents: integer("image_price_cents"), imageUrl: text("image_url").notNull().default(""), imageSource: text("image_source").notNull().default("none"),
  confidenceBps: integer("confidence_bps"), sourceJobItemId: text("source_job_item_id").notNull(), promptVersionId: text("prompt_version_id").notNull(),
  reviewedBy: text("reviewed_by").notNull(), reviewedAt: text("reviewed_at").notNull(), version: integer("version").notNull().default(1),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`), updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("market_sku_annotations_category_sku_uq").on(table.category, table.skuCode),
  index("market_sku_annotations_segment_idx").on(table.category, table.segment, table.updatedAt),
]);

export const marketAnnotationCommitReceipts = sqliteTable("market_annotation_commit_receipts", {
  id: text("id").primaryKey(), jobItemId: text("job_item_id").notNull(), annotationId: text("annotation_id").notNull(),
  idempotencyKey: text("idempotency_key").notNull(), beforeJson: text("before_json").notNull().default("{}"), afterJson: text("after_json").notNull(),
  committedBy: text("committed_by").notNull(), committedAt: text("committed_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  batchId: text("batch_id").notNull().default(""), requestDigest: text("request_digest").notNull().default(""),
}, (table) => [
  uniqueIndex("market_annotation_commits_item_uq").on(table.jobItemId), uniqueIndex("market_annotation_commits_idempotency_uq").on(table.idempotencyKey),
  index("market_annotation_commits_batch_idx").on(table.batchId),
]);

export const marketAnnotationValidationSamples = sqliteTable("market_annotation_validation_samples", {
  id: text("id").primaryKey(), category: text("category").notNull(), skuCode: text("sku_code").notNull(), productName: text("product_name").notNull().default(""),
  brand: text("brand").notNull().default(""), imageUrl: text("image_url").notNull().default(""), goldSegment: text("gold_segment").notNull(),
  goldImagePriceCents: integer("gold_image_price_cents"), sourceAnnotationId: text("source_annotation_id").notNull().default(""),
  createdBy: text("created_by").notNull(), createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [uniqueIndex("market_annotation_samples_category_sku_uq").on(table.category, table.skuCode)]);

export const marketAnnotationValidationRuns = sqliteTable("market_annotation_validation_runs", {
  id: text("id").primaryKey(), category: text("category").notNull(), baselinePromptId: text("baseline_prompt_id"), candidatePromptId: text("candidate_prompt_id").notNull(),
  modelId: text("model_id").notNull(), status: text("status").notNull().default("queued"), seed: text("seed").notNull(),
  requestedSampleCount: integer("requested_sample_count").notNull().default(50), sampleCount: integer("sample_count").notNull().default(0),
  sampleHash: text("sample_hash").notNull().default(""), metricsJson: text("metrics_json").notNull().default("{}"), gateJson: text("gate_json").notNull().default("{}"),
  createdBy: text("created_by").notNull(), createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`), completedAt: text("completed_at"),
}, (table) => [index("market_annotation_validation_runs_prompt_idx").on(table.candidatePromptId, table.createdAt)]);

export const marketAnnotationValidationResults = sqliteTable("market_annotation_validation_results", {
  id: text("id").primaryKey(), runId: text("run_id").notNull(), sampleId: text("sample_id").notNull(), promptVersionId: text("prompt_version_id").notNull(),
  status: text("status").notNull().default("queued"),
  predictedSegment: text("predicted_segment").notNull().default(""), predictedImagePriceCents: integer("predicted_image_price_cents"), confidenceBps: integer("confidence_bps"),
  isCorrect: integer("is_correct", { mode: "boolean" }).notNull().default(false), errorMessage: text("error_message").notNull().default(""),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  sampleSnapshotJson: text("sample_snapshot_json").notNull().default("{}"), claimTokenHash: text("claim_token_hash").notNull().default(""),
  leaseExpiresAt: text("lease_expires_at"), attemptCount: integer("attempt_count").notNull().default(0), updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("market_annotation_validation_result_uq").on(table.runId, table.sampleId, table.promptVersionId),
  index("market_annotation_validation_result_lease_idx").on(table.runId, table.status, table.leaseExpiresAt),
]);

export const marketAnnotationPromptAudits = sqliteTable("market_annotation_prompt_audits", {
  id: text("id").primaryKey(), promptId: text("prompt_id").notNull(), category: text("category").notNull(), action: text("action").notNull(),
  reason: text("reason").notNull(), actor: text("actor").notNull(), createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("market_annotation_prompt_audits_prompt_idx").on(table.promptId, table.createdAt)]);

export const marketAnnotationLocalAgents = sqliteTable("market_annotation_local_agents", {
  id: text("id").primaryKey(), name: text("name").notNull(), tokenHash: text("token_hash").notNull(), status: text("status").notNull().default("enabled"),
  capabilitiesJson: text("capabilities_json").notNull().default("{}"), createdBy: text("created_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`), lastSeenAt: text("last_seen_at"), revokedAt: text("revoked_at"),
}, (table) => [uniqueIndex("market_annotation_agents_token_uq").on(table.tokenHash)]);

/** Paired customer-service session/chat imports and durable AI/manual review fields. */
export const customerServiceImportBatches = sqliteTable("customer_service_import_batches", {
  id: text("id").primaryKey(), shopName: text("shop_name").notNull().default("志高商用设备"),
  sessionFileName: text("session_file_name").notNull(), chatFileName: text("chat_file_name").notNull(), fileHash: text("file_hash").notNull(), status: text("status").notNull(),
  conversationCount: integer("conversation_count").notNull().default(0), matchedCount: integer("matched_count").notNull().default(0), sessionOnlyCount: integer("session_only_count").notNull().default(0), chatOnlyCount: integer("chat_only_count").notNull().default(0), ambiguousCount: integer("ambiguous_count").notNull().default(0),
  warningsJson: text("warnings_json").notNull().default("[]"), createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`), completedAt: text("completed_at"),
}, (table) => [uniqueIndex("customer_service_import_batches_file_hash_uq").on(table.fileHash)]);

export const customerServiceConversations = sqliteTable("customer_service_conversations", {
  id: integer("id").primaryKey({ autoIncrement: true }), conversationKey: text("conversation_key").notNull(), firstImportBatchId: text("first_import_batch_id").notNull(), lastImportBatchId: text("last_import_batch_id").notNull(),
  shopName: text("shop_name").notNull().default("志高商用设备"), consultedAt: text("consulted_at").notNull(), customerId: text("customer_id").notNull().default(""), customerAlias: text("customer_alias").notNull().default(""), consultationType: text("consultation_type").notNull().default(""), agent: text("agent").notNull().default(""), transferredAgent: text("transferred_agent").notNull().default(""), skillGroup: text("skill_group").notNull().default(""),
  productSku: text("product_sku").notNull().default(""), productName: text("product_name").notNull().default(""), firstResponseAt: text("first_response_at").notNull().default(""), responseSeconds: integer("response_seconds"), durationMinutes: integer("duration_minutes"), customerMessageCount: integer("customer_message_count"), agentMessageCount: integer("agent_message_count"), satisfaction: text("satisfaction").notNull().default(""), resolved: text("resolved").notNull().default(""), conversationId: text("conversation_id").notNull().default(""),
  matchStatus: text("match_status").notNull(), matchConfidence: text("match_confidence").notNull(), chatStartedAt: text("chat_started_at").notNull().default(""), chatEndedAt: text("chat_ended_at").notNull().default(""), chatCustomerAlias: text("chat_customer_alias").notNull().default(""), messagesJson: text("messages_json").notNull().default("[]"),
  robotScope: text("robot_scope").notNull().default(""), problemType: text("problem_type").notNull().default(""), conversionStatus: text("conversion_status").notNull().default(""), serviceIssues: text("service_issues").notNull().default(""), summaryText: text("summary_text").notNull().default(""), analysisSource: text("analysis_source").notNull().default(""), analyzedAt: text("analyzed_at"), annotatedAt: text("annotated_at"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`), updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("customer_service_conversations_key_uq").on(table.conversationKey),
  index("customer_service_conversations_consulted_idx").on(table.consultedAt),
  index("customer_service_conversations_filter_idx").on(table.agent, table.matchStatus, table.consultedAt),
]);

export const customerServiceConversationVersions = sqliteTable("customer_service_conversation_versions", {
  conversationId: integer("conversation_id").primaryKey().references(() => customerServiceConversations.id, { onDelete: "cascade" }),
  version: integer("version").notNull().default(1),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const customerServiceDeletionAudits = sqliteTable("customer_service_deletion_audits", {
  auditId: text("audit_id").primaryKey(),
  conversationId: integer("conversation_id").notNull(),
  conversationKey: text("conversation_key").notNull(),
  actor: text("actor").notNull(),
  oldVersion: integer("old_version").notNull(),
  expectedVersion: integer("expected_version").notNull(),
  reason: text("reason").notNull(),
  deletedAt: text("deleted_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const productShippingRateImportBatches = sqliteTable("product_shipping_rate_import_batches", {
  id: text("id").primaryKey(),
  source: text("source").notNull().default("sku_cumulative"),
  fileName: text("file_name").notNull(),
  fileSizeBytes: integer("file_size_bytes").notNull(),
  fileHash: text("file_hash").notNull(),
  rawFileHash: text("raw_file_hash").notNull(),
  contentHash: text("content_hash").notNull(),
  sheetName: text("sheet_name").notNull(),
  actor: text("actor").notNull().default(""),
  status: text("status").notNull().default("processing"),
  sourceRowCount: integer("source_row_count").notNull().default(0),
  rowCount: integer("row_count").notNull().default(0),
  insertedCount: integer("inserted_count").notNull().default(0),
  updatedCount: integer("updated_count").notNull().default(0),
  duplicateCount: integer("duplicate_count").notNull().default(0),
  warningCount: integer("warning_count").notNull().default(0),
  warningsJson: text("warnings_json").notNull().default("[]"),
  totalsJson: text("totals_json").notNull().default("{}"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  completedAt: text("completed_at"),
}, (table) => [
  uniqueIndex("product_shipping_rate_batches_file_hash_uq").on(table.fileHash),
  index("product_shipping_rate_batches_completed_idx").on(table.completedAt, table.createdAt),
]);

export const productShippingRates = sqliteTable("product_shipping_rates", {
  productCode: text("product_code").primaryKey(),
  shippingRate: real("shipping_rate").notNull(),
  sourceRowNumber: integer("source_row_number").notNull(),
  lastImportBatchId: text("last_import_batch_id").notNull()
    .references(() => productShippingRateImportBatches.id, { onDelete: "restrict" }),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("product_shipping_rates_batch_idx").on(table.lastImportBatchId, table.productCode),
]);
