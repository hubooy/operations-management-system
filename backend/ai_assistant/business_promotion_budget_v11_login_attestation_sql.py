"""0073 independent LOGIN-capable v11 proof; publication remains closed.

The original 0067 proof, table, functions and role remain byte-for-byte
unchanged. This sidecar starts with a NOLOGIN role and has no credential loader.
"""

from __future__ import annotations

from importlib import import_module


ROLE = "teruisi_ai_budget_v11_attestor_v2_login"
TABLE = "public.protected_business_budget_v11_login_attestations"
GUARD_SIGNATURE = "public.ai_budget_v11_login_attestation_guard()"
REQUIREMENTS_SIGNATURE = (
    "public.ai_budget_v11_login_attestation_requirements(text,integer,text,text)")
ATTEST_SIGNATURE = "public.ai_budget_v11_attest_staged_login_v2(text,integer,text)"
OUTCOME_SIGNATURE = "public.ai_budget_v11_attest_outcome_login_v2(text,integer,text)"


def _replace(source: str, old: str, new: str, count: int = 1) -> str:
    if source.count(old) != count:
        raise RuntimeError("0073 requires exact frozen 0067 SQL predecessor")
    return source.replace(old, new)


def _old():
    return import_module(
        "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")


def requirements_sql() -> str:
    old = _old()
    return _replace(_replace(old.REQUIREMENTS,
        "ai_budget_v11_attestation_requirements(",
        "ai_budget_v11_login_attestation_requirements("),
        "teruisi_ai_budget_v11_attestor", ROLE)


def guard_sql() -> str:
    old = _old()
    return _replace(_replace(old.GUARD,
        "ai_budget_v11_attestation_guard()",
        "ai_budget_v11_login_attestation_guard()"),
        "teruisi_ai_budget_v11_attestor", ROLE)


def attest_sql() -> str:
    old = _old()
    body = old.ATTEST
    for needle, replacement, count in (
            ("ai_budget_v11_attest_staged(",
             "ai_budget_v11_attest_staged_login_v2(", 1),
            ("ai_budget_v11_attestation_requirements(",
             "ai_budget_v11_login_attestation_requirements(", 1),
            ("teruisi_ai_budget_v11_attestor", ROLE, 4),
            ("ai_business_promotion_budget_v11_attestations",
             "protected_business_budget_v11_login_attestations", 4),
            ("AND NOT r.rolcanlogin", "AND r.rolcanlogin", 1),
            ("receipt_id:=encode(sha256(convert_to(parent.id||':'||",
             "receipt_id:=encode(sha256(convert_to("
             "'teruisi:budget-v11:login-attestation:v2:'||parent.id||':'||", 1)):
        body = _replace(body, needle, replacement, count)
    return body


OUTCOME_SQL = r"""CREATE FUNCTION public.ai_budget_v11_attest_outcome_login_v2(
  selected_run text, selected_attempt integer, selected_attestation_sha text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE recorded public.protected_business_budget_v11_login_attestations%ROWTYPE;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_attestor_v2_login'
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR selected_attestation_sha IS NULL
     OR selected_attestation_sha !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.rolname='teruisi_ai_budget_v11_attestor_v2_login'
         AND r.rolcanlogin AND NOT r.rolinherit AND NOT r.rolsuper
         AND NOT r.rolcreatedb AND NOT r.rolcreaterole
         AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members membership
       WHERE membership.roleid=
         'teruisi_ai_budget_v11_attestor_v2_login'::regrole
          OR membership.member=
         'teruisi_ai_budget_v11_attestor_v2_login'::regrole)
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid=
         'public.protected_business_budget_v11_login_attestations'::regclass))
  THEN RAISE EXCEPTION 'ai_budget_v11_login_attestor_unavailable'; END IF;
  SELECT * INTO recorded FROM public.protected_business_budget_v11_login_attestations item
    WHERE item.run_id=selected_run AND item.attempt=selected_attempt;
  IF recorded.id IS NULL THEN
    RETURN jsonb_build_object('schemaVersion','budget-v11-login-attest-outcome-v2',
      'status','absent_observed','runId',selected_run,
      'attempt',selected_attempt,'receiptId',null,'retryAllowed',false);
  END IF;
  IF recorded.attestation_sha256 IS DISTINCT FROM selected_attestation_sha
     OR encode(sha256(convert_to(recorded.attestation_json,'UTF8')),'hex')
       IS DISTINCT FROM recorded.attestation_sha256
  THEN
    RETURN jsonb_build_object('schemaVersion','budget-v11-login-attest-outcome-v2',
      'status','conflict','runId',selected_run,
      'attempt',selected_attempt,'receiptId',null,'retryAllowed',false);
  END IF;
  RETURN jsonb_build_object('schemaVersion','budget-v11-login-attest-outcome-v2',
    'status','committed','runId',selected_run,'attempt',selected_attempt,
    'receiptId',recorded.id,'retryAllowed',false);
END $$"""


SIGNATURES = (GUARD_SIGNATURE, REQUIREMENTS_SIGNATURE,
    ATTEST_SIGNATURE, OUTCOME_SIGNATURE)
