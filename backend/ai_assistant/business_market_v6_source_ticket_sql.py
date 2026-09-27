"""0079 test-only, default-closed market source pointer for persisted 0077 jobs.

The only positive source is the selected sealed market TOP sample. Issuance
does not execute a tool, read page bytes for an Agent, select a model, or spend.
"""

ROLE = "teruisi_ai_market_v6_source_login"
TABLE = "public.protected_business_market_v6_source_tickets"
EXPECTED = "public.ai_market_v6_source_ticket_expected(text,text,bigint,text,integer)"
ISSUE = "public.ai_market_v6_issue_source_ticket(text,text,bigint,text,integer)"
OUTCOME = "public.ai_market_v6_source_ticket_outcome(text,text,text,bigint)"
GUARD = "public.ai_market_v6_source_ticket_guard()"
SIGNATURES = (GUARD, EXPECTED, ISSUE, OUTCOME)

CREATE_TABLE = """CREATE TABLE public.protected_business_market_v6_source_tickets (
  ticket_id varchar(160) PRIMARY KEY,
  report_id varchar(160) NOT NULL REFERENCES
    public.protected_business_market_v6_topologies(report_id) ON DELETE RESTRICT,
  owner_email varchar(320) NOT NULL,
  owner_version bigint NOT NULL CHECK (owner_version>=1),
  job_id varchar(160) NOT NULL REFERENCES public.ai_agent_jobs(id) ON DELETE RESTRICT,
  view_name varchar(32) NOT NULL CHECK (view_name='rank_entry_exit'),
  page_index integer NOT NULL CHECK (page_index>=0 AND page_index<20000),
  ticket_json text NOT NULL CHECK (octet_length(ticket_json)<=8192),
  ticket_digest varchar(64) NOT NULL CHECK
    (ticket_digest ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL,
  CONSTRAINT ai_market_v6_source_page_uq UNIQUE(report_id,job_id,view_name,page_index)
)"""

GUARD_SQL = """CREATE FUNCTION public.ai_market_v6_source_ticket_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' OR session_user<>'teruisi_ai_market_v6_source_login'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_market_v6_source_ticket_immutable'; END IF;
  RETURN NEW;
END $$"""

_CLOSED = """session_user<>'teruisi_ai_market_v6_source_login'
     OR current_database() NOT IN ('teruisi_ai_rehearsal',
       'test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR inet_server_addr()::text NOT IN
       ('127.0.0.1','127.0.0.1/32','::1','::1/128')
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
        oid='public.protected_business_market_v6_source_tickets'::regclass))
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
       r.rolname='teruisi_ai_market_v6_source_login' AND r.rolcanlogin
       AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
       AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE
       m.roleid='teruisi_ai_market_v6_source_login'::regrole OR
       m.member='teruisi_ai_market_v6_source_login'::regrole)"""

EXPECTED_SQL = """CREATE FUNCTION public.ai_market_v6_source_ticket_expected(
  selected_report text,selected_owner text,selected_version bigint,
  selected_view text,selected_page integer)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE top public.protected_business_market_v6_topologies%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  job public.ai_agent_jobs%ROWTYPE;
  plan public.ai_business_market_v2_execution_plans%ROWTYPE;
  source_execution public.ai_report_runs%ROWTYPE;
  admitted public.ai_report_runs%ROWTYPE;
  parked public.ai_report_runs%ROWTYPE;
  sealed_report public.ai_report_runs%ROWTYPE;
  sealed_flow public.ai_workflow_runs%ROWTYPE;
  evidence public.ai_business_evidence_runs%ROWTYPE;
  material public.ai_business_market_v2_materials%ROWTYPE;
  root jsonb; manifest jsonb; summary jsonb; spec jsonb;
  sealed_root jsonb; result jsonb; ticket_id text;
BEGIN
  IF """ + _CLOSED + """
     OR selected_report IS NULL OR selected_report !~ '^market-v6-report-[0-9a-f]{48}$'
     OR selected_owner IS NULL OR selected_owner<>lower(selected_owner)
     OR selected_version IS NULL OR selected_version<1
     OR selected_view IS DISTINCT FROM 'rank_entry_exit'
     OR selected_page IS NULL OR selected_page<0 OR selected_page>=20000
  THEN RAISE EXCEPTION 'ai_market_v6_source_ticket_unavailable'; END IF;
  SELECT * INTO top FROM public.protected_business_market_v6_topologies t
    WHERE t.report_id=selected_report FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users a
    WHERE a.email=selected_owner FOR SHARE;
  SELECT * INTO report FROM public.ai_report_runs r
    WHERE r.id=selected_report FOR SHARE;
  SELECT * INTO flow FROM public.ai_workflow_runs f
    WHERE f.id=top.workflow_id FOR SHARE;
  SELECT * INTO job FROM public.ai_agent_jobs j
    WHERE j.id=top.snapshot_json::jsonb->'jobs'->2->>'jobId' FOR SHARE;
  SELECT * INTO plan FROM public.ai_business_market_v2_execution_plans p
    WHERE p.id=top.source_plan_id FOR SHARE;
  SELECT * INTO source_execution FROM public.ai_report_runs r
    WHERE r.id=top.source_report_id FOR SHARE;
  root:=plan.plan_json::jsonb->'executionRoot';
  SELECT * INTO admitted FROM public.ai_report_runs r
    WHERE r.id=root->>'admittedReportId' FOR SHARE;
  SELECT * INTO parked FROM public.ai_report_runs r
    WHERE r.id=root->>'parkedReportId' FOR SHARE;
  SELECT * INTO material FROM public.ai_business_market_v2_materials m
    WHERE m.report_id=parked.id FOR SHARE;
  SELECT * INTO sealed_report FROM public.ai_report_runs r
    WHERE r.id=root->>'sourceReportId' FOR SHARE;
  SELECT * INTO sealed_flow FROM public.ai_workflow_runs f
    WHERE f.id=sealed_report.workflow_id FOR SHARE;
  sealed_root:=parked.snapshot_json::jsonb->'sourceRoot';
  SELECT * INTO evidence FROM public.ai_business_evidence_runs e
    WHERE e.id=sealed_root->>'evidenceRunId' FOR SHARE;
  manifest:=material.manifest_json::jsonb;
  summary:=material.summary_json::jsonb;
  SELECT item.spec INTO spec FROM jsonb_array_elements(manifest->'tables')
    AS item(spec) WHERE item.spec->>'view'=selected_view;
  IF top.report_id IS NULL OR actor.email IS NULL OR report.id IS NULL
     OR flow.id IS NULL OR job.id IS NULL OR plan.id IS NULL
     OR source_execution.id IS NULL OR admitted.id IS NULL
     OR parked.id IS NULL OR material.report_id IS NULL
     OR sealed_report.id IS NULL OR sealed_flow.id IS NULL
     OR evidence.id IS NULL OR spec IS NULL
     OR actor.role<>'admin' OR actor.status<>'active' OR actor.scope IS NOT NULL
     OR actor.version IS DISTINCT FROM selected_version
     OR top.owner_email IS DISTINCT FROM actor.email
     OR top.owner_version IS DISTINCT FROM actor.version
     OR top.workflow_id IS DISTINCT FROM flow.id
     OR top.snapshot_digest IS DISTINCT FROM encode(sha256(convert_to(
       top.snapshot_json,'UTF8')),'hex')
     OR top.snapshot_json::jsonb->>'reportId' IS DISTINCT FROM top.report_id
     OR top.snapshot_json::jsonb->>'workflowId' IS DISTINCT FROM top.workflow_id
     OR top.snapshot_json::jsonb->>'ownerEmail' IS DISTINCT FROM actor.email
     OR top.snapshot_json::jsonb->>'ownerVersion' IS DISTINCT FROM actor.version::text
     OR top.snapshot_json::jsonb->>'manifestDigest' IS DISTINCT FROM material.manifest_digest
     OR top.snapshot_json::jsonb->>'selectorDigest' IS DISTINCT FROM material.selector_digest
     OR top.snapshot_json::jsonb->'providerCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR top.snapshot_json::jsonb->'agentReadPersisted' IS DISTINCT FROM 'false'::jsonb
     OR top.snapshot_json::jsonb->'reportPublishAuthorized' IS DISTINCT FROM 'false'::jsonb
     OR EXISTS(SELECT 1 FROM
       public.protected_business_market_v6_topology_cancellations c
       WHERE c.report_id=top.report_id)
     OR report.owner_email IS DISTINCT FROM actor.email
     OR report.scope_json IS DISTINCT FROM 'null'
     OR report.workflow_id IS DISTINCT FROM flow.id
     OR report.snapshot_json IS DISTINCT FROM top.snapshot_json
     OR flow.owner_email IS DISTINCT FROM actor.email
     OR flow.scope_json IS DISTINCT FROM 'null'
     OR flow.input_json IS DISTINCT FROM top.snapshot_json
     OR flow.status IS DISTINCT FROM 'paused'
     OR flow.cancel_requested<>0
     OR flow.model_id IS DISTINCT FROM '' OR flow.provider_round_count<>0
     OR flow.tool_call_count<>0 OR flow.allowed_tools_json IS DISTINCT FROM '[]'
     OR job.workflow_run_id IS DISTINCT FROM flow.id
     OR job.owner_email IS DISTINCT FROM actor.email
     OR job.scope_json IS DISTINCT FROM 'null'
     OR job.input_json IS DISTINCT FROM top.snapshot_json
     OR job.status IS DISTINCT FROM 'paused'
     OR job.phase IS DISTINCT FROM 'paused' OR job.cancel_requested<>0
     OR job.provider_dispatch_started_at IS NOT NULL
     OR job.workflow_node_key IS DISTINCT FROM 'market_b2b'
     OR job.model_id IS DISTINCT FROM '' OR job.provider_round_count<>0
     OR job.tool_call_count<>0 OR job.allowed_tools_json IS DISTINCT FROM '[]'
     OR (SELECT count(*) FROM public.ai_agent_jobs j
          WHERE j.workflow_run_id=flow.id) IS DISTINCT FROM 5
     OR (SELECT count(*) FROM public.ai_agent_jobs j
          WHERE j.workflow_run_id=flow.id AND j.status='paused'
            AND j.phase='paused' AND j.model_id=''
            AND j.provider_round_count=0 AND j.tool_call_count=0)
          IS DISTINCT FROM 5
     OR (SELECT count(*) FROM public.ai_workflow_node_runs n
          WHERE n.run_id=flow.id AND n.status='pending') IS DISTINCT FROM 6
     OR plan.execution_report_id IS DISTINCT FROM source_execution.id
     OR plan.plan_digest IS DISTINCT FROM top.source_plan_digest
     OR plan.plan_digest IS DISTINCT FROM encode(sha256(convert_to(
       plan.plan_json,'UTF8')),'hex')
     OR source_execution.id IS DISTINCT FROM top.source_report_id
     OR source_execution.owner_email IS DISTINCT FROM actor.email
     OR source_execution.scope_json IS DISTINCT FROM 'null'
     OR source_execution.snapshot_json::jsonb->>'executionProfile'
          IS DISTINCT FROM 'business-agent-screening-promotion-market-execution-v2'
     OR source_execution.snapshot_json::jsonb->'executionRoot' IS DISTINCT FROM
       root-ARRAY['executionReportId','contextProofDigest',
         'executionSnapshotDigest']
     OR root->>'executionReportId' IS DISTINCT FROM source_execution.id
     OR root->>'executionSnapshotDigest' IS DISTINCT FROM
       encode(sha256(convert_to(source_execution.snapshot_json,'UTF8')),'hex')
     OR root->>'ownerEmail' IS DISTINCT FROM actor.email
     OR root->>'selectorDigest' IS DISTINCT FROM material.selector_digest
     OR root->>'manifestDigest' IS DISTINCT FROM material.manifest_digest
     OR admitted.owner_email IS DISTINCT FROM actor.email
     OR admitted.scope_json IS DISTINCT FROM 'null'
     OR admitted.snapshot_json::jsonb->>'executionProfile'
          IS DISTINCT FROM 'business-agent-screening-promotion-market-admitted-v2'
     OR admitted.snapshot_json::jsonb->'marketAdmission'->>'parkedReportId'
          IS DISTINCT FROM parked.id
     OR admitted.snapshot_json::jsonb->'marketAdmission'->>'selectorDigest'
          IS DISTINCT FROM material.selector_digest
     OR admitted.snapshot_json::jsonb->'marketAdmission'->>'manifestDigest'
          IS DISTINCT FROM material.manifest_digest
     OR parked.owner_email IS DISTINCT FROM actor.email
     OR parked.scope_json IS DISTINCT FROM 'null'
     OR parked.snapshot_json::jsonb->>'executionProfile'
          IS DISTINCT FROM 'business-agent-screening-promotion-market-reference-v2'
     OR material.source_report_id IS DISTINCT FROM sealed_report.id
     OR sealed_root->>'sourceReportId' IS DISTINCT FROM sealed_report.id
     OR sealed_report.owner_email IS DISTINCT FROM actor.email
     OR sealed_report.scope_json IS DISTINCT FROM 'null'
     OR material.source_snapshot_digest IS DISTINCT FROM encode(sha256(convert_to(
       sealed_report.snapshot_json,'UTF8')),'hex')
     OR material.source_workflow_input_digest IS DISTINCT FROM encode(sha256(convert_to(
       sealed_flow.input_json,'UTF8')),'hex')
     OR material.selector_digest IS DISTINCT FROM encode(sha256(convert_to(
       (parked.snapshot_json::jsonb->'marketSelector')::text,'UTF8')),'hex')
     OR material.manifest_json_sha256 IS DISTINCT FROM encode(sha256(convert_to(
       material.manifest_json,'UTF8')),'hex')
     OR material.manifest_digest IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(manifest-'manifestDigest'),'UTF8')),'hex')
     OR material.summary_digest IS DISTINCT FROM encode(sha256(convert_to(
       material.summary_json,'UTF8')),'hex')
     OR manifest->>'schemaVersion' IS DISTINCT FROM
       'business-market-composite-materials-v2'
     OR manifest->'reportBinding'->>'reportId' IS DISTINCT FROM sealed_report.id
     OR manifest->>'manifestDigest' IS DISTINCT FROM material.manifest_digest
     OR jsonb_array_length(manifest->'tables') IS DISTINCT FROM 3
     OR manifest->'tables'->0->>'view' IS DISTINCT FROM 'price_band_summary'
     OR manifest->'tables'->1->>'view' IS DISTINCT FROM 'price_band_members'
     OR manifest->'tables'->2->>'view' IS DISTINCT FROM 'rank_entry_exit'
     OR manifest->>'rankCurrentSourceKey' IS DISTINCT FROM
       parked.snapshot_json::jsonb->'marketSelector'->>'rankCurrentSourceKey'
     OR manifest->>'rankBaselineKey' IS DISTINCT FROM
       parked.snapshot_json::jsonb->'marketSelector'->>'rankBaselineKey'
     OR manifest->>'rankCurrentSourceKey' IS NULL
     OR manifest->>'rankCurrentSourceKey' !~ '^[A-Za-z0-9_-]{1,160}$'
     OR manifest->>'rankBaselineKey' IS NULL
     OR manifest->>'rankBaselineKey' !~ '^[A-Za-z0-9_-]{1,160}$'
     OR manifest->>'rankCurrentSourceKey' IS NOT DISTINCT FROM
        manifest->>'rankBaselineKey'
     OR summary->>'marketManifestDigest' IS DISTINCT FROM material.manifest_digest
     OR summary->>'admissionDigest' IS NULL
     OR summary->>'admissionDigest' !~ '^[0-9a-f]{64}$'
     OR root->>'marketContextDigest' IS NULL
     OR root->>'marketContextDigest' !~ '^[0-9a-f]{64}$'
     OR manifest->'rankObservationCoverage' IS DISTINCT FROM
       '{"currentDatePresent":true,"baselineDatePresent":true,"bothDatesPresent":true}'::jsonb
     OR manifest->'authorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR evidence.owner_email IS DISTINCT FROM actor.email
     OR evidence.status IS DISTINCT FROM 'sealed'
     OR evidence.version::text IS DISTINCT FROM sealed_root->>'evidenceVersion'
     OR evidence.state_json::jsonb->>'sealedDigest' IS DISTINCT FROM
       sealed_root->>'sealedDigest'
     OR spec->>'sourceTableDigest' IS NULL
     OR spec->>'sourceTableDigest' !~ '^[0-9a-f]{64}$'
     OR spec->>'ndjsonSha256' IS NULL
     OR spec->>'ndjsonSha256' !~ '^[0-9a-f]{64}$'
     OR spec->>'pageCount' IS NULL
     OR spec->>'pageCount' !~ '^[0-9]{1,5}$'
     OR spec->>'rowCount' IS NULL
     OR spec->>'rowCount' !~ '^[0-9]{1,6}$'
     OR (spec->>'pageCount')::integer NOT BETWEEN 1 AND 20000
     OR (spec->>'rowCount')::integer NOT BETWEEN 0 AND 200000
     OR selected_page >= (spec->>'pageCount')::integer
     OR manifest->'observationDates'->>'current' IS NULL
     OR manifest->'observationDates'->>'current' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
     OR manifest->'observationDates'->>'baseline' IS NULL
     OR manifest->'observationDates'->>'baseline' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
     OR manifest->'observationDates'->>'current' IS NOT DISTINCT FROM
        manifest->'observationDates'->>'baseline'
     OR parked.snapshot_json::jsonb->'marketSelector'->>'currentObservationDate'
          IS DISTINCT FROM manifest->'observationDates'->>'current'
     OR parked.snapshot_json::jsonb->'marketSelector'->>'baselineObservationDate'
          IS DISTINCT FROM manifest->'observationDates'->>'baseline'
  THEN RAISE EXCEPTION 'ai_market_v6_source_ticket_root_drift'; END IF;
  ticket_id:='market-v6-page-'||substr(encode(sha256(convert_to(
    public.ai_v4_replay_canonical(jsonb_build_array(top.report_id,job.id,
      actor.version,top.snapshot_digest,material.manifest_digest,
      selected_view,selected_page)),'UTF8')),'hex'),1,48);
  result:=jsonb_build_object(
    'schemaVersion','business-market-v6-source-page-ticket-v1',
    'ticketId',ticket_id,'reportId',top.report_id,
    'workflowId',flow.id,'jobId',job.id,'role','market_b2b',
    'ownerEmail',actor.email,
    'ownerVersion',actor.version,
    'sourceExecutionReportId',source_execution.id,
    'admittedReportId',admitted.id,'parkedReportId',parked.id,
    'sealedSourceReportId',sealed_report.id,
    'evidenceRunId',evidence.id,'evidenceVersion',evidence.version,
    'sealedDigest',sealed_root->>'sealedDigest',
    'topologySnapshotDigest',top.snapshot_digest,
    'sourcePlanDigest',plan.plan_digest,
    'selectorDigest',material.selector_digest,
    'admissionDigest',summary->>'admissionDigest',
    'marketContextDigest',root->>'marketContextDigest',
    'marketManifestDigest',material.manifest_digest,
    'view',selected_view,'pageIndex',selected_page,'maxPageBytes',38000,
    'rowCount',(spec->>'rowCount')::bigint,
    'pageCount',(spec->>'pageCount')::integer,
    'tableBindingDigest',spec->>'sourceTableDigest',
    'ndjsonSha256',spec->>'ndjsonSha256',
    'currentSourceKey',manifest->>'rankCurrentSourceKey',
    'baselineSourceKey',manifest->>'rankBaselineKey',
    'currentObservationDate',manifest->'observationDates'->>'current',
    'baselineObservationDate',manifest->'observationDates'->>'baseline',
    'marketStatus','bound_selected_top_sample',
    'shopStatus','unknown_not_supplied',
    'shopSalesStatus','unknown_not_supplied',
    'b2bStatus','unknown_not_supplied',
    'yearAgoStatus','unknown_not_supplied',
    'providerCallsAllowed',false,'toolDispatchAllowed',false,
    'agentReadPersisted',false,'numericCitationAllowed',false,
    'reportPublishAuthorized',false,'pageBytesVerified',false);
  RETURN result||jsonb_build_object('ticketDigest',encode(sha256(convert_to(
    public.ai_v4_replay_canonical(result),'UTF8')),'hex'));
END $$"""

ISSUE_SQL = """CREATE FUNCTION public.ai_market_v6_issue_source_ticket(
  selected_report text,selected_owner text,selected_version bigint,
  selected_view text,selected_page integer)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; existing public.protected_business_market_v6_source_tickets%ROWTYPE;
  total integer;
BEGIN
  IF """ + _CLOSED + """ THEN
    RAISE EXCEPTION 'ai_market_v6_source_ticket_unavailable'; END IF;
  PERFORM 1 FROM public.protected_business_market_v6_topologies t
    WHERE t.report_id=selected_report FOR UPDATE;
  value:=public.ai_market_v6_source_ticket_expected(selected_report,
    selected_owner,selected_version,selected_view,selected_page);
  SELECT * INTO existing FROM public.protected_business_market_v6_source_tickets t
    WHERE t.ticket_id=value->>'ticketId';
  IF existing.ticket_id IS NOT NULL THEN
    IF existing.ticket_json IS DISTINCT FROM public.ai_v4_replay_canonical(value)
       OR existing.ticket_digest IS DISTINCT FROM value->>'ticketDigest'
    THEN RAISE EXCEPTION 'ai_market_v6_source_ticket_replay_conflict'; END IF;
    RETURN value;
  END IF;
  SELECT count(*) INTO total FROM public.protected_business_market_v6_source_tickets t
    WHERE t.report_id=selected_report;
  IF total>=64 THEN RAISE EXCEPTION 'ai_market_v6_source_ticket_quota'; END IF;
  INSERT INTO public.protected_business_market_v6_source_tickets(
    ticket_id,report_id,owner_email,owner_version,job_id,view_name,
    page_index,ticket_json,ticket_digest,created_at)
  VALUES(value->>'ticketId',selected_report,selected_owner,selected_version,
    value->>'jobId',selected_view,selected_page,
    public.ai_v4_replay_canonical(value),value->>'ticketDigest',clock_timestamp());
  RETURN value;
END $$"""

OUTCOME_SQL = """CREATE FUNCTION public.ai_market_v6_source_ticket_outcome(
  selected_ticket text,selected_report text,selected_owner text,
  selected_version bigint)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE saved public.protected_business_market_v6_source_tickets%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  top public.protected_business_market_v6_topologies%ROWTYPE;
  expected jsonb;
BEGIN
  IF """ + _CLOSED + """
     OR selected_ticket IS NULL OR selected_ticket !~ '^market-v6-page-[0-9a-f]{48}$'
  THEN RAISE EXCEPTION 'ai_market_v6_source_ticket_unavailable'; END IF;
  SELECT * INTO actor FROM public.access_control_users a
    WHERE a.email=selected_owner FOR SHARE;
  SELECT * INTO top FROM public.protected_business_market_v6_topologies t
    WHERE t.report_id=selected_report FOR SHARE;
  IF actor.email IS NULL OR actor.role<>'admin' OR actor.status<>'active'
     OR actor.scope IS NOT NULL OR actor.version IS DISTINCT FROM selected_version
     OR top.report_id IS NULL OR top.owner_email IS DISTINCT FROM actor.email
     OR top.owner_version IS DISTINCT FROM actor.version
  THEN RAISE EXCEPTION 'ai_market_v6_source_ticket_owner_conflict'; END IF;
  SELECT * INTO saved FROM public.protected_business_market_v6_source_tickets t
    WHERE t.ticket_id=selected_ticket;
  IF saved.ticket_id IS NULL THEN
    RETURN jsonb_build_object('status','absent_observed',
      'ticketId',selected_ticket,'agentReadPersisted',false); END IF;
  IF saved.report_id IS DISTINCT FROM selected_report
     OR saved.owner_email IS DISTINCT FROM selected_owner
     OR saved.owner_version IS DISTINCT FROM selected_version
  THEN RAISE EXCEPTION 'ai_market_v6_source_ticket_owner_conflict'; END IF;
  expected:=public.ai_market_v6_source_ticket_expected(selected_report,
    selected_owner,selected_version,saved.view_name,saved.page_index);
  IF expected->>'ticketId' IS DISTINCT FROM saved.ticket_id
     OR expected->>'ticketDigest' IS DISTINCT FROM saved.ticket_digest
     OR public.ai_v4_replay_canonical(expected) IS DISTINCT FROM saved.ticket_json
  THEN RAISE EXCEPTION 'ai_market_v6_source_ticket_root_drift'; END IF;
  RETURN jsonb_build_object('status','committed_market_pointer_only',
    'ticket',expected,'agentReadPersisted',false);
END $$"""
