"""Short-lived v4 seal ticket and one-time claim; no ticketed reader or publisher yet."""
from django.db import migrations, models


TICKETS = "public.ai_business_v4_seal_tickets"
CLAIMS = "public.ai_business_v4_seal_claims"
SEALER = "teruisi_ai_seal_writer"

SOURCE_ROOT = """CREATE FUNCTION public.ai_v4_seal_ticket_source_root(selected_run text)
RETURNS TABLE(root_digest text,source_count bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public SET TimeZone='UTC' AS $$
DECLARE completed bigint; distinct_ordinals bigint;
  first_ordinal integer; last_ordinal integer;
  finance_count bigint; netshop_count bigint; current_count bigint; source_bytes bigint;
BEGIN
  SELECT count(*),count(*) FILTER (WHERE source.finished),
    count(DISTINCT source.ordinal),
    min(source.ordinal),max(source.ordinal),
    count(*) FILTER (WHERE source.domain='finance'),
    count(*) FILTER (WHERE source.domain='netshop'),
    count(*) FILTER (WHERE source.domain='netshop' AND
      source.query_json::jsonb->>'window'='current'),
    coalesce(sum(octet_length(source.query_json)+
      octet_length(source.checkpoint_json)),0),
    encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(source)
      ORDER BY source.ordinal)::text,'[]'),'UTF8')),'hex')
    INTO source_count,completed,distinct_ordinals,first_ordinal,last_ordinal,
      finance_count,netshop_count,current_count,source_bytes,root_digest
    FROM public.ai_business_v4_sources source WHERE source.run_id=selected_run;
  IF source_count NOT BETWEEN 2 AND 4 OR completed<>source_count
     OR distinct_ordinals<>source_count
     OR first_ordinal<>1 OR last_ordinal<>source_count
     OR finance_count<>1 OR netshop_count NOT BETWEEN 1 AND 3
     OR current_count<>1 OR source_bytes>262144
  THEN RAISE EXCEPTION 'ai_v4_ticket_source_root_invalid'; END IF;
  RETURN NEXT;
END $$"""

IMMUTABLE = """CREATE FUNCTION public.ai_v4_seal_ticket_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE expected_session text;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_v4_ticket_immutable'; END IF;
  expected_session:=CASE WHEN TG_TABLE_NAME='ai_business_v4_seal_tickets'
    THEN 'teruisi_ai_writer' ELSE 'teruisi_ai_seal_writer' END;
  IF session_user<>expected_session OR current_user IS DISTINCT FROM
      pg_catalog.pg_get_userbyid((SELECT c.relowner FROM pg_catalog.pg_class c
        WHERE c.oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_v4_ticket_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

NO_TRUNCATE = """CREATE FUNCTION public.ai_v4_seal_ticket_no_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  -- Django's isolated TransactionTestCase flush and owner-run maintenance
  -- require a trusted owner path. Runtime reader/writer/sealer roles never own
  -- these tables and receive no TRUNCATE grant.
  IF current_user=pg_catalog.pg_get_userbyid((SELECT c.relowner
       FROM pg_catalog.pg_class c WHERE c.oid=TG_RELID))
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.rolname=current_user AND r.rolsuper)
  THEN RETURN NULL; END IF;
  RAISE EXCEPTION 'ai_v4_ticket_truncate_denied';
END $$"""

ISSUE = """CREATE FUNCTION public.ai_v4_issue_seal_ticket(
  selected_run text,selected_attempt text,selected_actor text,
  selected_actor_version bigint,selected_parent_version bigint,
  selected_directory_digest text,selected_request_digest text)
RETURNS TABLE(ticket_id uuid,nonce text,expires_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  root text; sources bigint; cutoff timestamptz; guard_text text;
  issued timestamptz;
BEGIN
  IF session_user<>'teruisi_ai_writer' OR selected_request_digest !~ '^[0-9a-f]{64}$'
     OR selected_directory_digest !~ '^[0-9a-f]{64}$'
     OR selected_actor_version<1 OR selected_parent_version<1
  THEN RAISE EXCEPTION 'ai_v4_ticket_issue_role_or_request_invalid'; END IF;
  SELECT probe.guard_version,probe.guard_installed_at INTO guard_text,cutoff
    FROM public.ai_v4_lock_source_revisions_for_admission() probe;
  IF guard_text IS DISTINCT FROM 'business-v4-source-write-fence-read-v1'
     OR cutoff IS NULL
  THEN RAISE EXCEPTION 'ai_v4_ticket_source_fence_missing'; END IF;
  SELECT * INTO parent FROM public.ai_business_v4_runs
    WHERE id=selected_run FOR UPDATE;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts
    WHERE id=selected_attempt;
  IF parent.id IS NULL OR attempt.id IS NULL
     OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR parent.scope_json<>'null' OR parent.version<>selected_parent_version
     OR parent.owner_email<>selected_actor OR attempt.run_id<>parent.id
     OR attempt.run_version<>parent.version
     OR attempt.actor_email<>selected_actor
     OR attempt.actor_version<>selected_actor_version
     OR attempt.plan_digest<>parent.plan_digest
     OR attempt.directory_digest<>selected_directory_digest
     OR attempt.created_at<=cutoff
     OR (SELECT latest.id FROM public.ai_business_v4_validation_attempts latest
       WHERE latest.run_id=parent.id ORDER BY latest.created_at DESC,latest.id DESC
       LIMIT 1) IS DISTINCT FROM attempt.id
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users account
       WHERE account.email=selected_actor AND account.role='admin'
         AND account.status='active' AND account.scope IS NULL
         AND account.version=selected_actor_version)
     OR EXISTS (SELECT 1 FROM public.ai_business_v4_seals seal
       WHERE seal.run_id=parent.id)
  THEN RAISE EXCEPTION 'ai_v4_ticket_issue_binding_invalid'; END IF;
  SELECT actual.root_digest,actual.source_count INTO root,sources
    FROM public.ai_v4_seal_ticket_source_root(parent.id) actual;
  issued:=clock_timestamp();
  IF EXISTS (SELECT 1 FROM public.ai_business_v4_seal_tickets ticket
       LEFT JOIN public.ai_business_v4_seal_claims claim
         ON claim.ticket_id=ticket.id
       WHERE ticket.run_id=parent.id AND ticket.attempt_id=attempt.id
         AND (ticket.expires_at>issued OR claim.lease_until>issued))
  THEN RAISE EXCEPTION 'ai_v4_ticket_active_attempt_exists'; END IF;
  ticket_id:=gen_random_uuid();
  nonce:=replace(gen_random_uuid()::text,'-','')||
    replace(gen_random_uuid()::text,'-','');
  expires_at:=issued+interval '60 seconds';
  INSERT INTO public.ai_business_v4_seal_tickets
    (id,run_id,attempt_id,actor_email,actor_version,parent_version,
     plan_digest,directory_digest,source_root,source_count,request_digest,
     nonce_hash,issued_at,expires_at)
    VALUES (ticket_id,parent.id,attempt.id,selected_actor,selected_actor_version,
      parent.version,parent.plan_digest,selected_directory_digest,root,sources,
      selected_request_digest,
      encode(sha256(convert_to('v4-seal-ticket-v1:'||nonce,'UTF8')),'hex'),
      issued,expires_at);
  RETURN NEXT;
END $$"""

CLAIM = """CREATE FUNCTION public.ai_v4_claim_seal_ticket(
  selected_run text,selected_attempt text,selected_actor text,
  selected_actor_version bigint,selected_nonce text)
RETURNS TABLE(ticket_id uuid,claim_token text,lease_until timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE ticket public.ai_business_v4_seal_tickets%ROWTYPE;
  parent public.ai_business_v4_runs%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  root text; sources bigint; guard_text text; cutoff timestamptz;
  claimed timestamptz;
BEGIN
  IF session_user<>'teruisi_ai_seal_writer'
     OR selected_nonce !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_v4_ticket_claim_role_or_nonce_invalid'; END IF;
  SELECT probe.guard_version,probe.guard_installed_at INTO guard_text,cutoff
    FROM public.ai_v4_lock_source_revisions_for_admission() probe;
  IF guard_text IS DISTINCT FROM 'business-v4-source-write-fence-read-v1'
     OR cutoff IS NULL
  THEN RAISE EXCEPTION 'ai_v4_ticket_source_fence_missing'; END IF;
  SELECT * INTO ticket FROM public.ai_business_v4_seal_tickets selected_ticket
    WHERE selected_ticket.nonce_hash=encode(sha256(convert_to(
      'v4-seal-ticket-v1:'||selected_nonce,'UTF8')),'hex') FOR UPDATE;
  SELECT * INTO parent FROM public.ai_business_v4_runs
    WHERE id=selected_run FOR UPDATE;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts
    WHERE id=selected_attempt;
  claimed:=clock_timestamp();
  IF ticket.id IS NULL OR parent.id IS NULL OR attempt.id IS NULL
     OR ticket.run_id<>selected_run OR ticket.attempt_id<>selected_attempt
     OR ticket.actor_email<>selected_actor
     OR ticket.actor_version<>selected_actor_version
     OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR parent.scope_json<>'null' OR parent.owner_email<>selected_actor
     OR parent.version<>ticket.parent_version
     OR parent.plan_digest<>ticket.plan_digest
     OR attempt.run_id<>parent.id OR attempt.run_version<>parent.version
     OR attempt.actor_email<>selected_actor
     OR attempt.actor_version<>selected_actor_version
     OR attempt.plan_digest<>parent.plan_digest
     OR attempt.directory_digest<>ticket.directory_digest
     OR attempt.created_at<=cutoff OR claimed>=ticket.expires_at
     OR EXISTS (SELECT 1 FROM public.ai_business_v4_seal_claims prior
       WHERE prior.ticket_id=ticket.id)
     OR EXISTS (SELECT 1 FROM public.ai_business_v4_seals seal
       WHERE seal.run_id=parent.id)
     OR (SELECT latest.id FROM public.ai_business_v4_validation_attempts latest
       WHERE latest.run_id=parent.id ORDER BY latest.created_at DESC,latest.id DESC
       LIMIT 1) IS DISTINCT FROM attempt.id
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users account
       WHERE account.email=selected_actor AND account.role='admin'
         AND account.status='active' AND account.scope IS NULL
         AND account.version=selected_actor_version)
  THEN RAISE EXCEPTION 'ai_v4_ticket_claim_binding_invalid'; END IF;
  SELECT actual.root_digest,actual.source_count INTO root,sources
    FROM public.ai_v4_seal_ticket_source_root(parent.id) actual;
  IF root IS DISTINCT FROM ticket.source_root
     OR sources IS DISTINCT FROM ticket.source_count
  THEN RAISE EXCEPTION 'ai_v4_ticket_claim_directory_drift'; END IF;
  ticket_id:=ticket.id;
  claim_token:=replace(gen_random_uuid()::text,'-','')||
    replace(gen_random_uuid()::text,'-','');
  lease_until:=claimed+interval '180 seconds';
  INSERT INTO public.ai_business_v4_seal_claims
    (ticket_id,claim_hash,claimed_at,lease_until)
    VALUES (ticket.id,encode(sha256(convert_to(
      'v4-seal-claim-v1:'||claim_token,'UTF8')),'hex'),claimed,lease_until);
  RETURN NEXT;
END $$"""

FUNCTIONS = (
    "public.ai_v4_seal_ticket_source_root(text)",
    "public.ai_v4_seal_ticket_immutable()",
    "public.ai_v4_seal_ticket_no_truncate()",
    "public.ai_v4_issue_seal_ticket(text,text,text,bigint,bigint,text,text)",
    "public.ai_v4_claim_seal_ticket(text,text,text,bigint,text)",
)
OLD_SEALER_EXECUTE = (
    "public.ai_v4_sealer_read_context(text,text,text,bigint)",
    "public.ai_v4_sealer_read_segment(text,text,text,integer,text,bigint)",
    "public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)",
    "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)",
    "public.ai_v4_lock_source_revisions_for_admission()",
)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname='teruisi_ai_seal_writer'")
        if cursor.fetchone() != (False, False, False, False, False, False, False):
            raise RuntimeError("0041 requires the unchanged NOLOGIN seal-writer role")
        cursor.execute("""CREATE TABLE public.ai_business_v4_seal_tickets (
          id uuid PRIMARY KEY, run_id varchar(160) NOT NULL
            REFERENCES public.ai_business_v4_runs(id) ON DELETE RESTRICT,
          attempt_id varchar(160) NOT NULL
            REFERENCES public.ai_business_v4_validation_attempts(id) ON DELETE RESTRICT,
          actor_email varchar(320) NOT NULL, actor_version bigint NOT NULL,
          parent_version bigint NOT NULL, plan_digest varchar(64) NOT NULL,
          directory_digest varchar(64) NOT NULL, source_root varchar(64) NOT NULL,
          source_count integer NOT NULL, request_digest varchar(64) NOT NULL UNIQUE,
          nonce_hash varchar(64) NOT NULL UNIQUE,
          issued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
          CONSTRAINT ai_v4_ticket_period_ck CHECK (expires_at>issued_at AND
            expires_at<=issued_at+interval '60 seconds'),
          CONSTRAINT ai_v4_ticket_count_ck CHECK (source_count BETWEEN 2 AND 4),
          CONSTRAINT ai_v4_ticket_versions_ck CHECK (
            actor_version>=1 AND parent_version>=1)
        )""")
        cursor.execute("""CREATE TABLE public.ai_business_v4_seal_claims (
          ticket_id uuid PRIMARY KEY REFERENCES public.ai_business_v4_seal_tickets(id)
            ON DELETE RESTRICT,
          claim_hash varchar(64) NOT NULL UNIQUE,
          claimed_at timestamptz NOT NULL, lease_until timestamptz NOT NULL,
          CONSTRAINT ai_v4_claim_period_ck CHECK (lease_until>claimed_at AND
            lease_until<=claimed_at+interval '180 seconds')
        )""")
        cursor.execute("CREATE INDEX ai_v4_ticket_run_attempt_idx ON " + TICKETS +
            " (run_id,attempt_id,expires_at)")
        for name in (TICKETS, CLAIMS):
            cursor.execute("REVOKE ALL ON " + name + " FROM PUBLIC")
            for role in ("teruisi_ai_writer", "teruisi_ai_reader", SEALER):
                cursor.execute("SELECT to_regrole(%s)", [role])
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE ALL ON " + name + " FROM " + role)
        for definition in (SOURCE_ROOT, IMMUTABLE, NO_TRUNCATE, ISSUE, CLAIM):
            cursor.execute(definition)
        for name in (TICKETS, CLAIMS):
            cursor.execute("CREATE TRIGGER ai_v4_ticket_immutable BEFORE INSERT OR "
                "UPDATE OR DELETE ON " + name + " FOR EACH ROW EXECUTE FUNCTION "
                "public.ai_v4_seal_ticket_immutable()")
            cursor.execute("CREATE TRIGGER ai_v4_ticket_no_truncate BEFORE TRUNCATE "
                "ON " + name + " FOR EACH STATEMENT EXECUTE FUNCTION "
                "public.ai_v4_seal_ticket_no_truncate()")
        for signature in FUNCTIONS:
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
        cursor.execute("SELECT to_regrole('teruisi_ai_writer')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("GRANT EXECUTE ON FUNCTION " + FUNCTIONS[3] +
                " TO teruisi_ai_writer")
        cursor.execute("GRANT EXECUTE ON FUNCTION " + FUNCTIONS[4] +
            " TO " + SEALER)
        for signature in OLD_SEALER_EXECUTE:
            cursor.execute("REVOKE EXECUTE ON FUNCTION " + signature +
                " FROM " + SEALER)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TICKETS + ") OR "
            "EXISTS(SELECT 1 FROM " + CLAIMS + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0041 cannot discard issued or claimed seal tickets")
        cursor.execute("SELECT rolcanlogin FROM pg_catalog.pg_roles "
            "WHERE rolname='teruisi_ai_seal_writer'")
        if cursor.fetchone() != (False,):
            raise RuntimeError("0041 rollback requires the unchanged NOLOGIN sealer")
        for name in (TICKETS, CLAIMS):
            cursor.execute("DROP TRIGGER ai_v4_ticket_no_truncate ON " + name)
            cursor.execute("DROP TRIGGER ai_v4_ticket_immutable ON " + name)
        for signature in reversed(FUNCTIONS):
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute("DROP TABLE " + CLAIMS)
        cursor.execute("DROP TABLE " + TICKETS)
        for signature in OLD_SEALER_EXECUTE:
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                " TO " + SEALER)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0040_business_v4_sealer_narrow_stream")]
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
        migrations.CreateModel(name="AiBusinessV4SealTicket", fields=[
            ("id", models.UUIDField(primary_key=True, serialize=False)),
            ("run_id", models.CharField(max_length=160)),
            ("attempt_id", models.CharField(max_length=160)),
            ("actor_email", models.CharField(max_length=320)),
            ("actor_version", models.BigIntegerField()),
            ("parent_version", models.BigIntegerField()),
            ("plan_digest", models.CharField(max_length=64)),
            ("directory_digest", models.CharField(max_length=64)),
            ("source_root", models.CharField(max_length=64)),
            ("source_count", models.IntegerField()),
            ("request_digest", models.CharField(max_length=64)),
            ("nonce_hash", models.CharField(max_length=64)),
            ("issued_at", models.DateTimeField()),
            ("expires_at", models.DateTimeField()),
        ], options={"db_table": "ai_business_v4_seal_tickets"}),
        migrations.CreateModel(name="AiBusinessV4SealClaim", fields=[
            ("ticket_id", models.UUIDField(primary_key=True, serialize=False)),
            ("claim_hash", models.CharField(max_length=64)),
            ("claimed_at", models.DateTimeField()),
            ("lease_until", models.DateTimeField()),
        ], options={"db_table": "ai_business_v4_seal_claims"}),
        ]),
        migrations.RunPython(install, uninstall),
    ]
