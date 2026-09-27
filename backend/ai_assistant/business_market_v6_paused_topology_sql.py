"""0077 SQL-owned, default-closed five-job paused market topology.

The dedicated role has EXECUTE on three narrow functions and no table DML.
No function selects a model, reserves funds, or dispatches a provider/tool.
"""

ROLE = "teruisi_ai_market_v6_topology_login"
TABLE = "public.protected_business_market_v6_topologies"
CANCEL_TABLE = "public.protected_business_market_v6_topology_cancellations"
CREATE = "public.ai_market_v6_create_paused_topology(text,text,text)"
CANCEL = "public.ai_market_v6_cancel_paused_topology(text)"
OUTCOME = "public.ai_market_v6_paused_topology_outcome(text,text,text,text)"
ROW_GUARD = "public.ai_market_v6_topology_row_guard()"
STATE_GUARD = "public.ai_market_v6_topology_state_guard()"
EFFECT_GUARD = "public.ai_market_v6_topology_effect_guard()"
ANCILLARY_GUARD = "public.ai_market_v6_topology_ancillary_guard()"
SIGNATURES = (ROW_GUARD, STATE_GUARD, EFFECT_GUARD, ANCILLARY_GUARD,
    CREATE, CANCEL, OUTCOME)
ROLES = ("commerce", "promotion", "market_b2b", "independent_review", "report")

CREATE_TABLES = """CREATE TABLE public.protected_business_market_v6_topologies (
  report_id varchar(160) PRIMARY KEY REFERENCES public.ai_report_runs(id) ON DELETE RESTRICT,
  workflow_id varchar(160) NOT NULL UNIQUE REFERENCES public.ai_workflow_runs(id) ON DELETE RESTRICT,
  owner_email varchar(320) NOT NULL,
  owner_version bigint NOT NULL CHECK (owner_version>=1),
  client_request_id varchar(128) NOT NULL,
  request_digest varchar(64) NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
  snapshot_json text NOT NULL,
  snapshot_digest varchar(64) NOT NULL CHECK (snapshot_digest ~ '^[0-9a-f]{64}$'),
  graph_json text NOT NULL CHECK (octet_length(graph_json) <= 32768),
  graph_digest varchar(64) NOT NULL CHECK (graph_digest ~ '^[0-9a-f]{64}$'),
  source_report_id varchar(160) NOT NULL REFERENCES public.ai_report_runs(id) ON DELETE RESTRICT,
  source_plan_id varchar(64) NOT NULL REFERENCES public.ai_business_market_v2_execution_plans(id) ON DELETE RESTRICT,
  source_plan_digest varchar(64) NOT NULL,
  source_cost_id varchar(64) NOT NULL REFERENCES public.ai_business_market_v2_cost_ledger_candidates(id) ON DELETE RESTRICT,
  source_cost_digest varchar(64) NOT NULL,
  created_at timestamptz NOT NULL,
  CONSTRAINT ai_market_v6_topology_owner_client_uq UNIQUE(owner_email,client_request_id)
);
CREATE TABLE public.protected_business_market_v6_topology_cancellations (
  report_id varchar(160) PRIMARY KEY REFERENCES public.protected_business_market_v6_topologies(report_id) ON DELETE RESTRICT,
  request_digest varchar(64) NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
  reason_digest varchar(64) NOT NULL CHECK (reason_digest ~ '^[0-9a-f]{64}$'),
  expected_version bigint NOT NULL CHECK (expected_version>=1),
  cancelled_at timestamptz NOT NULL
)"""

ROW_GUARD_SQL = """CREATE FUNCTION public.ai_market_v6_topology_row_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' OR session_user<>'teruisi_ai_market_v6_topology_login'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_market_v6_topology_immutable'; END IF;
  RETURN NEW;
END $$"""

STATE_GUARD_SQL = """CREATE FUNCTION public.ai_market_v6_topology_state_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE selected_report text; selected_flow text; cancellation_exists boolean;
BEGIN
  IF TG_OP='UPDATE' THEN
    IF TG_TABLE_NAME='ai_agent_jobs' THEN
      IF NEW.workflow_run_id IS DISTINCT FROM OLD.workflow_run_id AND
         EXISTS(SELECT 1 FROM public.protected_business_market_v6_topologies t
           WHERE t.workflow_id=NEW.workflow_run_id)
      THEN RAISE EXCEPTION 'ai_market_v6_topology_cross_flow_update'; END IF;
    ELSIF TG_TABLE_NAME='ai_workflow_node_runs' THEN
      IF NEW.run_id IS DISTINCT FROM OLD.run_id AND
         EXISTS(SELECT 1 FROM public.protected_business_market_v6_topologies t
           WHERE t.workflow_id=NEW.run_id)
      THEN RAISE EXCEPTION 'ai_market_v6_topology_cross_flow_update'; END IF;
    END IF;
  END IF;
  IF TG_TABLE_NAME='ai_workflow_runs' THEN
    IF TG_OP='INSERT' THEN selected_flow:=NEW.id;
    ELSE selected_flow:=OLD.id; END IF;
  ELSIF TG_TABLE_NAME='ai_workflow_node_runs' THEN
    IF TG_OP='INSERT' THEN selected_flow:=NEW.run_id;
    ELSE selected_flow:=OLD.run_id; END IF;
  ELSIF TG_TABLE_NAME='ai_agent_jobs' THEN
    IF TG_OP='INSERT' THEN selected_flow:=NEW.workflow_run_id;
    ELSE selected_flow:=OLD.workflow_run_id; END IF;
  ELSE RAISE EXCEPTION 'ai_market_v6_topology_guard_binding'; END IF;
  SELECT t.report_id INTO selected_report FROM
    public.protected_business_market_v6_topologies t WHERE
    t.workflow_id=selected_flow;
  IF selected_report IS NULL THEN
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
  END IF;
  IF TG_OP<>'UPDATE' OR session_user<>'teruisi_ai_market_v6_topology_login'
  THEN RAISE EXCEPTION 'ai_market_v6_topology_state_immutable'; END IF;
  SELECT EXISTS(SELECT 1 FROM public.protected_business_market_v6_topology_cancellations c
    WHERE c.report_id=selected_report) INTO cancellation_exists;
  IF NOT cancellation_exists OR NEW.version IS DISTINCT FROM OLD.version+1
     OR NEW.status IS DISTINCT FROM 'cancelled'
  THEN RAISE EXCEPTION 'ai_market_v6_topology_cancel_fence'; END IF;
  IF TG_TABLE_NAME='ai_workflow_node_runs' THEN
    IF OLD.status<>'pending' OR to_jsonb(NEW)-ARRAY[
       'status','version','updated_at'] IS DISTINCT FROM
       to_jsonb(OLD)-ARRAY['status','version','updated_at']
    THEN RAISE EXCEPTION 'ai_market_v6_topology_node_drift'; END IF;
  ELSIF TG_TABLE_NAME='ai_agent_jobs' THEN
    IF OLD.status<>'paused' OR NEW.phase<>'cancelled'
       OR NEW.cancel_requested<>1 OR NEW.model_id<>''
       OR NEW.allowed_tools_json<>'[]' OR NEW.tool_policy_digest<>''
       OR NEW.provider_round_count<>0 OR NEW.tool_call_count<>0
       OR to_jsonb(NEW)-ARRAY['status','phase','version',
          'cancel_requested','error_code','completed_at','updated_at']
          IS DISTINCT FROM to_jsonb(OLD)-ARRAY['status','phase','version',
          'cancel_requested','error_code','completed_at','updated_at']
    THEN RAISE EXCEPTION 'ai_market_v6_topology_job_drift'; END IF;
  ELSE
    IF OLD.status<>'paused' OR NEW.cancel_requested<>1 OR NEW.model_id<>''
       OR NEW.allowed_tools_json<>'[]' OR NEW.tool_policy_digest<>''
       OR NEW.provider_round_count<>0 OR NEW.tool_call_count<>0
       OR to_jsonb(NEW)-ARRAY['status','version','cancel_requested',
          'error_code','completed_at','updated_at'] IS DISTINCT FROM
          to_jsonb(OLD)-ARRAY['status','version','cancel_requested',
          'error_code','completed_at','updated_at']
    THEN RAISE EXCEPTION 'ai_market_v6_topology_flow_drift'; END IF;
  END IF;
  RETURN NEW;
END $$"""

EFFECT_GUARD_SQL = """CREATE FUNCTION public.ai_market_v6_topology_effect_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE selected_job text;
BEGIN
  IF TG_OP='DELETE' THEN selected_job:=OLD.job_id;
  ELSE selected_job:=NEW.job_id; END IF;
  IF TG_OP='UPDATE' AND OLD.job_id IS DISTINCT FROM NEW.job_id AND
     EXISTS(SELECT 1 FROM public.ai_agent_jobs j JOIN
       public.protected_business_market_v6_topologies t ON
       t.workflow_id=j.workflow_run_id WHERE j.id=OLD.job_id)
  THEN RAISE EXCEPTION 'ai_market_v6_topology_effect_reassignment'; END IF;
  IF EXISTS(SELECT 1 FROM public.ai_agent_jobs j JOIN
      public.protected_business_market_v6_topologies t ON
      t.workflow_id=j.workflow_run_id WHERE j.id=selected_job)
  THEN RAISE EXCEPTION 'ai_market_v6_topology_effect_closed'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$"""

ANCILLARY_GUARD_SQL = """CREATE FUNCTION public.ai_market_v6_topology_ancillary_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE old_flow text; new_flow text;
BEGIN
  IF TG_TABLE_NAME='ai_workflow_events' THEN
    IF TG_OP='INSERT' THEN new_flow:=NEW.run_id;
    ELSIF TG_OP='DELETE' THEN old_flow:=OLD.run_id;
    ELSE old_flow:=OLD.run_id; new_flow:=NEW.run_id; END IF;
  ELSIF TG_TABLE_NAME IN ('ai_report_deliveries','ai_business_file_runs') THEN
    IF TG_OP='INSERT' THEN
      SELECT t.workflow_id INTO new_flow FROM
        public.protected_business_market_v6_topologies t WHERE
        t.report_id=NEW.report_id;
    ELSIF TG_OP='DELETE' THEN
      SELECT t.workflow_id INTO old_flow FROM
        public.protected_business_market_v6_topologies t WHERE
        t.report_id=OLD.report_id;
    ELSE
      SELECT t.workflow_id INTO old_flow FROM
        public.protected_business_market_v6_topologies t WHERE
        t.report_id=OLD.report_id;
      SELECT t.workflow_id INTO new_flow FROM
        public.protected_business_market_v6_topologies t WHERE
        t.report_id=NEW.report_id;
    END IF;
  ELSE RAISE EXCEPTION 'ai_market_v6_topology_ancillary_binding'; END IF;
  IF EXISTS(SELECT 1 FROM public.protected_business_market_v6_topologies t
      WHERE t.workflow_id=old_flow OR t.workflow_id=new_flow)
  THEN RAISE EXCEPTION 'ai_market_v6_topology_effect_closed'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$"""

CREATE_SQL = """CREATE FUNCTION public.ai_market_v6_create_paused_topology(
  intent_text text,snapshot_text text,graph_text text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE intent jsonb; snapshot jsonb; graph jsonb; plan jsonb; cost_json jsonb;
  actor public.access_control_users%ROWTYPE;
  plan_row public.ai_business_market_v2_execution_plans%ROWTYPE;
  cost_row public.ai_business_market_v2_cost_ledger_candidates%ROWTYPE;
  source_report public.ai_report_runs%ROWTYPE;
  item jsonb; node jsonb; role_name text; job_id text; node_id text;
  expected_node_job text;
  roles text[]:=ARRAY['commerce','promotion','market_b2b','independent_review','report'];
  i integer; owner_workflows integer; global_workflows integer;
  owner_jobs integer; global_jobs integer; report_id text; workflow_id text;
  owner_email text; target_owner text; client_id text; source_id text; snapshot_digest text;
  intent_digest text; expected_id text;
BEGIN
  IF session_user<>'teruisi_ai_market_v6_topology_login'
     OR current_database() NOT IN ('teruisi_ai_rehearsal',
       'test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR inet_server_addr()::text NOT IN
       ('127.0.0.1','127.0.0.1/32','::1','::1/128')
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE oid='public.ai_workflow_runs'::regclass))
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
       r.rolname='teruisi_ai_market_v6_topology_login' AND r.rolcanlogin
       AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
       AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE
       m.roleid='teruisi_ai_market_v6_topology_login'::regrole OR
       m.member='teruisi_ai_market_v6_topology_login'::regrole)
     OR intent_text IS NULL OR octet_length(intent_text) NOT BETWEEN 1 AND 4096
     OR snapshot_text IS NULL OR octet_length(snapshot_text) NOT BETWEEN 1 AND 16384
     OR graph_text IS NULL OR octet_length(graph_text) NOT BETWEEN 1 AND 32768
  THEN RAISE EXCEPTION 'ai_market_v6_topology_unavailable'; END IF;
  intent:=intent_text::jsonb; snapshot:=snapshot_text::jsonb; graph:=graph_text::jsonb;
  IF jsonb_typeof(intent)<>'object' OR jsonb_typeof(snapshot)<>'object'
     OR jsonb_typeof(graph)<>'object'
     OR intent_text IS DISTINCT FROM public.ai_v4_replay_canonical(intent)
     OR snapshot_text IS DISTINCT FROM public.ai_v4_replay_canonical(snapshot)
     OR graph_text IS DISTINCT FROM public.ai_v4_replay_canonical(graph)
  THEN RAISE EXCEPTION 'ai_market_v6_topology_noncanonical'; END IF;
  owner_email:=intent->>'actorEmail'; client_id:=intent->>'clientRequestId';
  target_owner:=owner_email;
  source_id:=intent->>'sourceExecutionReportId'; report_id:=intent->>'reportId';
  workflow_id:=intent->>'workflowId';
  snapshot_digest:=encode(sha256(convert_to(snapshot_text,'UTF8')),'hex');
  intent_digest:=encode(sha256(convert_to(intent_text,'UTF8')),'hex');
  IF owner_email IS NULL OR owner_email<>lower(owner_email) OR
     owner_email !~ '^[^@ ]+@[^@ ]+$' OR octet_length(owner_email)>320
     OR client_id IS NULL OR client_id !~ '^[A-Za-z0-9._:-]{8,128}$'
     OR source_id IS NULL OR source_id !~ '^[A-Za-z0-9_-]{1,160}$'
     OR report_id IS NULL OR workflow_id IS NULL
     OR report_id !~ '^market-v6-report-[0-9a-f]{48}$'
     OR workflow_id !~ '^market-v6-flow-[0-9a-f]{48}$'
     OR report_id IS DISTINCT FROM 'market-v6-report-'||substr(encode(sha256(
       convert_to(public.ai_v4_replay_canonical(jsonb_build_array(
         'market-v6-report',owner_email,source_id,client_id)),'UTF8')),'hex'),1,48)
     OR workflow_id IS DISTINCT FROM 'market-v6-flow-'||substr(encode(sha256(
       convert_to(public.ai_v4_replay_canonical(jsonb_build_array(
         'market-v6-flow',owner_email,source_id,client_id)),'UTF8')),'hex'),1,48)
     OR intent->>'schemaVersion'<>'business-market-v6-paused-topology-intent-v1'
     OR snapshot->>'schemaVersion'<>'business-market-v6-paused-topology-snapshot-v1'
     OR snapshot->>'executionProfile'<>'business-agent-screening-promotion-market-paused-v6'
     OR NOT intent ?& ARRAY['schemaVersion','clientRequestId','actorEmail',
       'expectedActorVersion','sourceExecutionReportId','sourcePlanId',
       'sourcePlanDigest','sourceUnverifiedCostCandidateId',
       'sourceUnverifiedCostCandidateDigest','reportId','workflowId',
       'snapshotDigest','quotaObservation','providerCallsAllowed']
     OR intent-ARRAY['schemaVersion','clientRequestId','actorEmail',
       'expectedActorVersion','sourceExecutionReportId','sourcePlanId',
       'sourcePlanDigest','sourceUnverifiedCostCandidateId',
       'sourceUnverifiedCostCandidateDigest','reportId','workflowId',
       'snapshotDigest','quotaObservation','providerCallsAllowed']<>'{}'::jsonb
     OR NOT snapshot ?& ARRAY['schemaVersion','executionProfile','reportId',
       'workflowId','clientRequestId','ownerEmail','ownerVersion',
       'sourceExecutionReportId','sourcePlanId','sourcePlanDigest',
       'sourceUnverifiedCostCandidateId','sourceUnverifiedCostCandidateDigest',
       'sourceCostCandidateSpendable','futureModelSpecificAuthorityId',
       'approvedCnyCapCents','tariffAuthorityVerified',
       'humanCapApprovalVerified','durablePaidReservation',
       'modelSelectionDeferred','modelId','modelVersion','withBudget',
       'graphDigest','toolCatalogDigest','allowedTools','contextProofDigest',
       'marketContextDigest','selectorDigest','manifestDigest','roles',
       'jobs','nodes','intendedStatus','providerCallsAllowed',
       'toolDispatchAllowed','externalProviderCalled','agentReadPersisted',
       'numericCitationAllowed','humanReviewApproved',
       'reportPublishAuthorized','syntheticOnly']
     OR snapshot-ARRAY['schemaVersion','executionProfile','reportId',
       'workflowId','clientRequestId','ownerEmail','ownerVersion',
       'sourceExecutionReportId','sourcePlanId','sourcePlanDigest',
       'sourceUnverifiedCostCandidateId','sourceUnverifiedCostCandidateDigest',
       'sourceCostCandidateSpendable','futureModelSpecificAuthorityId',
       'approvedCnyCapCents','tariffAuthorityVerified',
       'humanCapApprovalVerified','durablePaidReservation',
       'modelSelectionDeferred','modelId','modelVersion','withBudget',
       'graphDigest','toolCatalogDigest','allowedTools','contextProofDigest',
       'marketContextDigest','selectorDigest','manifestDigest','roles',
       'jobs','nodes','intendedStatus','providerCallsAllowed',
       'toolDispatchAllowed','externalProviderCalled','agentReadPersisted',
       'numericCitationAllowed','humanReviewApproved',
       'reportPublishAuthorized','syntheticOnly']<>'{}'::jsonb
     OR intent->>'snapshotDigest' IS DISTINCT FROM snapshot_digest
     OR intent->>'reportId' IS DISTINCT FROM snapshot->>'reportId'
     OR intent->>'workflowId' IS DISTINCT FROM snapshot->>'workflowId'
     OR intent->>'actorEmail' IS DISTINCT FROM snapshot->>'ownerEmail'
     OR intent->>'clientRequestId' IS DISTINCT FROM snapshot->>'clientRequestId'
     OR intent->>'expectedActorVersion' IS DISTINCT FROM snapshot->>'ownerVersion'
     OR intent->>'sourceExecutionReportId' IS DISTINCT FROM snapshot->>'sourceExecutionReportId'
     OR intent->>'sourcePlanId' IS DISTINCT FROM snapshot->>'sourcePlanId'
     OR intent->>'sourcePlanDigest' IS DISTINCT FROM snapshot->>'sourcePlanDigest'
     OR intent->>'sourceUnverifiedCostCandidateId' IS DISTINCT FROM snapshot->>'sourceUnverifiedCostCandidateId'
     OR intent->>'sourceUnverifiedCostCandidateDigest' IS DISTINCT FROM snapshot->>'sourceUnverifiedCostCandidateDigest'
     OR intent->'providerCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'providerCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'toolDispatchAllowed' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'agentReadPersisted' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'numericCitationAllowed' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'durablePaidReservation' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'futureModelSpecificAuthorityId' IS DISTINCT FROM 'null'::jsonb
     OR snapshot->'approvedCnyCapCents' IS DISTINCT FROM 'null'::jsonb
     OR snapshot->'modelSelectionDeferred' IS DISTINCT FROM 'true'::jsonb
     OR snapshot->'humanReviewApproved' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'sourceCostCandidateSpendable' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'tariffAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'humanCapApprovalVerified' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'externalProviderCalled' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'reportPublishAuthorized' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'syntheticOnly' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->>'modelId' IS DISTINCT FROM ''
     OR snapshot->'modelVersion' IS DISTINCT FROM '0'::jsonb
     OR snapshot->>'intendedStatus' IS DISTINCT FROM 'paused'
     OR snapshot->'roles' IS DISTINCT FROM to_jsonb(roles)
     OR jsonb_typeof(snapshot->'jobs') IS DISTINCT FROM 'array'
     OR jsonb_typeof(snapshot->'nodes') IS DISTINCT FROM 'array'
     OR jsonb_typeof(graph->'nodes') IS DISTINCT FROM 'array'
     OR jsonb_array_length(snapshot->'jobs')<>5
     OR jsonb_array_length(snapshot->'nodes')<>6
     OR jsonb_array_length(graph->'nodes')<>6
  THEN RAISE EXCEPTION 'ai_market_v6_topology_contract'; END IF;
  SELECT * INTO actor FROM public.access_control_users a WHERE a.email=owner_email FOR SHARE;
  SELECT * INTO plan_row FROM public.ai_business_market_v2_execution_plans p
    WHERE p.id=intent->>'sourcePlanId' FOR SHARE;
  SELECT * INTO cost_row FROM public.ai_business_market_v2_cost_ledger_candidates c
    WHERE c.id=intent->>'sourceUnverifiedCostCandidateId' FOR SHARE;
  SELECT * INTO source_report FROM public.ai_report_runs r WHERE r.id=source_id FOR SHARE;
  IF actor.email IS NULL OR actor.status<>'active' OR actor.role<>'admin'
     OR actor.scope IS NOT NULL OR actor.version::text IS DISTINCT FROM
       intent->>'expectedActorVersion'
     OR plan_row.id IS NULL OR cost_row.id IS NULL OR source_report.id IS NULL
     OR source_report.owner_email IS DISTINCT FROM owner_email
     OR source_report.scope_json IS DISTINCT FROM 'null'
     OR plan_row.execution_report_id IS DISTINCT FROM source_id
     OR plan_row.plan_digest IS DISTINCT FROM intent->>'sourcePlanDigest'
     OR plan_row.plan_digest IS DISTINCT FROM encode(sha256(convert_to(
       plan_row.plan_json,'UTF8')),'hex')
     OR cost_row.plan_id IS DISTINCT FROM plan_row.id
     OR cost_row.id IS DISTINCT FROM intent->>'sourceUnverifiedCostCandidateId'
     OR cost_row.owner_email IS DISTINCT FROM owner_email
     OR cost_row.candidate_digest IS DISTINCT FROM encode(sha256(convert_to(
       cost_row.candidate_json,'UTF8')),'hex')
     OR cost_row.reserved_cents<>0
     OR cost_row.status<>'pending_rate_and_approval_verification'
  THEN RAISE EXCEPTION 'ai_market_v6_topology_source_drift'; END IF;
  plan:=public.ai_market_v2_execution_plan_expected(source_id,plan_row.plan_json);
  cost_json:=public.ai_market_v2_cost_candidate_expected(
    plan_row.id,cost_row.candidate_json);
  IF cost_json->'providerCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR cost_json->'fundsReserved' IS DISTINCT FROM 'false'::jsonb
     OR cost_json->'reservedCents' IS DISTINCT FROM '0'::jsonb
     OR cost_json->>'candidateDigest' IS DISTINCT FROM
       intent->>'sourceUnverifiedCostCandidateDigest'
     OR snapshot->>'sourcePlanId' IS DISTINCT FROM plan_row.id
     OR snapshot->>'sourcePlanDigest' IS DISTINCT FROM plan_row.plan_digest
     OR snapshot->>'sourceUnverifiedCostCandidateId' IS DISTINCT FROM cost_row.id
     OR snapshot->>'sourceUnverifiedCostCandidateDigest' IS DISTINCT FROM
       cost_json->>'candidateDigest'
     OR snapshot->>'graphDigest' IS DISTINCT FROM plan->>'graphDigest'
     OR snapshot->>'graphDigest' IS DISTINCT FROM encode(sha256(convert_to(
       graph_text,'UTF8')),'hex')
     OR snapshot->>'toolCatalogDigest' IS DISTINCT FROM plan->>'toolCatalogDigest'
     OR snapshot->'allowedTools' IS DISTINCT FROM plan->'proposedTools'
     OR snapshot->>'contextProofDigest' IS DISTINCT FROM plan->'executionRoot'->>'contextProofDigest'
     OR snapshot->>'marketContextDigest' IS DISTINCT FROM plan->'executionRoot'->>'marketContextDigest'
     OR snapshot->>'selectorDigest' IS DISTINCT FROM plan->'executionRoot'->>'selectorDigest'
     OR snapshot->>'manifestDigest' IS DISTINCT FROM plan->'executionRoot'->>'manifestDigest'
     OR snapshot->'withBudget' IS DISTINCT FROM plan->'executionRoot'->'withBudget'
  THEN RAISE EXCEPTION 'ai_market_v6_topology_binding'; END IF;
  FOR i IN 0..4 LOOP
    role_name:=roles[i+1]; item:=snapshot->'jobs'->i;
    job_id:='market-v6-job-'||substr(encode(sha256(convert_to(
      'market-v6-job|'||report_id||'|'||role_name,'UTF8')),'hex'),1,48);
    IF item->>'role' IS DISTINCT FROM role_name OR item->>'jobId' IS DISTINCT FROM job_id
       OR item-ARRAY['role','jobId','workflowId','reportId',
         'intendedStatus','modelId','modelVersion','providerRoundCount',
         'toolCallCount','providerDispatchIds','toolDispatchIds',
         'readReceiptIds']<>'{}'::jsonb
       OR item->>'reportId' IS DISTINCT FROM report_id
       OR item->>'workflowId' IS DISTINCT FROM workflow_id
       OR item->>'intendedStatus' IS DISTINCT FROM 'paused'
       OR item->>'modelId' IS DISTINCT FROM ''
       OR item->'modelVersion' IS DISTINCT FROM '0'::jsonb
       OR item->'providerRoundCount' IS DISTINCT FROM '0'::jsonb
       OR item->'toolCallCount' IS DISTINCT FROM '0'::jsonb
       OR item->'providerDispatchIds' IS DISTINCT FROM '[]'::jsonb
       OR item->'toolDispatchIds' IS DISTINCT FROM '[]'::jsonb
       OR item->'readReceiptIds' IS DISTINCT FROM '[]'::jsonb
    THEN RAISE EXCEPTION 'ai_market_v6_topology_job_shape'; END IF;
  END LOOP;
  FOR i IN 0..5 LOOP
    node:=snapshot->'nodes'->i; item:=graph->'nodes'->i;
    node_id:='market-v6-node-'||substr(encode(sha256(convert_to(
      public.ai_v4_replay_canonical(jsonb_build_array(
        'market-v6-node',report_id,item->>'key')),'UTF8')),'hex'),1,48);
    expected_node_job:=NULL;
    IF item->>'type'='agent' THEN
      expected_node_job:='market-v6-job-'||substr(encode(sha256(convert_to(
        'market-v6-job|'||report_id||'|'||(item->>'key'),'UTF8')),'hex'),1,48);
    END IF;
    IF node->>'nodeId' IS DISTINCT FROM node_id
       OR node-ARRAY['nodeId','nodeKey','nodeType','position',
         'dependsOn','jobId','intendedStatus']<>'{}'::jsonb
       OR node->'position' IS DISTINCT FROM to_jsonb(i)
       OR node->>'nodeKey' IS DISTINCT FROM item->>'key'
       OR node->>'nodeType' IS DISTINCT FROM item->>'type'
       OR node->'dependsOn' IS DISTINCT FROM item->'dependsOn'
       OR node->>'intendedStatus' IS DISTINCT FROM 'pending'
       OR node->>'jobId' IS DISTINCT FROM expected_node_job
    THEN RAISE EXCEPTION 'ai_market_v6_topology_node_shape'; END IF;
  END LOOP;
  PERFORM 1 FROM public.ai_data_revisions WHERE domain='ai-assistant' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_market_v6_revision_missing'; END IF;
  SELECT count(*) INTO owner_workflows FROM public.ai_workflow_runs
    WHERE ai_workflow_runs.owner_email=target_owner
      AND status IN ('queued','running','paused','waiting_review');
  SELECT count(*) INTO global_workflows FROM public.ai_workflow_runs
    WHERE status IN ('queued','running','paused','waiting_review');
  SELECT count(*) INTO owner_jobs FROM public.ai_agent_jobs
    WHERE ai_agent_jobs.owner_email=target_owner
      AND status IN ('queued','running','paused');
  SELECT count(*) INTO global_jobs FROM public.ai_agent_jobs
    WHERE status IN ('queued','running','paused');
  IF owner_workflows+1>4 OR global_workflows+1>24
     OR owner_jobs+5>8 OR global_jobs+5>64
  THEN RAISE EXCEPTION 'ai_market_v6_topology_quota'; END IF;
  IF EXISTS(SELECT 1 FROM public.protected_business_market_v6_topologies t
      WHERE t.report_id=intent->>'reportId' OR t.workflow_id=intent->>'workflowId'
        OR (t.owner_email=target_owner
          AND t.client_request_id=intent->>'clientRequestId'))
     OR EXISTS(SELECT 1 FROM public.ai_report_runs WHERE id=report_id)
     OR EXISTS(SELECT 1 FROM public.ai_workflow_runs WHERE id=workflow_id)
  THEN RAISE EXCEPTION 'ai_market_v6_topology_conflict'; END IF;
  INSERT INTO public.ai_workflow_runs(id,owner_email,client_request_id,
    request_digest,scope_json,name,graph_json,graph_digest,input_json,
    model_id,model_version,allowed_tools_json,tool_policy_digest,dry_run,
    provider_round_count,tool_call_count,status,current_node_key,version,
    mutation_token,cancel_requested,retryable,resume_count,attempt_count,
    lease_token,lease_epoch,next_run_at,error_code,error_message,
    created_at,updated_at)
  VALUES(workflow_id,owner_email,client_id,intent_digest,'null',
    '市场五Agent暂停诊断',graph_text,snapshot->>'graphDigest',snapshot_text,
    '',0,'[]','',0,0,0,'paused',NULL,1,
    '',0,0,0,0,'',0,clock_timestamp(),'','',
    clock_timestamp(),clock_timestamp());
  FOR i IN 0..4 LOOP
    role_name:=roles[i+1]; item:=snapshot->'jobs'->i;
    INSERT INTO public.ai_agent_jobs(id,owner_email,client_request_id,
      request_digest,scope_json,task,input_json,state_json,
      model_id,model_version,allowed_tools_json,tool_policy_digest,
      provider_round_count,tool_call_count,status,phase,step_index,version,
      mutation_token,cancel_requested,retryable,resume_count,attempt_count,
      lease_token,lease_epoch,next_run_at,workflow_run_id,workflow_node_key,
      error_code,error_message,created_at,updated_at)
    VALUES(item->>'jobId',owner_email,client_id||':'||role_name,
      encode(sha256(convert_to(intent_digest||'|'||role_name,'UTF8')),'hex'),
      'null',(graph->'nodes'->i)->>'instruction',snapshot_text,'{}',
      '',0,'[]','',0,0,'paused','paused',0,1,
      '',0,0,0,0,'',0,clock_timestamp(),workflow_id,role_name,
      '','',clock_timestamp(),clock_timestamp());
  END LOOP;
  FOR i IN 0..5 LOOP
    node:=snapshot->'nodes'->i; item:=graph->'nodes'->i;
    INSERT INTO public.ai_workflow_node_runs(id,run_id,node_key,position,
      node_type,depends_on_json,instruction,input_json,status,version,
      mutation_token,agent_job_id,error_code,error_message,created_at,updated_at)
    VALUES(node->>'nodeId',workflow_id,node->>'nodeKey',i,
      node->>'nodeType',public.ai_v4_replay_canonical(node->'dependsOn'),
      item->>'instruction',snapshot_text,'pending',1,'',node->>'jobId',
      '','',clock_timestamp(),clock_timestamp());
  END LOOP;
  INSERT INTO public.ai_report_runs(id,owner_email,scope_json,client_request_id,
    request_digest,workflow_id,snapshot_json,created_at)
  VALUES(report_id,owner_email,'null',client_id,intent_digest,workflow_id,
    snapshot_text,clock_timestamp());
  INSERT INTO public.protected_business_market_v6_topologies(
    report_id,workflow_id,owner_email,owner_version,client_request_id,
    request_digest,snapshot_json,snapshot_digest,graph_json,graph_digest,
    source_report_id,source_plan_id,source_plan_digest,source_cost_id,
    source_cost_digest,created_at)
  VALUES(report_id,workflow_id,owner_email,actor.version,client_id,
    intent_digest,snapshot_text,snapshot_digest,graph_text,
    snapshot->>'graphDigest',source_id,plan_row.id,plan_row.plan_digest,
    cost_row.id,cost_json->>'candidateDigest',clock_timestamp());
  UPDATE public.ai_data_revisions AS d SET revision=d.revision+1,
    updated_at=clock_timestamp() WHERE domain='ai-assistant';
  RETURN jsonb_build_object('schemaVersion',
    'business-market-v6-paused-topology-outcome-v1',
    'status','committed_paused','reportId',report_id,'workflowId',workflow_id,
    'ownerEmail',owner_email,'clientRequestId',client_id,
    'requestDigest',intent_digest,'modelId','',
    'providerCallsAllowed',false,'agentReadPersisted',false,
    'numericCitationAllowed',false,'durablePaidReservation',false);
END $$"""

CANCEL_SQL = """CREATE FUNCTION public.ai_market_v6_cancel_paused_topology(
  request_text text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE request jsonb; selected public.protected_business_market_v6_topologies%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  locked_revision public.ai_data_revisions%ROWTYPE;
  job_count integer; node_count integer; selected_digest text;
BEGIN
  IF session_user<>'teruisi_ai_market_v6_topology_login'
     OR current_database() NOT IN ('teruisi_ai_rehearsal',
       'test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR inet_server_addr()::text NOT IN
       ('127.0.0.1','127.0.0.1/32','::1','::1/128')
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
       r.rolname='teruisi_ai_market_v6_topology_login' AND r.rolcanlogin
       AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
       AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE
       m.roleid='teruisi_ai_market_v6_topology_login'::regrole OR
       m.member='teruisi_ai_market_v6_topology_login'::regrole)
     OR request_text IS NULL OR octet_length(request_text) NOT BETWEEN 1 AND 4096
  THEN RAISE EXCEPTION 'ai_market_v6_cancel_unavailable'; END IF;
  request:=request_text::jsonb;
  IF request_text IS DISTINCT FROM public.ai_v4_replay_canonical(request)
     OR request->>'schemaVersion'<>'business-market-v6-paused-topology-cancel-intent-v1'
     OR request->'explicitCancel' IS DISTINCT FROM 'true'::jsonb
     OR request->>'reportId' !~ '^market-v6-report-[0-9a-f]{48}$'
     OR request->>'ownerEmail' IS NULL
     OR request->>'expectedVersion' !~ '^[1-9][0-9]*$'
     OR request->>'reasonDigest' !~ '^[0-9a-f]{64}$'
     OR request-ARRAY['schemaVersion','explicitCancel','reportId','ownerEmail',
       'expectedVersion','reasonDigest']<>'{}'::jsonb
  THEN RAISE EXCEPTION 'ai_market_v6_cancel_shape'; END IF;
  selected_digest:=encode(sha256(convert_to(request_text,'UTF8')),'hex');
  SELECT * INTO locked_revision FROM public.ai_data_revisions
    WHERE domain='ai-assistant' FOR UPDATE;
  IF locked_revision.domain IS NULL THEN
    RAISE EXCEPTION 'ai_market_v6_revision_missing'; END IF;
  SELECT * INTO selected FROM public.protected_business_market_v6_topologies
    WHERE report_id=request->>'reportId' FOR SHARE;
  SELECT * INTO flow FROM public.ai_workflow_runs
    WHERE id=selected.workflow_id FOR UPDATE;
  SELECT * INTO report FROM public.ai_report_runs
    WHERE id=selected.report_id FOR SHARE;
  PERFORM 1 FROM public.access_control_users actor
    WHERE actor.email=selected.owner_email AND actor.status='active'
      AND actor.role='admin' AND actor.scope IS NULL
      AND actor.version=selected.owner_version FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_market_v6_cancel_actor_drift'; END IF;
  IF selected.report_id IS NULL OR flow.id IS NULL OR report.id IS NULL
     OR selected.owner_email IS DISTINCT FROM request->>'ownerEmail'
     OR flow.owner_email IS DISTINCT FROM selected.owner_email
     OR report.owner_email IS DISTINCT FROM selected.owner_email
     OR report.workflow_id IS DISTINCT FROM flow.id
     OR report.snapshot_json IS DISTINCT FROM selected.snapshot_json
     OR flow.input_json IS DISTINCT FROM selected.snapshot_json
     OR flow.graph_json IS DISTINCT FROM selected.graph_json
     OR selected.snapshot_digest IS DISTINCT FROM encode(sha256(convert_to(
       selected.snapshot_json,'UTF8')),'hex')
     OR flow.model_id<>'' OR flow.allowed_tools_json<>'[]'
     OR flow.tool_policy_digest<>'' OR flow.provider_round_count<>0
     OR flow.tool_call_count<>0
     OR flow.status<>'paused' OR flow.version::text IS DISTINCT FROM
       request->>'expectedVersion'
     OR EXISTS(SELECT 1 FROM public.protected_business_market_v6_topology_cancellations
       WHERE report_id=selected.report_id)
  THEN RAISE EXCEPTION 'ai_market_v6_cancel_conflict'; END IF;
  SELECT count(*) INTO job_count FROM public.ai_agent_jobs
    WHERE workflow_run_id=flow.id AND status='paused' AND phase='paused'
      AND model_id='' AND allowed_tools_json='[]'
      AND tool_policy_digest='' AND provider_round_count=0 AND tool_call_count=0;
  SELECT count(*) INTO node_count FROM public.ai_workflow_node_runs
    WHERE run_id=flow.id AND status='pending';
  IF job_count<>5 OR node_count<>6
     OR EXISTS(SELECT 1 FROM public.ai_agent_provider_dispatches p JOIN
       public.ai_agent_jobs j ON j.id=p.job_id WHERE j.workflow_run_id=flow.id)
     OR EXISTS(SELECT 1 FROM public.ai_agent_tool_dispatches t JOIN
       public.ai_agent_jobs j ON j.id=t.job_id WHERE j.workflow_run_id=flow.id)
     OR EXISTS(SELECT 1 FROM public.ai_business_market_v2_read_receipts r JOIN
       public.ai_agent_jobs j ON j.id=r.job_id WHERE j.workflow_run_id=flow.id)
     OR EXISTS(SELECT 1 FROM public.ai_agent_checkpoints c JOIN
       public.ai_agent_jobs j ON j.id=c.job_id WHERE j.workflow_run_id=flow.id)
     OR EXISTS(SELECT 1 FROM public.ai_agent_events e JOIN
       public.ai_agent_jobs j ON j.id=e.job_id WHERE j.workflow_run_id=flow.id)
     OR EXISTS(SELECT 1 FROM public.ai_workflow_events e WHERE e.run_id=flow.id)
     OR EXISTS(SELECT 1 FROM public.ai_report_deliveries d WHERE
       d.report_id=selected.report_id)
     OR EXISTS(SELECT 1 FROM public.ai_business_file_runs f WHERE
       f.report_id=selected.report_id)
  THEN RAISE EXCEPTION 'ai_market_v6_cancel_effect_unknown'; END IF;
  INSERT INTO public.protected_business_market_v6_topology_cancellations(
    report_id,request_digest,reason_digest,expected_version,cancelled_at)
  VALUES(selected.report_id,selected_digest,request->>'reasonDigest',
    flow.version,clock_timestamp());
  UPDATE public.ai_workflow_node_runs SET status='cancelled',version=version+1,
    updated_at=clock_timestamp() WHERE run_id=flow.id AND status='pending';
  UPDATE public.ai_agent_jobs SET status='cancelled',phase='cancelled',
    cancel_requested=1,version=version+1,error_code='market_v6_cancelled',
    completed_at=clock_timestamp(),updated_at=clock_timestamp()
    WHERE workflow_run_id=flow.id AND status='paused';
  UPDATE public.ai_workflow_runs SET status='cancelled',cancel_requested=1,
    version=version+1,error_code='market_v6_cancelled',
    completed_at=clock_timestamp(),updated_at=clock_timestamp()
    WHERE id=flow.id AND status='paused';
  UPDATE public.ai_data_revisions AS d SET revision=d.revision+1,
    updated_at=clock_timestamp() WHERE domain='ai-assistant';
  RETURN jsonb_build_object('schemaVersion',
    'business-market-v6-paused-topology-outcome-v1',
    'status','cancelled','reportId',selected.report_id,
    'workflowId',selected.workflow_id,'ownerEmail',selected.owner_email,
    'clientRequestId',selected.client_request_id,
    'requestDigest',selected.request_digest,'modelId','',
    'providerCallsAllowed',false,'agentReadPersisted',false,
    'numericCitationAllowed',false,'durablePaidReservation',false);
END $$"""

OUTCOME_SQL = """CREATE FUNCTION public.ai_market_v6_paused_topology_outcome(
  selected_owner text,selected_source text,selected_client text,
  selected_request_digest text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE calc_report_id text; calc_workflow_id text;
  selected public.protected_business_market_v6_topologies%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE; status_text text;
  expected_job_status text; expected_node_status text;
BEGIN
  IF session_user<>'teruisi_ai_market_v6_topology_login'
     OR current_database() NOT IN ('teruisi_ai_rehearsal',
       'test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR inet_server_addr()::text NOT IN
       ('127.0.0.1','127.0.0.1/32','::1','::1/128')
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
       r.rolname='teruisi_ai_market_v6_topology_login' AND r.rolcanlogin
       AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
       AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE
       m.roleid='teruisi_ai_market_v6_topology_login'::regrole OR
       m.member='teruisi_ai_market_v6_topology_login'::regrole)
     OR selected_owner IS NULL OR selected_owner<>lower(selected_owner)
     OR selected_source IS NULL OR selected_source !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_client IS NULL OR selected_client !~ '^[A-Za-z0-9._:-]{8,128}$'
     OR selected_request_digest IS NULL OR selected_request_digest !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_market_v6_outcome_unavailable'; END IF;
  calc_report_id:='market-v6-report-'||substr(encode(sha256(convert_to(
    public.ai_v4_replay_canonical(jsonb_build_array('market-v6-report',
      selected_owner,selected_source,selected_client)),'UTF8')),'hex'),1,48);
  calc_workflow_id:='market-v6-flow-'||substr(encode(sha256(convert_to(
    public.ai_v4_replay_canonical(jsonb_build_array('market-v6-flow',
      selected_owner,selected_source,selected_client)),'UTF8')),'hex'),1,48);
  SELECT * INTO selected FROM public.protected_business_market_v6_topologies t
    WHERE t.report_id=calc_report_id;
  IF selected.report_id IS NULL THEN
    status_text:=CASE WHEN EXISTS(SELECT 1 FROM public.ai_report_runs r
        WHERE r.id=calc_report_id) OR EXISTS(SELECT 1 FROM
        public.ai_workflow_runs w WHERE w.id=calc_workflow_id)
      THEN 'conflict' ELSE 'absent_observed' END;
  ELSIF selected.owner_email IS DISTINCT FROM selected_owner OR
      selected.source_report_id IS DISTINCT FROM selected_source OR
      selected.client_request_id IS DISTINCT FROM selected_client OR
      selected.workflow_id IS DISTINCT FROM calc_workflow_id OR
      selected.request_digest IS DISTINCT FROM selected_request_digest THEN
    status_text:='conflict';
  ELSE
    SELECT * INTO flow FROM public.ai_workflow_runs w
      WHERE w.id=selected.workflow_id;
    SELECT * INTO report FROM public.ai_report_runs r
      WHERE r.id=selected.report_id;
    status_text:=CASE WHEN EXISTS(SELECT 1 FROM
      public.protected_business_market_v6_topology_cancellations c
      WHERE c.report_id=selected.report_id)
      THEN 'cancelled' ELSE 'committed_paused' END;
    expected_job_status:=CASE WHEN status_text='cancelled'
      THEN 'cancelled' ELSE 'paused' END;
    expected_node_status:=CASE WHEN status_text='cancelled'
      THEN 'cancelled' ELSE 'pending' END;
    IF flow.id IS NULL OR report.id IS NULL OR
       report.workflow_id IS DISTINCT FROM flow.id OR
       report.owner_email IS DISTINCT FROM selected_owner OR
       report.snapshot_json IS DISTINCT FROM selected.snapshot_json OR
       flow.input_json IS DISTINCT FROM selected.snapshot_json OR
       flow.graph_json IS DISTINCT FROM selected.graph_json OR
       selected.snapshot_digest IS DISTINCT FROM encode(sha256(convert_to(
         selected.snapshot_json,'UTF8')),'hex') OR
       flow.status IS DISTINCT FROM expected_job_status
       OR flow.owner_email IS DISTINCT FROM selected_owner
       OR flow.model_id IS DISTINCT FROM ''
       OR flow.allowed_tools_json IS DISTINCT FROM '[]'
       OR flow.tool_policy_digest IS DISTINCT FROM ''
       OR flow.provider_round_count<>0 OR flow.tool_call_count<>0
       OR (SELECT count(*) FROM public.ai_agent_jobs j WHERE
          j.workflow_run_id=flow.id AND j.status=expected_job_status AND
          j.model_id='' AND j.allowed_tools_json='[]' AND
          j.tool_policy_digest='' AND j.provider_round_count=0 AND
          j.tool_call_count=0)<>5
       OR (SELECT count(*) FROM public.ai_workflow_node_runs n WHERE
          n.run_id=flow.id AND n.status=expected_node_status)<>6
       OR EXISTS(SELECT 1 FROM public.ai_agent_provider_dispatches p JOIN
          public.ai_agent_jobs j ON j.id=p.job_id WHERE j.workflow_run_id=flow.id)
       OR EXISTS(SELECT 1 FROM public.ai_agent_tool_dispatches t JOIN
          public.ai_agent_jobs j ON j.id=t.job_id WHERE j.workflow_run_id=flow.id)
       OR EXISTS(SELECT 1 FROM public.ai_business_market_v2_read_receipts r JOIN
          public.ai_agent_jobs j ON j.id=r.job_id WHERE j.workflow_run_id=flow.id)
       OR EXISTS(SELECT 1 FROM public.ai_agent_checkpoints c JOIN
          public.ai_agent_jobs j ON j.id=c.job_id WHERE j.workflow_run_id=flow.id)
       OR EXISTS(SELECT 1 FROM public.ai_agent_events e JOIN
          public.ai_agent_jobs j ON j.id=e.job_id WHERE j.workflow_run_id=flow.id)
       OR EXISTS(SELECT 1 FROM public.ai_workflow_events e WHERE e.run_id=flow.id)
       OR EXISTS(SELECT 1 FROM public.ai_report_deliveries d WHERE
          d.report_id=selected.report_id)
       OR EXISTS(SELECT 1 FROM public.ai_business_file_runs f WHERE
          f.report_id=selected.report_id)
    THEN status_text:='conflict'; END IF;
  END IF;
  RETURN jsonb_build_object('schemaVersion',
    'business-market-v6-paused-topology-outcome-v1',
    'status',status_text,'reportId',calc_report_id,'workflowId',calc_workflow_id,
    'ownerEmail',selected_owner,'clientRequestId',selected_client,
    'requestDigest',selected_request_digest,'modelId','',
    'providerCallsAllowed',false,'agentReadPersisted',false,
    'numericCitationAllowed',false,'durablePaidReservation',false);
END $$"""
