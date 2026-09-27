"""Default-closed v11 identity v2 SQL, installed by 0070 without credentials.

Three distinct service roles start NOLOGIN and have no members. Ticket issue
and claim rows are append-only, with exact run/attempt bindings. This does not
authorize full ORM preflight, v11 ready, publication, or download.
"""
from __future__ import annotations

from importlib import import_module


ATTEST = "teruisi_ai_budget_v11_attest_login"
SIGN = "teruisi_ai_budget_v11_sign_login"
PUBLISH = "teruisi_ai_budget_v11_publish_login"
ROLES = (ATTEST, SIGN, PUBLISH)
TABLE = "public.protected_business_budget_v11_proof_tickets"
CLAIMS = "public.protected_business_budget_v11_proof_ticket_claims"
ISSUE = "public.ai_budget_v11_issue_proof_ticket_v2(text,integer,text)"
READ = "public.ai_budget_v11_read_proof_ticket_v2(text,text,integer,text)"
VERIFY = "public.ai_budget_v11_verify_protected_receipt_v2(text,integer,text,text)"


ROW_GUARD = r"""CREATE FUNCTION public.ai_budget_v11_proof_ticket_row_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE expected_role text;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_budget_v11_ticket_immutable'; END IF;
  expected_role:=CASE TG_RELID
    WHEN 'public.protected_business_budget_v11_proof_tickets'::regclass
      THEN 'teruisi_ai_budget_v11_attest_login'
    WHEN 'public.protected_business_budget_v11_proof_ticket_claims'::regclass
      THEN 'teruisi_ai_budget_v11_sign_login' ELSE NULL END;
  IF expected_role IS NULL OR session_user IS DISTINCT FROM expected_role
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_budget_v11_ticket_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""


ISSUE_SQL = r"""CREATE FUNCTION public.ai_budget_v11_issue_proof_ticket_v2(
  selected_run text,selected_attempt integer,selected_attestation_sha text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  proof public.ai_business_promotion_budget_v11_attestations%ROWTYPE;
  ticket_id uuid;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_attest_login'
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR selected_attestation_sha !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.rolname='teruisi_ai_budget_v11_attest_login'
         AND r.rolcanlogin AND NOT r.rolinherit AND NOT r.rolsuper
         AND NOT r.rolcreatedb AND NOT r.rolcreaterole
         AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
       WHERE m.roleid='teruisi_ai_budget_v11_attest_login'::regrole
          OR m.member='teruisi_ai_budget_v11_attest_login'::regrole)
  THEN RAISE EXCEPTION 'ai_budget_v11_ticket_issuer_unavailable'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR SHARE;
  SELECT * INTO proof FROM public.ai_business_promotion_budget_v11_attestations item
    WHERE item.run_id=selected_run AND item.attempt=selected_attempt FOR SHARE;
  IF parent.id IS NULL OR proof.id IS NULL OR parent.renderer_version<>11
     OR parent.draft OR parent.status<>'paused'
     OR parent.error_code<>'renderer_unpublished'
     OR parent.progress_json::jsonb IS DISTINCT FROM jsonb_build_object(
       'stage','staged_unpublished','attempt',selected_attempt)
     OR parent.attempt<>selected_attempt
     OR proof.attestation_sha256 IS DISTINCT FROM selected_attestation_sha
     OR proof.report_id IS DISTINCT FROM parent.report_id
     OR proof.owner_email IS DISTINCT FROM parent.owner_email
     OR proof.binding_digest IS DISTINCT FROM parent.binding_digest
     OR encode(sha256(convert_to(proof.attestation_json,'UTF8')),'hex')
        IS DISTINCT FROM selected_attestation_sha
     OR encode(sha256(convert_to(parent.manifest_json,'UTF8')),'hex')
        IS DISTINCT FROM proof.compact_json_sha256
  THEN RAISE EXCEPTION 'ai_budget_v11_ticket_proof_drift'; END IF;
  ticket_id:=gen_random_uuid();
  INSERT INTO public.protected_business_budget_v11_proof_tickets(
    id,run_id,attempt,parent_version,report_id,binding_digest,
    attestation_sha256,created_at,expires_at)
  VALUES(ticket_id,parent.id,parent.attempt,parent.version,parent.report_id,
    parent.binding_digest,selected_attestation_sha,clock_timestamp(),
    clock_timestamp()+interval '10 minutes');
  RETURN jsonb_build_object('schemaVersion','budget-v11-proof-ticket-v2',
    'ticketId',ticket_id::text,'runId',parent.id,'attempt',parent.attempt,
    'attestationSha256',selected_attestation_sha,'readyAuthorized',false);
END $$"""


READ_SQL = r"""CREATE FUNCTION public.ai_budget_v11_read_proof_ticket_v2(
  selected_ticket text,selected_run text,selected_attempt integer,
  selected_attestation_sha text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  ticket public.protected_business_budget_v11_proof_tickets%ROWTYPE;
  proof public.ai_business_promotion_budget_v11_attestations%ROWTYPE;
  claim_id uuid;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_sign_login'
     OR selected_ticket IS NULL OR selected_ticket !~
       '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR selected_attestation_sha !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.rolname='teruisi_ai_budget_v11_sign_login'
         AND r.rolcanlogin AND NOT r.rolinherit AND NOT r.rolsuper
         AND NOT r.rolcreatedb AND NOT r.rolcreaterole
         AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
       WHERE m.roleid='teruisi_ai_budget_v11_sign_login'::regrole
          OR m.member='teruisi_ai_budget_v11_sign_login'::regrole)
  THEN RAISE EXCEPTION 'ai_budget_v11_ticket_reader_unavailable'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR SHARE;
  SELECT * INTO ticket FROM public.protected_business_budget_v11_proof_tickets item
    WHERE item.id=selected_ticket::uuid FOR SHARE;
  SELECT * INTO proof FROM public.ai_business_promotion_budget_v11_attestations item
    WHERE item.run_id=selected_run AND item.attempt=selected_attempt FOR SHARE;
  IF parent.id IS NULL OR ticket.id IS NULL OR proof.id IS NULL
     OR parent.renderer_version<>11 OR parent.draft
     OR parent.status<>'paused' OR parent.error_code<>'renderer_unpublished'
     OR parent.progress_json::jsonb IS DISTINCT FROM jsonb_build_object(
       'stage','staged_unpublished','attempt',selected_attempt)
     OR ticket.run_id IS DISTINCT FROM selected_run
     OR ticket.attempt IS DISTINCT FROM selected_attempt
     OR ticket.parent_version IS DISTINCT FROM parent.version
     OR ticket.report_id IS DISTINCT FROM parent.report_id
     OR ticket.binding_digest IS DISTINCT FROM parent.binding_digest
     OR ticket.attestation_sha256 IS DISTINCT FROM selected_attestation_sha
     OR ticket.expires_at<=clock_timestamp()
     OR proof.attestation_sha256 IS DISTINCT FROM selected_attestation_sha
     OR proof.report_id IS DISTINCT FROM parent.report_id
     OR proof.owner_email IS DISTINCT FROM parent.owner_email
     OR proof.binding_digest IS DISTINCT FROM parent.binding_digest
     OR encode(sha256(convert_to(proof.attestation_json,'UTF8')),'hex')
        IS DISTINCT FROM selected_attestation_sha
     OR encode(sha256(convert_to(parent.manifest_json,'UTF8')),'hex')
        IS DISTINCT FROM proof.compact_json_sha256
  THEN RAISE EXCEPTION 'ai_budget_v11_ticket_claim_invalid'; END IF;
  claim_id:=gen_random_uuid();
  INSERT INTO public.protected_business_budget_v11_proof_ticket_claims(
    id,ticket_id,run_id,attempt,attestation_sha256,claimed_at)
  VALUES(claim_id,ticket.id,parent.id,parent.attempt,
    selected_attestation_sha,clock_timestamp())
  ON CONFLICT (ticket_id) DO NOTHING;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_budget_v11_ticket_claim_conflict'; END IF;
  RETURN jsonb_build_object('schemaVersion','budget-v11-proof-read-v2',
    'ticketId',ticket.id::text,'claimId',claim_id::text,
    'runId',parent.id,'attempt',parent.attempt,
    'runVersion',parent.version,'reportId',parent.report_id,
    'ownerEmail',parent.owner_email,'bindingDigest',parent.binding_digest,
    'attestationId',proof.id,'attestationSha256',proof.attestation_sha256,
    'attestationText',proof.attestation_json,'readyAuthorized',false);
END $$"""


def verify_sql():
    """Version the exact 0068 verifier without altering its OID/body/ACL."""
    old = import_module(
        "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
    return old.VERIFY.replace(
        "ai_budget_v11_verify_protected_receipt(",
        "ai_budget_v11_verify_protected_receipt_v2(", 1).replace(
        "session_user<>'teruisi_ai_budget_v11_publisher'",
        "session_user<>'teruisi_ai_budget_v11_publish_login'", 1).replace(
        "OR selected_run IS NULL",
        "OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE "
        "r.rolname='teruisi_ai_budget_v11_publish_login' AND r.rolcanlogin "
        "AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb "
        "AND NOT r.rolcreaterole AND NOT r.rolreplication "
        "AND NOT r.rolbypassrls) OR EXISTS (SELECT 1 FROM "
        "pg_catalog.pg_auth_members m WHERE "
        "m.roleid='teruisi_ai_budget_v11_publish_login'::regrole "
        "OR m.member='teruisi_ai_budget_v11_publish_login'::regrole) "
        "OR selected_run IS NULL", 1)


SIGNATURES = (ISSUE, READ, VERIFY)
