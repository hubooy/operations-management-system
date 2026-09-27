"""0076 candidate-only ticket/claim-bound v11 signing SQL.

This adds no ready branch, download route, credential loader or key row. Old
0068/0070/0073 definitions are imported only for frozen catalog checks.
"""
from __future__ import annotations

from importlib import import_module


SIGN = "teruisi_ai_budget_v11_sign_login"
KEY_OWNER = "teruisi_ai_budget_v11_key_owner"
TABLE = "public.protected_business_budget_v11_signed_receipts_v3"
GUARD = "public.ai_budget_v11_signed_receipt_guard_v3()"
REQUIREMENTS = (
    "public.ai_budget_v11_sign_requirements_v3(uuid,uuid,text,integer,text)")
PAGE_CORE = (
    "public.ai_budget_v11_sign_ledger_page_core_v3(text,text,integer,text)")
LEDGER_ROOT = (
    "public.ai_budget_v11_sign_ledger_root_v3(uuid,uuid,text,integer,text)")
INVENTORY = (
    "public.ai_budget_v11_sign_ledger_inventory_v3(uuid,uuid,text,integer,text)")
READ = ("public.ai_budget_v11_read_agent_ledger_v3("
    "uuid,uuid,text,integer,text,text,text,integer,text)")
PRIVATE_MAC = "public.ai_budget_v11_private_mac_valid_v3(text,text,text)"
RECORD = ("public.ai_budget_v11_record_signed_receipt_v3("
    "uuid,uuid,text,integer,text,text,text)")
OUTCOME = "public.ai_budget_v11_sign_outcome_v3(uuid,uuid,text)"
SIGNATURES = (GUARD, REQUIREMENTS, PAGE_CORE, LEDGER_ROOT, INVENTORY, READ, PRIVATE_MAC,
    RECORD, OUTCOME)
DOMAIN = "teruisi:budget-v11:ticket-bound-signer:v3"
PURPOSE = "teruisi:business-promotion-budget-v11:ticket-bound-sign-once:v3"


CREATE_TABLE = """CREATE TABLE public.protected_business_budget_v11_signed_receipts_v3 (
  id uuid PRIMARY KEY,
  ticket_id uuid NOT NULL UNIQUE REFERENCES
    public.protected_business_budget_v11_proof_tickets(id) ON DELETE RESTRICT,
  claim_id uuid NOT NULL UNIQUE REFERENCES
    public.protected_business_budget_v11_proof_ticket_claims(id) ON DELETE RESTRICT,
  run_id varchar(160) NOT NULL REFERENCES public.ai_business_file_runs(id)
    ON DELETE RESTRICT,
  attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 5),
  report_id varchar(160) NOT NULL REFERENCES public.ai_report_runs(id)
    ON DELETE RESTRICT,
  owner_email varchar(320) NOT NULL,
  run_version bigint NOT NULL CHECK (run_version>=1),
  binding_digest varchar(64) NOT NULL CHECK
    (binding_digest ~ '^[0-9a-f]{64}$'),
  old_attestation_id varchar(64) NOT NULL,
  login_attestation_id varchar(64) NOT NULL,
  attestation_sha256 varchar(64) NOT NULL CHECK
    (attestation_sha256 ~ '^[0-9a-f]{64}$'),
  ledger_root varchar(64) NOT NULL CHECK (ledger_root ~ '^[0-9a-f]{64}$'),
  file_page_root varchar(64) NOT NULL CHECK (file_page_root ~ '^[0-9a-f]{64}$'),
  key_id varchar(64) NOT NULL,
  receipt_sha256 varchar(64) NOT NULL UNIQUE CHECK
    (receipt_sha256 ~ '^[0-9a-f]{64}$'),
  receipt_text text NOT NULL,
  receipt_mac varchar(64) NOT NULL CHECK (receipt_mac ~ '^[0-9a-f]{64}$'),
  recorded_at timestamptz NOT NULL,
  CONSTRAINT ai_budget_v11_signed_v3_one_per_attempt UNIQUE(run_id,attempt)
)"""


GUARD_SQL = r"""CREATE FUNCTION public.ai_budget_v11_signed_receipt_guard_v3()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' OR session_user<>'teruisi_ai_budget_v11_sign_login'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_budget_v11_signed_receipt_immutable'; END IF;
  RETURN NEW;
END $$"""


REQUIREMENTS_SQL = r"""CREATE FUNCTION public.ai_budget_v11_sign_requirements_v3(
  selected_ticket uuid,selected_claim uuid,selected_run text,
  selected_attempt integer,selected_sha text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE ticket public.protected_business_budget_v11_proof_tickets%ROWTYPE;
  claim public.protected_business_budget_v11_proof_ticket_claims%ROWTYPE;
  parent public.ai_business_file_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  budget public.ai_business_budget_plans%ROWTYPE;
  old_proof public.ai_business_promotion_budget_v11_attestations%ROWTYPE;
  login_proof public.protected_business_budget_v11_login_attestations%ROWTYPE;
  assertion jsonb; file_root text;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_sign_login'
     OR selected_ticket IS NULL OR selected_claim IS NULL
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR selected_sha IS NULL OR selected_sha !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
       r.rolname='teruisi_ai_budget_v11_sign_login' AND r.rolcanlogin
       AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
       AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE
       m.roleid='teruisi_ai_budget_v11_sign_login'::regrole OR
       m.member='teruisi_ai_budget_v11_sign_login'::regrole)
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_unavailable'; END IF;
  SELECT * INTO ticket FROM public.protected_business_budget_v11_proof_tickets
    WHERE id=selected_ticket FOR SHARE;
  SELECT * INTO claim FROM public.protected_business_budget_v11_proof_ticket_claims
    WHERE id=selected_claim FOR SHARE;
  SELECT * INTO parent FROM public.ai_business_file_runs
    WHERE id=selected_run FOR SHARE;
  IF ticket.id IS NULL OR claim.id IS NULL OR parent.id IS NULL
     OR ticket.id IS DISTINCT FROM claim.ticket_id
     OR ticket.run_id IS DISTINCT FROM selected_run
     OR claim.run_id IS DISTINCT FROM selected_run
     OR ticket.attempt IS DISTINCT FROM selected_attempt
     OR claim.attempt IS DISTINCT FROM selected_attempt
     OR ticket.attestation_sha256 IS DISTINCT FROM selected_sha
     OR claim.attestation_sha256 IS DISTINCT FROM selected_sha
     OR ticket.expires_at<=clock_timestamp()
     OR claim.claimed_at<ticket.created_at
     OR claim.claimed_at>ticket.expires_at
     OR parent.renderer_version<>11 OR parent.draft
     OR parent.status<>'paused' OR parent.error_code<>'renderer_unpublished'
     OR parent.progress_json::jsonb IS DISTINCT FROM jsonb_build_object(
       'stage','staged_unpublished','attempt',selected_attempt)
     OR parent.attempt IS DISTINCT FROM selected_attempt
     OR parent.version IS DISTINCT FROM ticket.parent_version
     OR parent.report_id IS DISTINCT FROM ticket.report_id
     OR parent.binding_digest IS DISTINCT FROM ticket.binding_digest
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_ticket_drift'; END IF;
  SELECT * INTO old_proof FROM public.ai_business_promotion_budget_v11_attestations
    WHERE run_id=selected_run AND attempt=selected_attempt FOR SHARE;
  SELECT * INTO login_proof FROM public.protected_business_budget_v11_login_attestations
    WHERE run_id=selected_run AND attempt=selected_attempt FOR SHARE;
  SELECT * INTO report FROM public.ai_report_runs WHERE id=parent.report_id FOR SHARE;
  SELECT * INTO flow FROM public.ai_workflow_runs
    WHERE id=report.workflow_id FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users
    WHERE email=parent.owner_email FOR SHARE;
  IF report.budget_plan_id IS NOT NULL THEN
    SELECT * INTO budget FROM public.ai_business_budget_plans
      WHERE id=report.budget_plan_id FOR SHARE;
  END IF;
  IF old_proof.id IS NULL OR login_proof.id IS NULL OR report.id IS NULL
     OR flow.id IS NULL
     OR report.owner_email IS DISTINCT FROM parent.owner_email
     OR flow.owner_email IS DISTINCT FROM parent.owner_email
     OR report.scope_json IS DISTINCT FROM parent.scope_json
     OR flow.scope_json IS DISTINCT FROM parent.scope_json
     OR flow.status<>'completed' OR flow.completed_at IS NULL
     OR old_proof.attestation_sha256 IS DISTINCT FROM selected_sha
     OR login_proof.attestation_sha256 IS DISTINCT FROM selected_sha
     OR old_proof.attestation_json IS DISTINCT FROM login_proof.attestation_json
     OR encode(sha256(convert_to(old_proof.attestation_json,'UTF8')),'hex')
       IS DISTINCT FROM selected_sha
     OR old_proof.report_id IS DISTINCT FROM parent.report_id
     OR login_proof.report_id IS DISTINCT FROM parent.report_id
     OR old_proof.owner_email IS DISTINCT FROM parent.owner_email
     OR login_proof.owner_email IS DISTINCT FROM parent.owner_email
     OR old_proof.binding_digest IS DISTINCT FROM parent.binding_digest
     OR login_proof.binding_digest IS DISTINCT FROM parent.binding_digest
     OR encode(sha256(convert_to(parent.manifest_json,'UTF8')),'hex')
       IS DISTINCT FROM old_proof.compact_json_sha256
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_proof_drift'; END IF;
  assertion:=old_proof.attestation_json::jsonb;
  IF encode(sha256(convert_to(report.snapshot_json,'UTF8')),'hex')
       IS DISTINCT FROM assertion->>'reportSnapshotSha256'
     OR encode(sha256(convert_to(flow.input_json,'UTF8')),'hex')
       IS DISTINCT FROM assertion->>'workflowInputSha256'
     OR actor.email IS NULL OR actor.role<>'admin'
     OR actor.status<>'active' OR actor.scope IS NOT NULL
     OR actor.version::text IS DISTINCT FROM assertion->>'actorVersion'
     OR (report.budget_plan_id IS NOT NULL) IS DISTINCT FROM
       old_proof.budget_present
     OR (report.budget_plan_id IS NOT NULL AND
       report.snapshot_json::jsonb#>>'{budgetRef,planDigest}'
         IS DISTINCT FROM old_proof.budget_plan_digest)
     OR (report.budget_plan_id IS NOT NULL AND
       (budget.id IS NULL OR budget.owner_email IS DISTINCT FROM parent.owner_email
        OR budget.scope_json IS DISTINCT FROM parent.scope_json
        OR budget.plan_digest IS DISTINCT FROM old_proof.budget_plan_digest
        OR budget.binding_digest IS DISTINCT FROM encode(sha256(convert_to(
          budget.binding_json,'UTF8')),'hex')))
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_current_root_drift'; END IF;
  file_root:=encode(sha256(convert_to(public.ai_v4_replay_canonical(
    jsonb_build_object('schemaVersion','budget-v11-file-page-root-v3',
      'files',assertion->'files',
      'fileByteVerificationDigest',assertion->>'fileByteVerificationDigest',
      'htmlRowsDigest',assertion->>'htmlRowsDigest',
      'xlsxOpcFormulaDigest',assertion->>'xlsxOpcFormulaDigest')),'UTF8')),'hex');
  RETURN jsonb_build_object('schemaVersion','budget-v11-sign-requirements-v3',
    'ticketId',ticket.id::text,'claimId',claim.id::text,
    'runId',parent.id,'attempt',parent.attempt,'runVersion',parent.version,
    'workflowVersion',flow.version,
    'reportId',parent.report_id,'ownerEmail',parent.owner_email,
    'bindingDigest',parent.binding_digest,
    'oldAttestationId',old_proof.id,
    'loginAttestationId',login_proof.id,
    'attestationSha256',selected_sha,'attestationText',old_proof.attestation_json,
    'filePageRoot',file_root,'workflowId',report.workflow_id,
    'readyAuthorized',false);
END $$"""


PAGE_CORE_SQL = r"""CREATE FUNCTION public.ai_budget_v11_sign_ledger_page_core_v3(
  selected_job text,selected_kind text,selected_ordinal integer,
  selected_owner text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE job public.ai_agent_jobs%ROWTYPE;
  p public.ai_agent_provider_dispatches%ROWTYPE;
  pr public.ai_agent_provider_results%ROWTYPE;
  t public.ai_agent_tool_dispatches%ROWTYPE;
  tr public.ai_agent_tool_results%ROWTYPE;
  calls jsonb;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_sign_login'
     OR selected_job IS NULL OR selected_job !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_kind NOT IN ('provider','tool')
     OR selected_ordinal NOT BETWEEN 1 AND 40
     OR selected_owner IS NULL OR octet_length(selected_owner)>320
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_page_core_shape'; END IF;
  SELECT * INTO job FROM public.ai_agent_jobs WHERE id=selected_job FOR SHARE;
  IF job.id IS NULL OR job.owner_email IS DISTINCT FROM selected_owner
     OR job.status<>'completed' OR job.workflow_node_key NOT IN
       ('commerce','promotion','market_b2b','independent_review','report')
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_page_core_job'; END IF;
  IF selected_kind='provider' THEN
    IF selected_ordinal>20 THEN RAISE EXCEPTION
      'ai_budget_v11_sign_v3_provider_ordinal'; END IF;
    SELECT * INTO p FROM public.ai_agent_provider_dispatches
      WHERE job_id=selected_job AND dispatch_ordinal=selected_ordinal FOR SHARE;
    IF p.id IS NULL OR p.owner_email IS DISTINCT FROM job.owner_email
       OR p.actor_role<>'admin' OR p.model_id IS DISTINCT FROM job.model_id
       OR p.model_version IS DISTINCT FROM job.model_version
       OR p.tool_policy_digest IS DISTINCT FROM job.tool_policy_digest
       OR p.request_digest !~ '^[0-9a-f]{64}$'
       OR p.lease_epoch<1 OR p.state<>'succeeded'
    THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_provider_drift'; END IF;
    SELECT * INTO pr FROM public.ai_agent_provider_results
      WHERE dispatch_id=p.id FOR SHARE;
    IF pr.dispatch_id IS NULL OR octet_length(pr.response_json)>262144
       OR jsonb_typeof(pr.response_json::jsonb) IS DISTINCT FROM 'object'
       OR pr.response_digest !~ '^[0-9a-f]{64}$'
       OR pr.response_digest IS DISTINCT FROM encode(sha256(convert_to(
         pr.response_json,'UTF8')),'hex')
       OR pr.response_json IS DISTINCT FROM public.ai_v4_replay_canonical(
         pr.response_json::jsonb)
    THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_provider_result'; END IF;
    RETURN jsonb_build_object('id',p.id,'jobId',p.job_id,
      'dispatchOrdinal',p.dispatch_ordinal,'ownerEmail',p.owner_email,
      'actorRole',p.actor_role,'modelId',p.model_id,
      'modelVersion',p.model_version,'toolPolicyDigest',p.tool_policy_digest,
      'requestDigest',p.request_digest,'leaseEpoch',p.lease_epoch,
      'state',p.state,'resultJson',pr.response_json,
      'resultDigest',pr.response_digest);
  END IF;
  SELECT * INTO t FROM public.ai_agent_tool_dispatches
    WHERE job_id=selected_job AND tool_call_ordinal=selected_ordinal FOR SHARE;
  IF t.id IS NULL OR t.lease_epoch<1 OR t.state<>'succeeded'
     OR t.tool_name NOT IN (
       'get_business_promotion_screening_package_v1',
       'get_business_promotion_screening_analysis_v1',
       'get_business_promotion_screening_budget_v1',
       'get_business_promotion_keyword_sku_v1')
     OR octet_length(t.arguments_json)>8192
     OR jsonb_typeof(t.arguments_json::jsonb) IS DISTINCT FROM 'object'
     OR t.arguments_digest !~ '^[0-9a-f]{64}$'
     OR t.arguments_json IS DISTINCT FROM public.ai_v4_replay_canonical(
       t.arguments_json::jsonb)
     OR t.arguments_digest IS DISTINCT FROM encode(sha256(convert_to(
       t.arguments_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_tool_drift'; END IF;
  SELECT * INTO p FROM public.ai_agent_provider_dispatches WHERE
    id=t.provider_dispatch_id AND job_id=selected_job FOR SHARE;
  SELECT * INTO pr FROM public.ai_agent_provider_results WHERE
    dispatch_id=p.id FOR SHARE;
  IF p.id IS NULL OR pr.dispatch_id IS NULL OR p.state<>'succeeded'
     OR p.lease_epoch<1 OR p.lease_epoch>t.lease_epoch
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_tool_provider_drift'; END IF;
  calls:=COALESCE(pr.response_json::jsonb->'calls',
    pr.response_json::jsonb->'toolCalls');
  IF jsonb_typeof(calls) IS DISTINCT FROM 'array'
     OR jsonb_array_length(calls)>40 OR NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(calls) c WHERE
      c->>'id'=t.provider_call_id AND c->>'name'=t.tool_name AND
      public.ai_v4_replay_canonical(c->'arguments')=t.arguments_json)
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_tool_call_drift'; END IF;
  SELECT * INTO tr FROM public.ai_agent_tool_results
    WHERE tool_dispatch_id=t.id FOR SHARE;
  IF tr.tool_dispatch_id IS NULL OR octet_length(tr.result_json)>262144
     OR jsonb_typeof(tr.result_json::jsonb) IS DISTINCT FROM 'object'
     OR tr.result_digest !~ '^[0-9a-f]{64}$'
     OR tr.result_json IS DISTINCT FROM public.ai_v4_replay_canonical(
       tr.result_json::jsonb)
     OR tr.result_digest IS DISTINCT FROM encode(sha256(convert_to(
       tr.result_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_tool_result_drift'; END IF;
  RETURN jsonb_build_object('id',t.id,'jobId',t.job_id,
    'providerDispatchId',t.provider_dispatch_id,
    'toolCallOrdinal',t.tool_call_ordinal,
    'providerCallId',t.provider_call_id,'toolName',t.tool_name,
    'argumentsJson',t.arguments_json,'argumentsDigest',t.arguments_digest,
    'invocationId',t.invocation_id,'leaseEpoch',t.lease_epoch,
    'state',t.state,'resultJson',tr.result_json,
    'resultDigest',tr.result_digest);
END $$"""


LEDGER_ROOT_SQL = r"""CREATE FUNCTION public.ai_budget_v11_sign_ledger_root_v3(
  selected_ticket uuid,selected_claim uuid,selected_run text,
  selected_attempt integer,selected_sha text)
RETURNS text LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE req jsonb;
  jobs public.ai_agent_jobs%ROWTYPE; pages jsonb:='[]'::jsonb;
  page jsonb; page_sha text; i integer; p_count integer; t_count integer;
  body jsonb;
BEGIN
  req:=public.ai_budget_v11_sign_requirements_v3(selected_ticket,
    selected_claim,selected_run,selected_attempt,selected_sha);
  IF (SELECT count(*) FROM public.ai_agent_jobs j WHERE
      j.workflow_run_id=req->>'workflowId')<>5 OR
     (SELECT count(DISTINCT j.workflow_node_key) FROM public.ai_agent_jobs j
      WHERE j.workflow_run_id=req->>'workflowId' AND
      j.workflow_node_key IN ('commerce','promotion','market_b2b',
        'independent_review','report'))<>5
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_job_inventory'; END IF;
  FOR jobs IN SELECT * FROM public.ai_agent_jobs j WHERE
      j.workflow_run_id=req->>'workflowId' ORDER BY j.workflow_node_key LOOP
    IF jobs.owner_email IS DISTINCT FROM req->>'ownerEmail' OR
       jobs.status<>'completed' THEN
      RAISE EXCEPTION 'ai_budget_v11_sign_v3_job_owner_or_state'; END IF;
    SELECT count(*) INTO p_count FROM public.ai_agent_provider_dispatches p
      WHERE p.job_id=jobs.id;
    SELECT count(*) INTO t_count FROM public.ai_agent_tool_dispatches t
      WHERE t.job_id=jobs.id;
    IF p_count NOT BETWEEN 1 AND 20 OR t_count NOT BETWEEN 1 AND 40
       OR jobs.provider_round_count IS DISTINCT FROM p_count
       OR jobs.tool_call_count IS DISTINCT FROM t_count
    THEN
      RAISE EXCEPTION 'ai_budget_v11_sign_v3_job_limit'; END IF;
    FOR i IN 1..p_count LOOP
      page:=public.ai_budget_v11_sign_ledger_page_core_v3(
        jobs.id,'provider',i,req->>'ownerEmail');
      page_sha:=encode(sha256(convert_to(public.ai_v4_replay_canonical(page),
        'UTF8')),'hex');
      pages:=pages||jsonb_build_array(jsonb_build_object('jobId',jobs.id,
        'kind','provider','ordinal',i,'pageSha256',page_sha));
    END LOOP;
    FOR i IN 1..t_count LOOP
      page:=public.ai_budget_v11_sign_ledger_page_core_v3(
        jobs.id,'tool',i,req->>'ownerEmail');
      page_sha:=encode(sha256(convert_to(public.ai_v4_replay_canonical(page),
        'UTF8')),'hex');
      pages:=pages||jsonb_build_array(jsonb_build_object('jobId',jobs.id,
        'kind','tool','ordinal',i,'pageSha256',page_sha));
    END LOOP;
  END LOOP;
  body:=jsonb_build_object('schemaVersion','budget-v11-ledger-root-v3',
    'runId',selected_run,'attempt',selected_attempt,'pages',pages);
  RETURN encode(sha256(convert_to(public.ai_v4_replay_canonical(body),'UTF8')),'hex');
END $$"""


INVENTORY_SQL = r"""CREATE FUNCTION public.ai_budget_v11_sign_ledger_inventory_v3(
  selected_ticket uuid,selected_claim uuid,selected_run text,
  selected_attempt integer,selected_sha text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE req jsonb; root text; jobs jsonb;
BEGIN
  req:=public.ai_budget_v11_sign_requirements_v3(selected_ticket,
    selected_claim,selected_run,selected_attempt,selected_sha);
  root:=public.ai_budget_v11_sign_ledger_root_v3(selected_ticket,
    selected_claim,selected_run,selected_attempt,selected_sha);
  SELECT jsonb_agg(jsonb_build_object('jobId',j.id,
    'role',j.workflow_node_key,
    'providerCount',(SELECT count(*) FROM public.ai_agent_provider_dispatches p
      WHERE p.job_id=j.id),
    'toolCount',(SELECT count(*) FROM public.ai_agent_tool_dispatches t
      WHERE t.job_id=j.id)) ORDER BY j.workflow_node_key) INTO jobs
    FROM public.ai_agent_jobs j WHERE j.workflow_run_id=req->>'workflowId';
  IF jsonb_array_length(jobs)<>5 OR EXISTS(
    SELECT 1 FROM jsonb_array_elements(jobs) e WHERE
      (e->>'providerCount')::integer NOT BETWEEN 1 AND 20 OR
      (e->>'toolCount')::integer NOT BETWEEN 1 AND 40)
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_inventory_bound'; END IF;
  RETURN jsonb_build_object('schemaVersion','budget-v11-ledger-inventory-v3',
    'ticketId',selected_ticket::text,'claimId',selected_claim::text,
    'runId',selected_run,'attempt',selected_attempt,
    'ledgerRoot',root,'jobs',jobs,'readyAuthorized',false);
END $$"""


READ_SQL = r"""CREATE FUNCTION public.ai_budget_v11_read_agent_ledger_v3(
  selected_ticket uuid,selected_claim uuid,selected_run text,
  selected_attempt integer,selected_sha text,selected_job text,
  selected_kind text,selected_ordinal integer,selected_expected_root text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE req jsonb; job public.ai_agent_jobs%ROWTYPE;
  value jsonb;
BEGIN
  req:=public.ai_budget_v11_sign_requirements_v3(selected_ticket,
    selected_claim,selected_run,selected_attempt,selected_sha);
  IF selected_job IS NULL OR selected_job !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_kind NOT IN ('provider','tool')
     OR selected_ordinal NOT BETWEEN 1 AND 40
     OR selected_expected_root IS NULL OR
        selected_expected_root !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_page_shape'; END IF;
  SELECT * INTO job FROM public.ai_agent_jobs WHERE id=selected_job FOR SHARE;
  IF job.id IS NULL OR job.workflow_run_id IS DISTINCT FROM req->>'workflowId'
     OR job.owner_email IS DISTINCT FROM req->>'ownerEmail'
     OR job.workflow_node_key NOT IN ('commerce','promotion','market_b2b',
       'independent_review','report')
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_job_drift'; END IF;
  value:=public.ai_budget_v11_sign_ledger_page_core_v3(selected_job,
    selected_kind,selected_ordinal,req->>'ownerEmail');
  RETURN jsonb_build_object('schemaVersion','budget-v11-ledger-page-v3',
    'ticketId',selected_ticket::text,'claimId',selected_claim::text,
    'runId',selected_run,'attempt',selected_attempt,'jobId',selected_job,
    'kind',selected_kind,'ordinal',selected_ordinal,
    'ledgerRoot',selected_expected_root,
    'page',value,'pageSha256',encode(sha256(convert_to(
      public.ai_v4_replay_canonical(value),'UTF8')),'hex'),
    'readyAuthorized',false);
END $$"""


def private_mac_sql() -> str:
    old = import_module(
        "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
    body = old.MAC
    changes = (("ai_budget_v11_private_mac_valid(",
        "ai_budget_v11_private_mac_valid_v3("),
        ("teruisi:budget-v11:protected-verifier:v1",DOMAIN),
        ("WHERE item.key_id=selected_key AND item.status='active';",
         "WHERE item.key_id=selected_key AND item.status='active' FOR SHARE;"))
    for before, after in changes:
        if body.count(before) != 1:
            raise RuntimeError("0076 requires frozen 0068 private MAC source")
        body = body.replace(before, after, 1)
    return body


RECORD_SQL = r"""CREATE FUNCTION public.ai_budget_v11_record_signed_receipt_v3(
  selected_ticket uuid,selected_claim uuid,selected_run text,
  selected_attempt integer,selected_sha text,receipt_text text,receipt_mac text)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE req jsonb; value jsonb; root text; receipt_id uuid;
BEGIN
  req:=public.ai_budget_v11_sign_requirements_v3(selected_ticket,
    selected_claim,selected_run,selected_attempt,selected_sha);
  IF receipt_text IS NULL OR octet_length(receipt_text) NOT BETWEEN 1 AND 262144
     OR receipt_mac IS NULL OR receipt_mac !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_receipt_shape'; END IF;
  value:=receipt_text::jsonb;
  IF receipt_text IS DISTINCT FROM public.ai_v4_replay_canonical(value)
     OR jsonb_typeof(value) IS DISTINCT FROM 'object'
     OR NOT value ?& ARRAY['schemaVersion','purpose','keyId','ticketId',
       'claimId','runId','attempt','runVersion','workflowVersion',
       'reportId','ownerEmail',
       'bindingDigest','oldAttestationId','loginAttestationId',
       'attestationSha256','ledgerRoot','filePageRoot']
     OR value-ARRAY['schemaVersion','purpose','keyId','ticketId',
       'claimId','runId','attempt','runVersion','workflowVersion',
       'reportId','ownerEmail',
       'bindingDigest','oldAttestationId','loginAttestationId',
       'attestationSha256','ledgerRoot','filePageRoot']<>'{}'::jsonb
     OR value->>'schemaVersion' IS DISTINCT FROM
       'budget-v11-ticket-bound-signed-receipt-v3'
     OR value->>'purpose' IS DISTINCT FROM
       'teruisi:business-promotion-budget-v11:ticket-bound-sign-once:v3'
     OR value->>'ticketId' IS DISTINCT FROM req->>'ticketId'
     OR value->>'claimId' IS DISTINCT FROM req->>'claimId'
     OR value->>'runId' IS DISTINCT FROM req->>'runId'
     OR value->>'attempt' IS DISTINCT FROM req->>'attempt'
     OR value->>'runVersion' IS DISTINCT FROM req->>'runVersion'
     OR value->>'workflowVersion' IS DISTINCT FROM req->>'workflowVersion'
     OR value->>'reportId' IS DISTINCT FROM req->>'reportId'
     OR value->>'ownerEmail' IS DISTINCT FROM req->>'ownerEmail'
     OR value->>'bindingDigest' IS DISTINCT FROM req->>'bindingDigest'
     OR value->>'oldAttestationId' IS DISTINCT FROM req->>'oldAttestationId'
     OR value->>'loginAttestationId' IS DISTINCT FROM req->>'loginAttestationId'
     OR value->>'attestationSha256' IS DISTINCT FROM req->>'attestationSha256'
     OR value->>'filePageRoot' IS DISTINCT FROM req->>'filePageRoot'
     OR value->>'keyId' !~ '^[A-Za-z0-9_-]{1,64}$'
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_receipt_binding'; END IF;
  root:=public.ai_budget_v11_sign_ledger_root_v3(selected_ticket,
    selected_claim,selected_run,selected_attempt,selected_sha);
  IF value->>'ledgerRoot' IS DISTINCT FROM root OR NOT
     public.ai_budget_v11_private_mac_valid_v3(value->>'keyId',
       receipt_text,receipt_mac)
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_mac_or_ledger_invalid'; END IF;
  receipt_id:=gen_random_uuid();
  INSERT INTO public.protected_business_budget_v11_signed_receipts_v3(
    id,ticket_id,claim_id,run_id,attempt,report_id,owner_email,run_version,
    binding_digest,old_attestation_id,login_attestation_id,
    attestation_sha256,ledger_root,file_page_root,key_id,
    receipt_sha256,receipt_text,receipt_mac,recorded_at)
  VALUES(receipt_id,selected_ticket,selected_claim,selected_run,
    selected_attempt,req->>'reportId',req->>'ownerEmail',
    (req->>'runVersion')::bigint,req->>'bindingDigest',
    req->>'oldAttestationId',req->>'loginAttestationId',selected_sha,
    root,req->>'filePageRoot',value->>'keyId',
    encode(sha256(convert_to(receipt_text,'UTF8')),'hex'),
    receipt_text,receipt_mac,clock_timestamp());
  RETURN receipt_id;
END $$"""


OUTCOME_SQL = r"""CREATE FUNCTION public.ai_budget_v11_sign_outcome_v3(
  selected_ticket uuid,selected_claim uuid,selected_receipt_sha text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE recorded public.protected_business_budget_v11_signed_receipts_v3%ROWTYPE;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_sign_login'
     OR selected_ticket IS NULL OR selected_claim IS NULL
     OR selected_receipt_sha !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS(SELECT 1 FROM public.protected_business_budget_v11_proof_ticket_claims c
       WHERE c.id=selected_claim AND c.ticket_id=selected_ticket)
  THEN RAISE EXCEPTION 'ai_budget_v11_sign_v3_outcome_unavailable'; END IF;
  SELECT * INTO recorded FROM public.protected_business_budget_v11_signed_receipts_v3
    WHERE claim_id=selected_claim AND ticket_id=selected_ticket;
  IF recorded.id IS NULL THEN RETURN jsonb_build_object(
    'schemaVersion','budget-v11-sign-outcome-v3','status','absent_observed',
    'receiptId',null,'retryAllowed',false); END IF;
  IF recorded.receipt_sha256 IS DISTINCT FROM selected_receipt_sha OR
     encode(sha256(convert_to(recorded.receipt_text,'UTF8')),'hex')
       IS DISTINCT FROM recorded.receipt_sha256
  THEN RETURN jsonb_build_object(
    'schemaVersion','budget-v11-sign-outcome-v3','status','conflict',
    'receiptId',null,'retryAllowed',false); END IF;
  RETURN jsonb_build_object('schemaVersion','budget-v11-sign-outcome-v3',
    'status','committed','receiptId',recorded.id::text,'retryAllowed',false);
END $$"""
