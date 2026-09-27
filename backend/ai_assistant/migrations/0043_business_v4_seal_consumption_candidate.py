"""Default-closed v4 seal consumption ledger; no commit wrapper or MAC authority."""
from django.db import migrations, models


TABLE = "public.ai_business_v4_seal_consumptions"
SEALER = "teruisi_ai_seal_writer"

CONSUMPTION_GUARD = """CREATE FUNCTION public.ai_v4_seal_consumption_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE ticket public.ai_business_v4_seal_tickets%ROWTYPE;
  claim public.ai_business_v4_seal_claims%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  seal public.ai_business_v4_seals%ROWTYPE;
  parent public.ai_business_v4_runs%ROWTYPE;
  root text; sources bigint;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_v4_consumption_immutable'; END IF;
  IF session_user<>'teruisi_ai_seal_writer'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_v4_consumption_direct_write_denied'; END IF;
  SELECT * INTO ticket FROM public.ai_business_v4_seal_tickets t
    WHERE t.id=NEW.ticket_id;
  SELECT * INTO claim FROM public.ai_business_v4_seal_claims c
    WHERE c.ticket_id=NEW.ticket_id;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts a
    WHERE a.id=NEW.attempt_id;
  SELECT * INTO seal FROM public.ai_business_v4_seals s
    WHERE s.run_id=NEW.run_id;
  SELECT * INTO parent FROM public.ai_business_v4_runs p
    WHERE p.id=NEW.run_id;
  IF ticket.id IS NULL OR claim.ticket_id IS NULL OR attempt.id IS NULL
     OR seal.run_id IS NULL OR parent.id IS NULL
     OR ticket.run_id<>NEW.run_id OR ticket.attempt_id<>NEW.attempt_id
     OR ticket.request_digest<>NEW.request_digest
     OR ticket.actor_email<>parent.owner_email
     OR attempt.run_id<>parent.id
     OR attempt.run_version<>ticket.parent_version
     OR attempt.actor_email<>ticket.actor_email
     OR attempt.actor_version<>ticket.actor_version
     OR attempt.plan_digest<>ticket.plan_digest
     OR attempt.directory_digest<>ticket.directory_digest
     OR attempt.key_id<>seal.key_id
     OR (SELECT latest.id FROM public.ai_business_v4_validation_attempts latest
       WHERE latest.run_id=parent.id ORDER BY latest.created_at DESC,latest.id DESC
       LIMIT 1) IS DISTINCT FROM attempt.id
     OR seal.attempt_id<>NEW.attempt_id
     OR seal.evidence_version<>NEW.evidence_version
     OR seal.body_digest<>NEW.body_digest
     OR parent.status<>'sealed' OR parent.version<>NEW.evidence_version
     OR ticket.parent_version<>NEW.evidence_version-1
     OR ticket.plan_digest<>parent.plan_digest
     OR claim.claimed_at<ticket.issued_at
     OR claim.claimed_at>=ticket.expires_at
     OR NEW.consumed_at<claim.claimed_at
     OR NEW.consumed_at>claim.lease_until
     OR clock_timestamp()>=claim.lease_until
     OR NEW.consumed_at<clock_timestamp()-interval '5 seconds'
     OR NEW.consumed_at>clock_timestamp()+interval '1 second'
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users account
       WHERE account.email=ticket.actor_email AND account.role='admin'
         AND account.status='active' AND account.scope IS NULL
         AND account.version=ticket.actor_version)
  THEN RAISE EXCEPTION 'ai_v4_consumption_binding_invalid'; END IF;
  SELECT actual.root_digest,actual.source_count INTO root,sources
    FROM public.ai_v4_seal_ticket_source_root(parent.id) actual;
  IF root IS DISTINCT FROM ticket.source_root
     OR sources IS DISTINCT FROM ticket.source_count
  THEN RAISE EXCEPTION 'ai_v4_consumption_source_drift'; END IF;
  RETURN NEW;
END $$"""

REQUIRE_CONSUMPTION = """CREATE FUNCTION public.ai_v4_seal_requires_consumption() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.ai_business_v4_seal_consumptions consumption
      WHERE consumption.run_id=NEW.run_id
        AND consumption.attempt_id=NEW.attempt_id
        AND consumption.evidence_version=NEW.evidence_version
        AND consumption.body_digest=NEW.body_digest)
  THEN RAISE EXCEPTION 'ai_v4_seal_consumption_missing'; END IF;
  RETURN NULL;
END $$"""

RESULT = """CREATE FUNCTION public.ai_v4_sealer_consumption_result(
  selected_run text,selected_attempt text,selected_request_digest text,
  selected_nonce text,selected_claim text)
RETURNS TABLE(run_id text,attempt_id text,evidence_version bigint,
  body_digest text,consumed_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE ticket public.ai_business_v4_seal_tickets%ROWTYPE;
  claim public.ai_business_v4_seal_claims%ROWTYPE;
  result public.ai_business_v4_seal_consumptions%ROWTYPE;
  seal public.ai_business_v4_seals%ROWTYPE;
  parent public.ai_business_v4_runs%ROWTYPE;
BEGIN
  IF session_user<>'teruisi_ai_seal_writer'
     OR selected_request_digest !~ '^[0-9a-f]{64}$'
     OR selected_nonce !~ '^[0-9a-f]{64}$'
     OR selected_claim !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_v4_seal_result_unavailable'; END IF;
  SELECT * INTO ticket FROM public.ai_business_v4_seal_tickets t
    WHERE t.nonce_hash=encode(sha256(convert_to(
      'v4-seal-ticket-v1:'||selected_nonce,'UTF8')),'hex');
  SELECT * INTO claim FROM public.ai_business_v4_seal_claims c
    WHERE c.ticket_id=ticket.id AND c.claim_hash=encode(sha256(convert_to(
      'v4-seal-claim-v1:'||selected_claim,'UTF8')),'hex');
  SELECT * INTO result FROM public.ai_business_v4_seal_consumptions c
    WHERE c.ticket_id=ticket.id;
  SELECT * INTO seal FROM public.ai_business_v4_seals s
    WHERE s.run_id=selected_run;
  SELECT * INTO parent FROM public.ai_business_v4_runs p
    WHERE p.id=selected_run;
  IF ticket.id IS NULL OR claim.ticket_id IS NULL OR result.ticket_id IS NULL
     OR seal.run_id IS NULL OR parent.id IS NULL
     OR ticket.run_id<>selected_run OR ticket.attempt_id<>selected_attempt
     OR ticket.request_digest<>selected_request_digest
     OR result.run_id<>selected_run OR result.attempt_id<>selected_attempt
     OR result.request_digest<>selected_request_digest
     OR result.evidence_version<>seal.evidence_version
     OR result.body_digest<>seal.body_digest
     OR parent.status<>'sealed' OR parent.version<>seal.evidence_version
  THEN RAISE EXCEPTION 'ai_v4_seal_result_unavailable'; END IF;
  run_id:=result.run_id; attempt_id:=result.attempt_id;
  evidence_version:=result.evidence_version;
  body_digest:=result.body_digest; consumed_at:=result.consumed_at;
  RETURN NEXT;
END $$"""

VERIFY_CONSUMPTION = """CREATE FUNCTION public.ai_v4_verify_seal_consumption(
  selected_run text,selected_attempt text,selected_version bigint,
  selected_body_digest text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  IF session_user<>'teruisi_ai_writer' AND session_user<>current_user
  THEN RAISE EXCEPTION 'ai_v4_consumption_verify_role_denied'; END IF;
  RETURN EXISTS (
    SELECT 1 FROM public.ai_business_v4_seal_consumptions consumption
    JOIN public.ai_business_v4_seal_tickets ticket
      ON ticket.id=consumption.ticket_id
    JOIN public.ai_business_v4_seal_claims claim
      ON claim.ticket_id=ticket.id
    JOIN public.ai_business_v4_seals seal
      ON seal.run_id=consumption.run_id
    WHERE consumption.run_id=selected_run
      AND consumption.attempt_id=selected_attempt
      AND consumption.evidence_version=selected_version
      AND consumption.body_digest=selected_body_digest
      AND ticket.run_id=consumption.run_id
      AND ticket.attempt_id=consumption.attempt_id
      AND ticket.request_digest=consumption.request_digest
      AND seal.attempt_id=consumption.attempt_id
      AND seal.evidence_version=consumption.evidence_version
      AND seal.body_digest=consumption.body_digest);
END $$"""

SEAL_TRIGGER = """CREATE CONSTRAINT TRIGGER ai_v4_seal_consumption_required
AFTER INSERT ON public.ai_business_v4_seals
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION public.ai_v4_seal_requires_consumption()"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname='teruisi_ai_seal_writer'")
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0043 requires unchanged NOLOGIN sealer")
        cursor.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',"
            "'public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)',"
            "'EXECUTE')")
        if cursor.fetchone() != (False,):
            raise RuntimeError("0043 direct seal commit must remain revoked")
        cursor.execute("""CREATE TABLE public.ai_business_v4_seal_consumptions (
          ticket_id uuid PRIMARY KEY REFERENCES public.ai_business_v4_seal_tickets(id)
            ON DELETE RESTRICT,
          run_id varchar(160) NOT NULL UNIQUE REFERENCES public.ai_business_v4_seals(run_id)
            ON DELETE RESTRICT,
          attempt_id varchar(160) NOT NULL,
          request_digest varchar(64) NOT NULL UNIQUE,
          evidence_version bigint NOT NULL CHECK (evidence_version>=1),
          body_digest varchar(64) NOT NULL,
          consumed_at timestamptz NOT NULL
        )""")
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        for role in ("teruisi_ai_reader", "teruisi_ai_writer", SEALER):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + TABLE + " FROM " + role)
        for definition in (CONSUMPTION_GUARD, REQUIRE_CONSUMPTION, RESULT,
                           VERIFY_CONSUMPTION):
            cursor.execute(definition)
        for signature in (
            "public.ai_v4_seal_consumption_guard()",
            "public.ai_v4_seal_requires_consumption()",
            "public.ai_v4_sealer_consumption_result(text,text,text,text,text)",
            "public.ai_v4_verify_seal_consumption(text,text,bigint,text)",
        ):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
        cursor.execute("GRANT EXECUTE ON FUNCTION "
            "public.ai_v4_sealer_consumption_result(text,text,text,text,text) "
            "TO " + SEALER)
        cursor.execute("SELECT to_regrole('teruisi_ai_writer')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("GRANT EXECUTE ON FUNCTION "
                "public.ai_v4_verify_seal_consumption(text,text,bigint,text) "
                "TO teruisi_ai_writer")
        cursor.execute("CREATE TRIGGER ai_v4_consumption_state BEFORE INSERT OR "
            "UPDATE OR DELETE ON " + TABLE + " FOR EACH ROW EXECUTE FUNCTION "
            "public.ai_v4_seal_consumption_guard()")
        cursor.execute("CREATE TRIGGER ai_v4_ticket_immutable BEFORE INSERT OR "
            "UPDATE OR DELETE ON " + TABLE + " FOR EACH ROW EXECUTE FUNCTION "
            "public.ai_v4_seal_ticket_immutable()")
        cursor.execute("CREATE TRIGGER ai_v4_ticket_no_truncate BEFORE TRUNCATE "
            "ON " + TABLE + " FOR EACH STATEMENT EXECUTE FUNCTION "
            "public.ai_v4_seal_ticket_no_truncate()")
        cursor.execute(SEAL_TRIGGER)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0043 cannot discard a consumed seal receipt")
        cursor.execute("DROP TRIGGER ai_v4_seal_consumption_required "
            "ON public.ai_business_v4_seals")
        for name in ("ai_v4_ticket_no_truncate", "ai_v4_ticket_immutable",
                     "ai_v4_consumption_state"):
            cursor.execute("DROP TRIGGER " + name + " ON " + TABLE)
        for signature in (
            "public.ai_v4_verify_seal_consumption(text,text,bigint,text)",
            "public.ai_v4_sealer_consumption_result(text,text,text,text,text)",
            "public.ai_v4_seal_requires_consumption()",
            "public.ai_v4_seal_consumption_guard()",
        ):
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute("DROP TABLE " + TABLE)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0042_business_v4_claimed_read")]
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.CreateModel(name="AiBusinessV4SealConsumption", fields=[
                ("ticket_id", models.UUIDField(primary_key=True, serialize=False)),
                ("run_id", models.CharField(max_length=160)),
                ("attempt_id", models.CharField(max_length=160)),
                ("request_digest", models.CharField(max_length=64)),
                ("evidence_version", models.BigIntegerField()),
                ("body_digest", models.CharField(max_length=64)),
                ("consumed_at", models.DateTimeField()),
            ], options={"db_table": "ai_business_v4_seal_consumptions"}),
        ]),
        migrations.RunPython(install, uninstall),
    ]
