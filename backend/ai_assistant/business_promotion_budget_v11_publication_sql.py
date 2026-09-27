"""0078 default-closed v11 publication sidecar and independent RO chunk.

This does not turn AiBusinessFileRun into ready and does not change the old
renderer-11 ready/download guards. It is an isolated, signed candidate only.
"""
from __future__ import annotations

from ai_assistant import business_promotion_budget_v11_ticket_sign_sql as v3


PUBLISH_ROLE="teruisi_ai_budget_v11_publish_login"
READ_ROLE="teruisi_ai_budget_v11_download_v2_login"
TABLE="public.protected_business_budget_v11_publications_v2"
GUARD="public.ai_budget_v11_publication_guard_v2()"
PUBLISH_REQ=("public.ai_budget_v11_publish_requirements_v2("
    "uuid,uuid,text,integer,text)")
PUBLISH_PAGE=("public.ai_budget_v11_publish_ledger_page_v2("
    "text,text,integer,text)")
PUBLISH_ROOT=("public.ai_budget_v11_publish_ledger_root_v2("
    "uuid,uuid,text,integer,text)")
READ_REQ=("public.ai_budget_v11_download_requirements_v2("
    "uuid,uuid,text,integer,text)")
READ_PAGE=("public.ai_budget_v11_download_ledger_page_v2("
    "text,text,integer,text)")
READ_ROOT=("public.ai_budget_v11_download_ledger_root_v2("
    "uuid,uuid,text,integer,text)")
RO_MAC="public.ai_budget_v11_private_mac_valid_v4_ro(text,text,text)"
PUBLISH="public.ai_budget_v11_publish_signed_v2(text,integer,bigint,uuid,text,text)"
OUTCOME="public.ai_budget_v11_publish_outcome_v2(text,integer,text,text)"
CHUNK=("public.ai_budget_v11_read_published_chunk_v2("
    "text,integer,integer,text,integer,text)")
SIGNATURES=(GUARD,PUBLISH_REQ,PUBLISH_PAGE,PUBLISH_ROOT,
    READ_REQ,READ_PAGE,READ_ROOT,RO_MAC,PUBLISH,OUTCOME,CHUNK)
REQUEST_SCHEMA="budget-v11-signed-publication-request-v2"
RESULT_SCHEMA="budget-v11-signed-publication-outcome-v2"


def _replace(source:str, old:str, new:str, count:int=1)->str:
    if source.count(old)!=count:
        raise RuntimeError("0078 requires exact frozen 0076 SQL predecessor")
    return source.replace(old,new)


def _requirements(*,read_only:bool)->str:
    source=v3.REQUIREMENTS_SQL
    role=READ_ROLE if read_only else PUBLISH_ROLE
    name="ai_budget_v11_download_requirements_v2" if read_only else \
        "ai_budget_v11_publish_requirements_v2"
    source=_replace(source,"ai_budget_v11_sign_requirements_v3(",name+"(")
    source=source.replace(v3.SIGN,role)
    if read_only:
        if "OR ticket.expires_at<=clock_timestamp()" not in source:
            raise RuntimeError("0078 requires exact ticket expiry predecessor")
        # Sign/commit must occur before ticket expiry. Immutable publication
        # remains readable later, subject to current roots and active key.
        source=source.replace("OR ticket.expires_at<=clock_timestamp()",
            "OR false /* publication was admitted while ticket active */",1)
        source=source.replace(" FOR SHARE", "")
    return source


def _page(*,read_only:bool)->str:
    source=v3.PAGE_CORE_SQL
    role=READ_ROLE if read_only else PUBLISH_ROLE
    name="ai_budget_v11_download_ledger_page_v2" if read_only else \
        "ai_budget_v11_publish_ledger_page_v2"
    source=_replace(source,"ai_budget_v11_sign_ledger_page_core_v3(",name+"(")
    source=source.replace(v3.SIGN,role)
    if read_only: source=source.replace(" FOR SHARE", "")
    return source


def _root(*,read_only:bool)->str:
    source=v3.LEDGER_ROOT_SQL
    name="ai_budget_v11_download_ledger_root_v2" if read_only else \
        "ai_budget_v11_publish_ledger_root_v2"
    req="ai_budget_v11_download_requirements_v2" if read_only else \
        "ai_budget_v11_publish_requirements_v2"
    page="ai_budget_v11_download_ledger_page_v2" if read_only else \
        "ai_budget_v11_publish_ledger_page_v2"
    source=_replace(source,"ai_budget_v11_sign_ledger_root_v3(",name+"(")
    source=_replace(source,"ai_budget_v11_sign_requirements_v3(",req+"(")
    source=_replace(source,"ai_budget_v11_sign_ledger_page_core_v3(",
        page+"(",2)
    if read_only: source=source.replace(" FOR SHARE", "")
    return source


PUBLISH_REQUIREMENTS_SQL=_requirements(read_only=False)
PUBLISH_PAGE_SQL=_page(read_only=False)
PUBLISH_ROOT_SQL=_root(read_only=False)
READ_REQUIREMENTS_SQL=_requirements(read_only=True)
READ_PAGE_SQL=_page(read_only=True)
READ_ROOT_SQL=_root(read_only=True)


def ro_mac_sql()->str:
    source=v3.private_mac_sql()
    source=_replace(source,"ai_budget_v11_private_mac_valid_v3(",
        "ai_budget_v11_private_mac_valid_v4_ro(")
    source=_replace(source," FOR SHARE;",";",1)
    return source


CREATE_TABLE="""CREATE TABLE public.protected_business_budget_v11_publications_v2 (
  id uuid PRIMARY KEY,
  run_id varchar(160) NOT NULL UNIQUE REFERENCES public.ai_business_file_runs(id)
    ON DELETE RESTRICT,
  attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 5),
  report_id varchar(160) NOT NULL REFERENCES public.ai_report_runs(id)
    ON DELETE RESTRICT,
  owner_email varchar(320) NOT NULL,
  run_version bigint NOT NULL CHECK (run_version>=1),
  signed_receipt_id uuid NOT NULL UNIQUE REFERENCES
    public.protected_business_budget_v11_signed_receipts_v3(id)
    ON DELETE RESTRICT,
  signed_receipt_sha256 varchar(64) NOT NULL CHECK
    (signed_receipt_sha256 ~ '^[0-9a-f]{64}$'),
  ticket_id uuid NOT NULL,
  claim_id uuid NOT NULL,
  binding_digest varchar(64) NOT NULL CHECK
    (binding_digest ~ '^[0-9a-f]{64}$'),
  ledger_root varchar(64) NOT NULL CHECK (ledger_root ~ '^[0-9a-f]{64}$'),
  file_page_root varchar(64) NOT NULL CHECK
    (file_page_root ~ '^[0-9a-f]{64}$'),
  key_id varchar(64) NOT NULL,
  request_digest varchar(64) NOT NULL UNIQUE CHECK
    (request_digest ~ '^[0-9a-f]{64}$'),
  published_at timestamptz NOT NULL,
  CONSTRAINT ai_budget_v11_pub_v2_attempt_uq UNIQUE(run_id,attempt)
)"""


def validate_constraints(rows: list[tuple]) -> None:
    """Validate the exact PostgreSQL catalog contract without database state."""
    kinds=sorted(row[0] for row in rows)
    if kinds!=["c"]*7+["f"]*3+["p"]+["u"]*4 or any(
            row[3:5]!=(True,False) for row in rows):
        raise ValueError("0078 publication constraint inventory drift")
    by={(kind,tuple(names)):(target,definition)
        for kind,names,target,_,_,definition in rows}
    if len(by)!=len(rows):
        raise ValueError("0078 publication duplicate constraint drift")
    for key,expected in ((("p",("id",)),"PRIMARY KEY (id)"),
            (("u",("run_id",)),"UNIQUE (run_id)"),
            (("u",("signed_receipt_id",)),"UNIQUE (signed_receipt_id)"),
            (("u",("request_digest",)),"UNIQUE (request_digest)"),
            (("u",("run_id","attempt")),"UNIQUE (run_id, attempt)")):
        if by.get(key,(None,None))[1]!=expected:
            raise ValueError("0078 publication exact unique constraint drift")
    for column,target in (("run_id","ai_business_file_runs"),
            ("report_id","ai_report_runs"),
            ("signed_receipt_id",
             "protected_business_budget_v11_signed_receipts_v3")):
        actual,definition=by.get(("f",(column,)),(None,None))
        variants={"FOREIGN KEY ("+column+") REFERENCES "+prefix+
            target+"(id) ON DELETE RESTRICT" for prefix in ("","public.")}
        if actual not in (target,"public."+target) or definition not in variants:
            raise ValueError("0078 publication foreign key drift")
    checks={
        "attempt":"CHECK (((attempt >= 1) AND (attempt <= 5)))",
        "run_version":"CHECK ((run_version >= 1))"}
    for column in ("signed_receipt_sha256","binding_digest","ledger_root",
            "file_page_root","request_digest"):
        checks[column]="CHECK ((("+column+")::text ~ '^[0-9a-f]{64}$'::text))"
    for column,expected in checks.items():
        if by.get(("c",(column,)),(None,None))[1]!=expected:
            raise ValueError("0078 publication exact check drift")


GUARD_SQL="""CREATE FUNCTION public.ai_budget_v11_publication_guard_v2()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' OR session_user<>'teruisi_ai_budget_v11_publish_login'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_budget_v11_publication_immutable'; END IF;
  RETURN NEW;
END $$"""


PUBLISH_SQL=r"""CREATE FUNCTION public.ai_budget_v11_publish_signed_v2(
  selected_run text,selected_attempt integer,expected_version bigint,
  selected_signed_id uuid,selected_signed_sha text,selected_request_sha text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  signed public.protected_business_budget_v11_signed_receipts_v3%ROWTYPE;
  prior public.protected_business_budget_v11_publications_v2%ROWTYPE;
  req jsonb; body jsonb; request_body jsonb; current_root text;
  expected_request text; publication_id uuid;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_publish_login'
     OR current_database() NOT IN ('teruisi_ai_rehearsal',
       'test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR inet_server_addr()::text NOT IN
       ('127.0.0.1','127.0.0.1/32','::1','::1/128')
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
       r.rolname='teruisi_ai_budget_v11_publish_login' AND r.rolcanlogin
       AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
       AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE
       m.roleid='teruisi_ai_budget_v11_publish_login'::regrole OR
       m.member='teruisi_ai_budget_v11_publish_login'::regrole)
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR expected_version IS NULL OR expected_version<1
     OR selected_signed_id IS NULL
     OR selected_signed_sha IS NULL OR selected_signed_sha !~ '^[0-9a-f]{64}$'
     OR selected_request_sha IS NULL OR selected_request_sha !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_budget_v11_publish_v2_unavailable'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs
    WHERE id=selected_run FOR UPDATE;
  SELECT * INTO signed FROM public.protected_business_budget_v11_signed_receipts_v3
    WHERE id=selected_signed_id FOR SHARE;
  IF parent.id IS NULL OR signed.id IS NULL OR parent.renderer_version<>11
     OR parent.draft OR parent.status<>'paused'
     OR parent.error_code<>'renderer_unpublished'
     OR parent.progress_json::jsonb IS DISTINCT FROM jsonb_build_object(
       'stage','staged_unpublished','attempt',selected_attempt)
     OR parent.attempt IS DISTINCT FROM selected_attempt
     OR parent.version IS DISTINCT FROM expected_version
     OR signed.run_id IS DISTINCT FROM parent.id
     OR signed.attempt IS DISTINCT FROM parent.attempt
     OR signed.run_version IS DISTINCT FROM parent.version
     OR signed.report_id IS DISTINCT FROM parent.report_id
     OR signed.owner_email IS DISTINCT FROM parent.owner_email
     OR signed.binding_digest IS DISTINCT FROM parent.binding_digest
     OR signed.receipt_sha256 IS DISTINCT FROM selected_signed_sha
     OR encode(sha256(convert_to(signed.receipt_text,'UTF8')),'hex')
       IS DISTINCT FROM selected_signed_sha
  THEN RAISE EXCEPTION 'ai_budget_v11_publish_v2_signed_root_drift'; END IF;
  req:=public.ai_budget_v11_publish_requirements_v2(signed.ticket_id,
    signed.claim_id,parent.id,parent.attempt,signed.attestation_sha256);
  current_root:=public.ai_budget_v11_publish_ledger_root_v2(signed.ticket_id,
    signed.claim_id,parent.id,parent.attempt,signed.attestation_sha256);
  body:=signed.receipt_text::jsonb;
  IF signed.ledger_root IS DISTINCT FROM current_root
     OR signed.file_page_root IS DISTINCT FROM req->>'filePageRoot'
     OR signed.ticket_id::text IS DISTINCT FROM req->>'ticketId'
     OR signed.claim_id::text IS DISTINCT FROM req->>'claimId'
     OR signed.old_attestation_id IS DISTINCT FROM req->>'oldAttestationId'
     OR signed.login_attestation_id IS DISTINCT FROM req->>'loginAttestationId'
     OR signed.attestation_sha256 IS DISTINCT FROM req->>'attestationSha256'
     OR signed.key_id IS DISTINCT FROM body->>'keyId'
     OR body->>'ticketId' IS DISTINCT FROM signed.ticket_id::text
     OR body->>'claimId' IS DISTINCT FROM signed.claim_id::text
     OR body->>'ledgerRoot' IS DISTINCT FROM current_root
     OR body->>'filePageRoot' IS DISTINCT FROM req->>'filePageRoot'
     OR body->>'runId' IS DISTINCT FROM parent.id
     OR body->>'runVersion' IS DISTINCT FROM parent.version::text
     OR body->>'workflowVersion' IS DISTINCT FROM req->>'workflowVersion'
     OR signed.receipt_text IS DISTINCT FROM public.ai_v4_replay_canonical(body)
     OR NOT public.ai_budget_v11_private_mac_valid_v3(signed.key_id,
       signed.receipt_text,signed.receipt_mac)
  THEN RAISE EXCEPTION 'ai_budget_v11_publish_v2_mac_or_root_drift'; END IF;
  PERFORM public.ai_business_volume_manifest_check(parent.id,parent.attempt,
    parent.binding_digest,parent.draft,parent.manifest_json);
  request_body:=jsonb_build_object('schemaVersion',
    'budget-v11-signed-publication-request-v2',
    'runId',parent.id,'attempt',parent.attempt,
    'expectedVersion',parent.version,'signedReceiptId',signed.id::text,
    'signedReceiptSha256',signed.receipt_sha256,
    'ticketId',signed.ticket_id::text,'claimId',signed.claim_id::text,
    'reportId',parent.report_id,'ownerEmail',parent.owner_email,
    'bindingDigest',parent.binding_digest,'ledgerRoot',current_root,
    'filePageRoot',signed.file_page_root,'keyId',signed.key_id);
  expected_request:=encode(sha256(convert_to(
    public.ai_v4_replay_canonical(request_body),'UTF8')),'hex');
  IF selected_request_sha IS DISTINCT FROM expected_request
  THEN RAISE EXCEPTION 'ai_budget_v11_publish_v2_request_sha'; END IF;
  SELECT * INTO prior FROM public.protected_business_budget_v11_publications_v2
    WHERE run_id=parent.id FOR SHARE;
  IF prior.id IS NOT NULL THEN
    RAISE EXCEPTION 'ai_budget_v11_publish_v2_existing_use_outcome';
  END IF;
  publication_id:=gen_random_uuid();
  INSERT INTO public.protected_business_budget_v11_publications_v2(
    id,run_id,attempt,report_id,owner_email,run_version,signed_receipt_id,
    signed_receipt_sha256,ticket_id,claim_id,binding_digest,ledger_root,
    file_page_root,key_id,request_digest,published_at)
  VALUES(publication_id,parent.id,parent.attempt,parent.report_id,
    parent.owner_email,parent.version,signed.id,signed.receipt_sha256,
    signed.ticket_id,signed.claim_id,parent.binding_digest,current_root,
    signed.file_page_root,signed.key_id,selected_request_sha,clock_timestamp());
  RETURN jsonb_build_object('schemaVersion',
    'budget-v11-signed-publication-outcome-v2',
    'status','committed','publicationId',publication_id::text,
    'requestDigest',selected_request_sha,'retryAllowed',false,
    'readyAuthorized',false);
END $$"""


OUTCOME_SQL=r"""CREATE FUNCTION public.ai_budget_v11_publish_outcome_v2(
  selected_run text,selected_attempt integer,selected_request_sha text,
  selected_signed_sha text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE recorded public.protected_business_budget_v11_publications_v2%ROWTYPE;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_publish_login'
     OR current_database() NOT IN ('teruisi_ai_rehearsal',
       'test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR inet_server_addr()::text NOT IN
       ('127.0.0.1','127.0.0.1/32','::1','::1/128')
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
       r.rolname='teruisi_ai_budget_v11_publish_login' AND r.rolcanlogin
       AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
       AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE
       m.roleid='teruisi_ai_budget_v11_publish_login'::regrole OR
       m.member='teruisi_ai_budget_v11_publish_login'::regrole)
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR selected_request_sha !~ '^[0-9a-f]{64}$'
     OR selected_signed_sha !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_budget_v11_publish_v2_outcome_unavailable'; END IF;
  SELECT * INTO recorded FROM public.protected_business_budget_v11_publications_v2
    WHERE run_id=selected_run AND attempt=selected_attempt;
  IF recorded.id IS NULL THEN RETURN jsonb_build_object(
    'schemaVersion','budget-v11-signed-publication-outcome-v2',
    'status','absent_observed','publicationId',null,'retryAllowed',false,
    'readyAuthorized',false); END IF;
  IF recorded.request_digest IS DISTINCT FROM selected_request_sha
     OR recorded.signed_receipt_sha256 IS DISTINCT FROM selected_signed_sha
  THEN RETURN jsonb_build_object(
    'schemaVersion','budget-v11-signed-publication-outcome-v2',
    'status','conflict','publicationId',null,'retryAllowed',false,
    'readyAuthorized',false); END IF;
  RETURN jsonb_build_object('schemaVersion',
    'budget-v11-signed-publication-outcome-v2',
    'status','committed','publicationId',recorded.id::text,
    'requestDigest',recorded.request_digest,'retryAllowed',false,
    'readyAuthorized',false);
END $$"""


CHUNK_SQL=r"""CREATE FUNCTION public.ai_budget_v11_read_published_chunk_v2(
  selected_run text,selected_attempt integer,selected_volume integer,
  selected_format text,selected_sequence integer,selected_request_sha text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE published public.protected_business_budget_v11_publications_v2%ROWTYPE;
  signed public.protected_business_budget_v11_signed_receipts_v3%ROWTYPE;
  parent public.ai_business_file_runs%ROWTYPE;
  req jsonb; compact jsonb; descriptor jsonb; part public.ai_business_volume_chunks%ROWTYPE;
  current_root text; descriptor_count integer; expected_length integer;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_download_v2_login'
     OR current_database() NOT IN ('teruisi_ai_rehearsal',
       'test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR inet_server_addr()::text NOT IN
       ('127.0.0.1','127.0.0.1/32','::1','::1/128')
     OR current_setting('transaction_read_only')<>'on'
     OR current_setting('transaction_isolation') NOT IN
       ('repeatable read','serializable')
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
       r.rolname='teruisi_ai_budget_v11_download_v2_login'
       AND r.rolcanlogin AND NOT r.rolinherit AND NOT r.rolsuper
       AND NOT r.rolcreatedb AND NOT r.rolcreaterole
       AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE
       m.roleid='teruisi_ai_budget_v11_download_v2_login'::regrole OR
       m.member='teruisi_ai_budget_v11_download_v2_login'::regrole)
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR selected_volume NOT BETWEEN 0 AND 100
     OR selected_sequence NOT BETWEEN 1 AND 512
     OR NOT ((selected_volume=0 AND selected_format='json') OR
       (selected_volume>0 AND selected_format IN ('html','xlsx')))
     OR selected_request_sha IS NULL OR
       selected_request_sha !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_budget_v11_download_v2_unavailable'; END IF;
  SELECT * INTO published FROM public.protected_business_budget_v11_publications_v2
    WHERE run_id=selected_run AND attempt=selected_attempt;
  SELECT * INTO signed FROM public.protected_business_budget_v11_signed_receipts_v3
    WHERE id=published.signed_receipt_id;
  SELECT * INTO parent FROM public.ai_business_file_runs WHERE id=selected_run;
  IF published.id IS NULL OR signed.id IS NULL OR parent.id IS NULL
     OR published.request_digest IS DISTINCT FROM selected_request_sha
     OR published.signed_receipt_id IS DISTINCT FROM signed.id
     OR published.signed_receipt_sha256 IS DISTINCT FROM signed.receipt_sha256
     OR published.ticket_id IS DISTINCT FROM signed.ticket_id
     OR published.claim_id IS DISTINCT FROM signed.claim_id
     OR published.key_id IS DISTINCT FROM signed.key_id
     OR published.ledger_root IS DISTINCT FROM signed.ledger_root
     OR published.file_page_root IS DISTINCT FROM signed.file_page_root
     OR published.run_version IS DISTINCT FROM parent.version
     OR published.report_id IS DISTINCT FROM parent.report_id
     OR published.owner_email IS DISTINCT FROM parent.owner_email
     OR published.binding_digest IS DISTINCT FROM parent.binding_digest
     OR parent.renderer_version<>11 OR parent.draft
     OR parent.status<>'paused' OR parent.error_code<>'renderer_unpublished'
     OR parent.progress_json::jsonb IS DISTINCT FROM jsonb_build_object(
       'stage','staged_unpublished','attempt',selected_attempt)
     OR signed.receipt_sha256 IS DISTINCT FROM encode(sha256(convert_to(
       signed.receipt_text,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_budget_v11_download_v2_publication_drift'; END IF;
  req:=public.ai_budget_v11_download_requirements_v2(signed.ticket_id,
    signed.claim_id,parent.id,parent.attempt,signed.attestation_sha256);
  current_root:=public.ai_budget_v11_download_ledger_root_v2(signed.ticket_id,
    signed.claim_id,parent.id,parent.attempt,signed.attestation_sha256);
  IF current_root IS DISTINCT FROM published.ledger_root
     OR req->>'filePageRoot' IS DISTINCT FROM published.file_page_root
     OR req->>'ownerEmail' IS DISTINCT FROM published.owner_email
     OR req->>'workflowVersion' IS DISTINCT FROM
       signed.receipt_text::jsonb->>'workflowVersion'
     OR NOT public.ai_budget_v11_private_mac_valid_v4_ro(signed.key_id,
       signed.receipt_text,signed.receipt_mac)
  THEN RAISE EXCEPTION 'ai_budget_v11_download_v2_current_root_drift'; END IF;
  PERFORM public.ai_business_volume_manifest_check(parent.id,parent.attempt,
    parent.binding_digest,parent.draft,parent.manifest_json);
  compact:=parent.manifest_json::jsonb;
  IF compact->>'rendererVersion' IS DISTINCT FROM '11'
     OR compact->>'attempt' IS DISTINCT FROM selected_attempt::text
     OR compact->>'bindingDigest' IS DISTINCT FROM parent.binding_digest
  THEN RAISE EXCEPTION 'ai_budget_v11_download_v2_manifest_drift'; END IF;
  IF selected_volume=0 THEN
    descriptor:=compact->'manifestFile';
  ELSE
    SELECT count(*) INTO descriptor_count FROM jsonb_array_elements(
      compact->'files') item WHERE item->>'volumeIndex'=selected_volume::text
      AND item->>'format'=selected_format;
    IF descriptor_count<>1 THEN RAISE EXCEPTION
      'ai_budget_v11_download_v2_descriptor_ambiguous'; END IF;
    SELECT item INTO descriptor FROM jsonb_array_elements(compact->'files') item
      WHERE item->>'volumeIndex'=selected_volume::text
        AND item->>'format'=selected_format;
  END IF;
  IF descriptor IS NULL OR descriptor->>'volumeIndex' IS DISTINCT FROM
       selected_volume::text OR descriptor->>'format' IS DISTINCT FROM
       selected_format OR selected_sequence>(descriptor->>'chunkCount')::integer
  THEN RAISE EXCEPTION 'ai_budget_v11_download_v2_descriptor_invalid'; END IF;
  SELECT * INTO part FROM public.ai_business_volume_chunks WHERE
    run_id=selected_run AND attempt=selected_attempt
    AND volume_index=selected_volume AND format=selected_format
    AND sequence=selected_sequence;
  expected_length:=LEAST(524288,(descriptor->>'bytes')::integer-
    (selected_sequence-1)*524288);
  IF part.id IS NULL OR expected_length<1 OR
     octet_length(part.content) IS DISTINCT FROM expected_length OR
     octet_length(part.content)>524288 OR
     encode(sha256(part.content),'hex') IS DISTINCT FROM part.content_digest
  THEN RAISE EXCEPTION 'ai_budget_v11_download_v2_chunk_invalid'; END IF;
  RETURN jsonb_build_object('schemaVersion','budget-v11-published-chunk-v2',
    'runId',selected_run,'attempt',selected_attempt,
    'volumeIndex',selected_volume,'format',selected_format,
    'sequence',selected_sequence,'bytes',octet_length(part.content),
    'sha256',part.content_digest,'fileSha256',descriptor->>'sha256',
    'publicationId',published.id::text,
    'publicationRequestDigest',published.request_digest,
    'contentBase64',replace(encode(part.content,'base64'),chr(10),''),
    'candidateOnly',true,'readyAuthorized',false,'releaseAllowed',false);
END $$"""
