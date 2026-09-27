"""Targeted isolated PostgreSQL checks for the candidate-only 0055 sidecar."""
from importlib import import_module

import psycopg
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis.period_bound_plan_v1 import prepare_candidate
from business_analysis.test_period_bound_plan_v1 import plan as make_plan
from sales.auth import Principal

from . import business_v4_period_plan_candidate as owning, models as m
from .v4_period_plan_catalog import verify as verify_catalog
from .control_models import AiDataRevision
from .policy import AiError, canonical, digest, uid
from .test_business_v4_seal_ticket import BusinessV4SealTicketTests as fixture


class BusinessV4PeriodPlanCatalogTests(TransactionTestCase):
    def test_closed_catalog_rejects_writer_function_acl_drift(self):
        with connection.cursor() as cursor:
            verify_catalog(cursor)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("REVOKE EXECUTE ON FUNCTION "
                "public.ai_v4_record_period_plan_candidate("
                "text,text,text,bigint,text,text) FROM teruisi_ai_writer")
            with self.assertRaisesRegex(ValueError, "function ACL drift"):
                verify_catalog(cursor)
            transaction.set_rollback(True)


class BusinessV4PeriodPlanCandidateTests(TransactionTestCase):
    promotion_owner = fixture.promotion_owner
    rebuild_plan = fixture.rebuild_plan
    finance_owner = fixture.finance_owner
    owner = fixture.owner
    collect = fixture.collect
    complete_mixed = fixture.complete_mixed
    attempt = fixture.attempt
    database = fixture.database
    _role_connection = fixture._role_connection

    def setUp(self):
        fixture.setUp(self)
        # Synthetic roles can be created after migrations in the isolated
        # fixture; production provisioning grants this during installation.
        with connection.cursor() as cursor:
            cursor.execute("GRANT EXECUTE ON FUNCTION "
                "public.ai_v4_record_period_plan_candidate("
                "text,text,text,bigint,text,text) TO teruisi_ai_writer")

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("REVOKE EXECUTE ON FUNCTION "
                "public.ai_v4_record_period_plan_candidate("
                "text,text,text,bigint,text,text) FROM teruisi_ai_writer")
        fixture.tearDown(self)

    def four_source_run(self, *, start="2026-08-16", end="2026-09-14"):
        # The existing admission fixture already owns two runs. Use another
        # current admin so the old per-owner two-run limit remains intact.
        role = AccessRole.objects.get(code="admin")
        email = "v4-period-sidecar@example.test"
        now = timezone.now()
        AppUser.objects.create(email=email, display_name="Synthetic period",
            role=role, status="active", scope=None, version=1,
            created_at=now, updated_at=now)
        self.period_principal = Principal(email, "Synthetic period", "admin", None)
        planned = make_plan(start=start, end=end)
        raw = canonical(planned)
        with transaction.atomic():
            parent = m.AiBusinessV4Run.objects.create(id=uid("v4-period"),
                owner_email=email, client_request_id=planned["clientRequestId"],
                plan_json=raw, plan_digest=digest(raw),
                run_identity_digest=planned["runIdentityDigest"])
            for entry in planned["sourcePlans"]:
                m.AiBusinessV4Source.objects.create(id=uid("v4-period-source"),
                    run=parent, source_key=entry["sourceKey"],
                    ordinal=entry["ordinal"], domain=entry["domain"],
                    temporal_role=entry["temporalRole"],
                    query_json=canonical(entry["query"]),
                    query_digest=entry["queryDigest"],
                    source_identity_digest=entry["sourceIdentityDigest"],
                    source_revision_hint=entry["sourceRevisionHint"])
        # This test only exercises the new sidecar identity/ACL boundary. A
        # privileged synthetic fixture marks the source counters complete;
        # it is not a claim of real owning-page or signed-tool evidence.
        with connection.cursor() as cursor:
            for table in ("ai_business_v4_runs", "ai_business_v4_sources"):
                for trigger in ("ai_write_fence", "ai_v4_state"):
                    cursor.execute(f"ALTER TABLE public.{table} DISABLE TRIGGER {trigger}")
            cursor.execute("ALTER TABLE public.ai_business_v4_runs "
                "DISABLE TRIGGER ai_v4_parent_complete")
            try:
                cursor.execute("UPDATE public.ai_business_v4_sources SET "
                    "version=2,page_count=1,row_count=0,stored_bytes=32,"
                    "finished=true,source_ref=%s,source_revision=%s "
                    "WHERE run_id=%s", ["a" * 64, "1:" + "b" * 64,
                    parent.id])
                cursor.execute("UPDATE public.ai_business_v4_runs SET "
                    "version=5,page_count=4,row_count=0,stored_bytes=128 "
                    "WHERE id=%s", [parent.id])
            finally:
                cursor.execute("ALTER TABLE public.ai_business_v4_runs "
                    "ENABLE TRIGGER ai_v4_parent_complete")
                for table in ("ai_business_v4_sources", "ai_business_v4_runs"):
                    for trigger in ("ai_v4_state", "ai_write_fence"):
                        cursor.execute(f"ALTER TABLE public.{table} ENABLE TRIGGER {trigger}")
        actor, checked, sources, directory = owning.validation._directory(
            parent.id, self.period_principal)
        attempt = m.AiBusinessV4ValidationAttempt.objects.create(
            id=uid("v4-period-attempt"), run=parent, run_version=checked.version,
            plan_digest=checked.plan_digest, directory_digest=directory,
            actor_email=actor["email"], actor_version=actor["version"],
            key_id="a" * 16)
        self.period_parent = parent
        self.period_attempt = attempt
        self.period_envelope = prepare_candidate(planned)
        return parent, attempt

    def record(self, *, raw=None, actor=None, attempt=None):
        parent = self.period_parent
        selected = canonical(self.period_envelope) if raw is None else raw
        with self._role_connection("teruisi_ai_writer") as db:
            try:
                row = db.execute("SELECT envelope_digest,source_root,created_at "
                    "FROM public.ai_v4_record_period_plan_candidate("
                    "%s,%s,%s,%s,%s,%s)",
                    [parent.id, attempt or self.period_attempt.id,
                     actor or self.period_principal.email, 1,
                     selected, digest(selected)]).fetchone()
                db.execute("COMMIT")
                return row
            except Exception:
                db.execute("ROLLBACK")
                raise

    def test_exact_bytes_idempotent_and_no_authority_or_direct_table_read(self):
        self.four_source_run()
        periods = self.period_envelope["resolvedPeriods"]
        self.assertEqual((periods["current"]["startDate"],
            periods["current"]["endDate"]), ("2026-08-16", "2026-09-14"))
        self.assertEqual((periods["previous"]["startDate"],
            periods["previous"]["endDate"]), ("2026-07-17", "2026-08-15"))
        self.assertEqual((periods["yearAgo"]["startDate"],
            periods["yearAgo"]["endDate"]), ("2025-08-16", "2025-09-14"))
        before = AiDataRevision.objects.get(domain="ai-assistant").revision
        first = self.record()
        again = self.record()
        self.assertEqual(first, again)
        row = m.AiBusinessV4PeriodPlanCandidate.objects.get(
            run_id=self.period_parent.id)
        self.assertEqual(row.envelope_digest, first[0])
        self.assertEqual(row.envelope_json, canonical(self.period_envelope))
        self.assertEqual(AiDataRevision.objects.get(
            domain="ai-assistant").revision, before + 1)
        self.assertFalse(self.period_envelope["sourceAuthorityVerified"])
        self.assertFalse(self.period_envelope["observedDailyCoverageVerified"])
        self.assertFalse(self.period_envelope["registeredRenderer"])
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_writer")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public.ai_business_v4_period_plan_candidates")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("INSERT INTO public.ai_business_v4_period_plan_candidates "
                    "(run_id,attempt_id,owner_email,actor_version,plan_digest,"
                    "directory_digest,source_root,envelope_json,envelope_digest,"
                    "created_at) VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,now())",
                    [self.period_parent.id, self.period_attempt.id,
                     self.period_principal.email, self.period_parent.plan_digest,
                     self.period_attempt.directory_digest, "0" * 64,
                     canonical(self.period_envelope), first[0]])
        with connection.cursor() as cursor:
            cursor.execute("GRANT INSERT ON public.ai_business_v4_period_plan_candidates "
                "TO teruisi_ai_writer")
        try:
            with self.database() as db:
                db.execute("SET SESSION AUTHORIZATION teruisi_ai_writer")
                with self.assertRaisesRegex(psycopg.Error,
                        "ai_v4_period_candidate_direct_write_denied"):
                    db.execute("INSERT INTO public.ai_business_v4_period_plan_candidates "
                        "(run_id,attempt_id,owner_email,actor_version,plan_digest,"
                        "directory_digest,source_root,envelope_json,envelope_digest,"
                        "created_at) VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,now())",
                        [self.period_parent.id, self.period_attempt.id,
                         self.period_principal.email, self.period_parent.plan_digest,
                         self.period_attempt.directory_digest, "0" * 64,
                         canonical(self.period_envelope), first[0]])
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE INSERT ON "
                    "public.ai_business_v4_period_plan_candidates "
                    "FROM teruisi_ai_writer")

    def test_wrong_actor_changed_dates_and_newer_attempt_refuse(self):
        self.four_source_run()
        with self.assertRaises(psycopg.Error):
            self.record(actor="other@example.test")
        changed = {**self.period_envelope,
            "observedDailyCoverageVerified": True}
        changed["periodPlanDigest"] = digest({key: value for key, value
            in changed.items() if key != "periodPlanDigest"})
        with self.assertRaises(psycopg.Error):
            self.record(raw=canonical(changed))
        changed = __import__("copy").deepcopy(self.period_envelope)
        changed["dailySources"][0]["resolvedPeriod"]["startDate"] = "2026-08-15"
        changed["periodPlanDigest"] = digest({key: value for key, value
            in changed.items() if key != "periodPlanDigest"})
        with self.assertRaises(psycopg.Error):
            self.record(raw=canonical(changed))
        self.record()
        m.AiBusinessV4ValidationAttempt.objects.create(
            id=uid("v4-period-newer"), run=self.period_parent,
            run_version=5, plan_digest=self.period_parent.plan_digest,
            directory_digest=self.period_attempt.directory_digest,
            actor_email=self.period_principal.email, actor_version=1,
            key_id="a" * 16)
        with self.assertRaises(psycopg.Error):
            self.record()

    def test_leap_day_matches_python_expected_days_and_reader_role_cannot_write(self):
        self.four_source_run(start="2024-02-29", end="2024-02-29")
        self.assertEqual(next(row for row in self.period_envelope["dailySources"]
            if row["window"] == "yearAgo")["resolvedPeriod"]["startDate"],
            "2023-02-28")
        self.record()  # DB independently rehashes the three expected-day sets.
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public.ai_v4_record_period_plan_candidate("
                    "%s,%s,%s,%s,%s,%s)", [self.period_parent.id,
                    self.period_attempt.id, self.period_principal.email, 1,
                    canonical(self.period_envelope),
                    digest(canonical(self.period_envelope))])

    def test_source_root_drift_refuses_existing_candidate(self):
        self.four_source_run()
        self.record()
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE public.ai_business_v4_sources "
                "DISABLE TRIGGER ai_v4_state")
            cursor.execute("ALTER TABLE public.ai_business_v4_sources "
                "DISABLE TRIGGER ai_write_fence")
            try:
                cursor.execute("UPDATE public.ai_business_v4_sources SET "
                    "checkpoint_json=%s WHERE run_id=%s AND source_key=%s",
                    ['{"syntheticDrift":true}', self.period_parent.id,
                     "promotion-current"])
            finally:
                cursor.execute("ALTER TABLE public.ai_business_v4_sources "
                    "ENABLE TRIGGER ai_write_fence")
                cursor.execute("ALTER TABLE public.ai_business_v4_sources "
                    "ENABLE TRIGGER ai_v4_state")
        with self.assertRaises(psycopg.Error):
            self.record()

    def test_nonempty_reverse_refuses_and_old_direct_seal_stays_closed(self):
        self.four_source_run()
        self.record()
        migration = import_module(
            "ai_assistant.migrations.0055_business_v4_period_plan_candidate")
        with connection.schema_editor() as editor:
            with self.assertRaisesRegex(RuntimeError, "cannot discard"):
                migration.uninstall(None, editor)
        with connection.cursor() as cursor:
            cursor.execute("SELECT has_function_privilege('teruisi_ai_writer',"
                "'public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)',"
                "'EXECUTE')")
            self.assertEqual(cursor.fetchone(), (False,))

    def test_owning_path_refuses_non_admin_before_database_write(self):
        self.four_source_run()
        with self.assertRaises(AiError):
            owning.create_candidate(self.period_parent.id, self.principal)
        self.assertFalse(m.AiBusinessV4PeriodPlanCandidate.objects.exists())
