-- Historical AI schema only; no production rows or secrets.

CREATE TABLE ai_agent_checkpoints (
    id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES ai_agent_jobs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 64),
    kind TEXT NOT NULL CHECK (kind IN ('checkpoint','completed','paused','failed')),
    state_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(state_json)),
    output_digest TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(job_id, ordinal)
  );

CREATE TABLE ai_agent_events (
    id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES ai_agent_jobs(id) ON DELETE CASCADE,
    owner_email TEXT NOT NULL COLLATE NOCASE, actor_email TEXT NOT NULL COLLATE NOCASE,
    event_type TEXT NOT NULL, from_status TEXT NOT NULL DEFAULT '', to_status TEXT NOT NULL DEFAULT '',
    job_version INTEGER NOT NULL CHECK (job_version >= 1),
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_agent_jobs (
    id TEXT PRIMARY KEY NOT NULL, owner_email TEXT NOT NULL COLLATE NOCASE,
    client_request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json)), task TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_json)),
    state_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(state_json)),
    output_json TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
    model_id TEXT NOT NULL DEFAULT '',
    model_version INTEGER NOT NULL DEFAULT 0 CHECK (model_version >= 0),
    allowed_tools_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(allowed_tools_json)),
    tool_policy_digest TEXT NOT NULL DEFAULT '',
    provider_round_count INTEGER NOT NULL DEFAULT 0 CHECK (provider_round_count >= 0),
    tool_call_count INTEGER NOT NULL DEFAULT 0 CHECK (tool_call_count >= 0),
    provider_dispatch_started_at TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','paused','completed','failed','cancelled')),
    phase TEXT NOT NULL DEFAULT 'queued' CHECK (phase IN ('queued','executing','paused','completed','failed','cancelled')),
    step_index INTEGER NOT NULL DEFAULT 0 CHECK (step_index BETWEEN 0 AND 64),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1), mutation_token TEXT NOT NULL DEFAULT '',
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0,1)),
    resume_count INTEGER NOT NULL DEFAULT 0 CHECK (resume_count BETWEEN 0 AND 16),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_token TEXT NOT NULL DEFAULT '', lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_expires_at TEXT, next_run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workflow_run_id TEXT, workflow_node_key TEXT,
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT, completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_email, client_request_id),
    CHECK ((workflow_run_id IS NULL AND workflow_node_key IS NULL)
      OR (workflow_run_id IS NOT NULL AND workflow_node_key IS NOT NULL))
  );

CREATE TABLE ai_agent_provider_dispatches (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES ai_agent_jobs(id) ON DELETE RESTRICT,
    dispatch_ordinal INTEGER NOT NULL CHECK (dispatch_ordinal BETWEEN 1 AND 20),
    owner_email TEXT NOT NULL COLLATE NOCASE,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('analyst','operator','admin')),
    model_id TEXT NOT NULL,
    model_version INTEGER NOT NULL CHECK (model_version >= 1),
    tool_policy_digest TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'calling' CHECK (state IN ('calling','succeeded','failed','unknown')),
    lease_epoch INTEGER NOT NULL CHECK (lease_epoch >= 1),
    reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    provider_called_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', completed_at TEXT
  );

CREATE TABLE ai_agent_provider_results (
    dispatch_id TEXT PRIMARY KEY NOT NULL
      REFERENCES ai_agent_provider_dispatches(id) ON DELETE RESTRICT,
    response_json TEXT NOT NULL CHECK (json_valid(response_json)),
    response_digest TEXT NOT NULL,
    usage_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(usage_json)),
    provider_request_id TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_agent_tool_dispatches (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES ai_agent_jobs(id) ON DELETE RESTRICT,
    provider_dispatch_id TEXT NOT NULL
      REFERENCES ai_agent_provider_dispatches(id) ON DELETE RESTRICT,
    tool_call_ordinal INTEGER NOT NULL CHECK (tool_call_ordinal BETWEEN 1 AND 40),
    provider_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL CHECK (json_valid(arguments_json)),
    arguments_digest TEXT NOT NULL,
    invocation_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'calling'
      CHECK (state IN ('calling','succeeded','failed','unknown')),
    lease_epoch INTEGER NOT NULL CHECK (lease_epoch >= 1),
    reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tool_called_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    completed_at TEXT
  );

CREATE TABLE ai_agent_tool_results (
    tool_dispatch_id TEXT PRIMARY KEY NOT NULL
      REFERENCES ai_agent_tool_dispatches(id) ON DELETE RESTRICT,
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    result_digest TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_analysis_runs (
    id TEXT PRIMARY KEY NOT NULL,
    owner_email TEXT NOT NULL COLLATE NOCASE,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('viewer', 'analyst', 'operator', 'admin')),
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json)),
    dataset TEXT NOT NULL CHECK (dataset IN ('sales_category', 'netshop_product_daily', 'netshop_promotion')),
    query_digest TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    operations_json TEXT NOT NULL CHECK (json_valid(operations_json)),
    data_cutoff_date TEXT,
    source_rows INTEGER NOT NULL CHECK (source_rows >= 0),
    returned_rows INTEGER NOT NULL CHECK (returned_rows >= 0),
    truncated INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
    result_digest TEXT NOT NULL,
    request_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_artifact_deliveries (
    id TEXT PRIMARY KEY NOT NULL,
    artifact_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    actor_email TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    surface TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    byte_size INTEGER,
    content_digest TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_artifacts (
    id TEXT PRIMARY KEY NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    owner_email TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('table')),
    title TEXT NOT NULL,
    file_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    source_tool TEXT NOT NULL,
    columns_json TEXT NOT NULL DEFAULT '[]',
    rows_json TEXT NOT NULL DEFAULT '[]',
    row_count INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
    content_digest TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_channel_callback_events (
    id TEXT PRIMARY KEY NOT NULL,
    channel_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel_id, event_key)
  );

CREATE TABLE ai_channels (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('dingtalk_group_bot', 'dingtalk_app', 'wechat_work_group_bot', 'wechat_work_app')),
    status TEXT NOT NULL CHECK (status IN ('enabled', 'disabled')),
    send_enabled INTEGER NOT NULL DEFAULT 0,
    callback_enabled INTEGER NOT NULL DEFAULT 0,
    webhook_url TEXT NOT NULL DEFAULT '',
    callback_token_encrypted TEXT NOT NULL DEFAULT '',
    callback_token_suffix TEXT NOT NULL DEFAULT '',
    aes_key_encrypted TEXT NOT NULL DEFAULT '',
    aes_key_suffix TEXT NOT NULL DEFAULT '',
    last_test_result TEXT,
    last_tested_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  , receiver_id TEXT NOT NULL DEFAULT '');

CREATE TABLE ai_chat_provider_dispatches (
    id TEXT PRIMARY KEY NOT NULL,
    receipt_id TEXT NOT NULL,
    owner_email TEXT NOT NULL,
    model_id TEXT NOT NULL,
    dispatch_ordinal INTEGER NOT NULL CHECK (dispatch_ordinal > 0),
    reserved_at TEXT NOT NULL,
    provider_called_at TEXT,
    FOREIGN KEY (receipt_id) REFERENCES ai_chat_request_receipts(id) ON DELETE CASCADE
  );

CREATE TABLE ai_chat_request_receipts (
    id TEXT PRIMARY KEY NOT NULL,
    owner_email TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'dispatched', 'succeeded', 'failed', 'unknown')),
    model_id TEXT,
    conversation_id TEXT,
    assistant_message_id TEXT,
    result_json TEXT,
    error_code TEXT,
    admitted_at TEXT,
    provider_started_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY (conversation_id) REFERENCES ai_conversations(id) ON DELETE SET NULL
  );

CREATE TABLE ai_conversation_deletion_audits (
    audit_id TEXT PRIMARY KEY NOT NULL,
    conversation_id TEXT NOT NULL UNIQUE,
    conversation_owner TEXT NOT NULL,
    actor_email TEXT NOT NULL,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('viewer', 'analyst', 'operator', 'admin')),
    reason TEXT NOT NULL,
    deleted_message_count INTEGER NOT NULL DEFAULT 0,
    deleted_artifact_count INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_conversation_messages (
    id TEXT PRIMARY KEY NOT NULL,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  , message_kind TEXT NOT NULL DEFAULT 'message');

CREATE TABLE ai_conversation_scopes (
    conversation_id TEXT PRIMARY KEY NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
    scope_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_conversations (
    id TEXT PRIMARY KEY NOT NULL,
    title TEXT NOT NULL,
    model_id TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_knowledge_entries (
    id TEXT PRIMARY KEY NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('system_policy', 'business_metric', 'identity_mapping')),
    source_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    allowed_roles_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    version INTEGER NOT NULL DEFAULT 1,
    content_digest TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_memory_audit_logs (
    id TEXT PRIMARY KEY NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    owner_email TEXT NOT NULL COLLATE NOCASE,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('viewer', 'analyst', 'operator', 'admin')),
    operation TEXT NOT NULL CHECK (operation IN ('create', 'update', 'archive', 'duplicate')),
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'duplicate')),
    scope_digest TEXT NOT NULL,
    before_digest TEXT,
    after_digest TEXT,
    result_version INTEGER NOT NULL CHECK (result_version > 0),
    policy_version TEXT NOT NULL,
    gate_results_json TEXT NOT NULL CHECK (json_valid(gate_results_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_memory_commit_guards (
    operation_id TEXT PRIMARY KEY NOT NULL,
    audit_present INTEGER NOT NULL CHECK (audit_present = 1)
  );

CREATE TABLE ai_memory_entries (
    id TEXT PRIMARY KEY NOT NULL,
    owner_email TEXT NOT NULL COLLATE NOCASE,
    kind TEXT NOT NULL CHECK (kind IN ('preference', 'glossary', 'business_context')),
    memory_key TEXT NOT NULL,
    memory_key_normalized TEXT NOT NULL,
    content TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    scope_mode TEXT NOT NULL CHECK (scope_mode IN ('owner', 'data_scope')),
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json)),
    scope_digest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    source TEXT NOT NULL CHECK (source IN ('management_ui', 'web_chat')),
    source_conversation_id TEXT,
    source_message_id TEXT,
    last_operation_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT
  );

CREATE TABLE ai_models (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    protocol TEXT NOT NULL CHECK (protocol IN ('openai_compatible', 'anthropic')),
    model_type TEXT NOT NULL CHECK (model_type IN ('text', 'image', 'vision')),
    model_name TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    api_key_encrypted TEXT NOT NULL DEFAULT '',
    api_key_suffix TEXT NOT NULL DEFAULT '',
    is_default_text_model INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('enabled', 'disabled')),
    last_test_result TEXT,
    last_tested_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  , timeout_ms INTEGER NOT NULL DEFAULT 20000, max_tokens INTEGER NOT NULL DEFAULT 1024, temperature_milli INTEGER NOT NULL DEFAULT 200, max_tool_rounds INTEGER NOT NULL DEFAULT 6, max_total_tool_calls INTEGER NOT NULL DEFAULT 12, reasoning_mode TEXT NOT NULL DEFAULT 'auto', version INTEGER NOT NULL DEFAULT 1);

CREATE TABLE ai_space_admin_audits (
    id TEXT PRIMARY KEY NOT NULL, actor_email TEXT NOT NULL COLLATE NOCASE, actor_role TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('upsert_profile','delete_profile','upsert_template','delete_template')),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('model_profile','template')), entity_id TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_space_asset_cleanup_queue (
    object_key TEXT PRIMARY KEY NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_space_asset_favorites (
    asset_id TEXT NOT NULL REFERENCES ai_space_assets(id) ON DELETE CASCADE,
    actor_email TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(asset_id, actor_email)
  );

CREATE TABLE ai_space_assets (
    id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES ai_space_jobs(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL UNIQUE REFERENCES ai_space_job_items(id) ON DELETE CASCADE,
    owner_email TEXT NOT NULL COLLATE NOCASE, scope_json TEXT NOT NULL, scene TEXT NOT NULL,
    object_key TEXT NOT NULL UNIQUE, content_sha256 TEXT NOT NULL,
    mime_type TEXT NOT NULL CHECK (mime_type IN ('image/png','image/jpeg','image/webp')),
    byte_size INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_space_dispatch_receipts (
    id TEXT PRIMARY KEY NOT NULL, item_id TEXT NOT NULL UNIQUE, job_id TEXT NOT NULL,
    owner_email TEXT NOT NULL COLLATE NOCASE, actor_role TEXT NOT NULL,
    model_profile_id TEXT NOT NULL, model_profile_version INTEGER NOT NULL, model_name TEXT NOT NULL,
    scene TEXT NOT NULL, size TEXT NOT NULL, prompt_digest TEXT NOT NULL,
    dispatched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_space_dispatch_results (
    dispatch_id TEXT PRIMARY KEY NOT NULL REFERENCES ai_space_dispatch_receipts(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('succeeded','failed')), provider_request_id TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '', usage_json TEXT NOT NULL DEFAULT '{}',
    estimated_cost_cents INTEGER, price_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_space_job_items (
    id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES ai_space_jobs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 4),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0, lease_token TEXT NOT NULL DEFAULT '', lease_epoch INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT, provider_request_id TEXT NOT NULL DEFAULT '', asset_id TEXT,
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', duration_ms INTEGER,
    started_at TEXT, completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, dispatch_started_at TEXT, pending_object_key TEXT NOT NULL DEFAULT '',
    UNIQUE(job_id, ordinal)
  );

CREATE TABLE ai_space_jobs (
    id TEXT PRIMARY KEY NOT NULL, client_request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
    owner_email TEXT NOT NULL COLLATE NOCASE, scope_json TEXT NOT NULL,
    scene TEXT NOT NULL CHECK (scene IN ('product_main', 'product_detail', 'promotion')),
    template_id TEXT NOT NULL, template_name TEXT NOT NULL, template_version INTEGER NOT NULL,
    model_profile_id TEXT NOT NULL, model_profile_name TEXT NOT NULL, model_name TEXT NOT NULL,
    product_name TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '', sku TEXT NOT NULL DEFAULT '',
    selling_points TEXT NOT NULL DEFAULT '', final_prompt TEXT NOT NULL, prompt_digest TEXT NOT NULL,
    size TEXT NOT NULL CHECK (size IN ('1024x1024', '1024x1536', '1536x1024')),
    requested_count INTEGER NOT NULL CHECK (requested_count BETWEEN 1 AND 4),
    succeeded_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
    cancelled_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','partial','failed','cancelled')),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT, completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, model_profile_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(owner_email, client_request_id)
  );

CREATE TABLE ai_space_model_profiles (
    id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
    protocol TEXT NOT NULL DEFAULT 'openai_images' CHECK (protocol = 'openai_images'),
    model_name TEXT NOT NULL, base_url TEXT NOT NULL,
    api_key_encrypted TEXT NOT NULL DEFAULT '', api_key_suffix TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'enabled' CHECK (status IN ('enabled', 'disabled')),
    timeout_ms INTEGER NOT NULL DEFAULT 90000 CHECK (timeout_ms BETWEEN 3000 AND 120000),
    last_success_result TEXT, last_success_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  , version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1));

CREATE TABLE ai_space_schema_upgrades (
    id TEXT PRIMARY KEY NOT NULL, completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_space_templates (
    id TEXT PRIMARY KEY NOT NULL,
    scene TEXT NOT NULL CHECK (scene IN ('product_main', 'product_detail', 'promotion')),
    name TEXT NOT NULL, prompt_template TEXT NOT NULL,
    size TEXT NOT NULL DEFAULT '1024x1024' CHECK (size IN ('1024x1024', '1024x1536', '1536x1024')),
    model_profile_id TEXT REFERENCES ai_space_model_profiles(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    updated_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (is_default = 0 OR is_enabled = 1)
  );

CREATE TABLE ai_system_settings (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_tool_audit_logs (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL,
    actor_email TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    surface TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    row_count INTEGER,
    duration_ms INTEGER,
    response_digest TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_workflow_events (
    id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL REFERENCES ai_workflow_runs(id) ON DELETE CASCADE,
    node_key TEXT, owner_email TEXT NOT NULL COLLATE NOCASE, actor_email TEXT NOT NULL COLLATE NOCASE,
    event_type TEXT NOT NULL, from_status TEXT NOT NULL DEFAULT '', to_status TEXT NOT NULL DEFAULT '',
    run_version INTEGER NOT NULL CHECK (run_version >= 1),
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE ai_workflow_node_runs (
    id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL REFERENCES ai_workflow_runs(id) ON DELETE CASCADE,
    node_key TEXT NOT NULL, position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 23),
    node_type TEXT NOT NULL CHECK (node_type IN ('agent','human_review')),
    depends_on_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(depends_on_json)), instruction TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_json)),
    output_json TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
    status TEXT NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending','running','waiting_review','completed','rejected','skipped','failed','cancelled')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1), mutation_token TEXT NOT NULL DEFAULT '',
    agent_job_id TEXT REFERENCES ai_agent_jobs(id) ON DELETE SET NULL,
    reviewer_email TEXT, reviewed_at TEXT,
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT, completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, node_key), UNIQUE(run_id, position)
  );

CREATE TABLE ai_workflow_runs (
    id TEXT PRIMARY KEY NOT NULL, owner_email TEXT NOT NULL COLLATE NOCASE,
    client_request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json)), name TEXT NOT NULL,
    graph_json TEXT NOT NULL CHECK (json_valid(graph_json)), graph_digest TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_json)),
    output_json TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
    model_id TEXT NOT NULL DEFAULT '',
    model_version INTEGER NOT NULL DEFAULT 0 CHECK (model_version >= 0),
    allowed_tools_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(allowed_tools_json)),
    tool_policy_digest TEXT NOT NULL DEFAULT '',
    provider_round_count INTEGER NOT NULL DEFAULT 0 CHECK (provider_round_count >= 0),
    tool_call_count INTEGER NOT NULL DEFAULT 0 CHECK (tool_call_count >= 0),
    provider_dispatch_started_at TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0 CHECK (dry_run IN (0,1)),
    status TEXT NOT NULL DEFAULT 'queued'
      CHECK (status IN ('queued','running','waiting_review','paused','completed','failed','cancelled')),
    current_node_key TEXT, version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    mutation_token TEXT NOT NULL DEFAULT '', cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0,1)),
    resume_count INTEGER NOT NULL DEFAULT 0 CHECK (resume_count BETWEEN 0 AND 16),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_token TEXT NOT NULL DEFAULT '', lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_expires_at TEXT, next_run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT, completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_email, client_request_id)
  );
