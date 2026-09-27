"""Real PostgreSQL 0036/0041 replay across two naturally expiring claims.

This intentionally slow test waits for the first 180-second lease to expire.
It does not shorten a lease, modify ticket rows, or substitute a fake page or
segment for the owning source and persisted 0036 HMAC chain.
"""
import json
import time
from urllib.parse import urlencode

import psycopg
from django.conf import settings
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.http import QueryDict

from business_analysis.v4_sealer_mac import derive_segment_key
from business_analysis.v4_sealer_step_core import replay_one_claimed_segment
from netshop import analysis as owning
from netshop.models import NetshopDataRevision, NetshopRow

from . import business_v4_validation as validation, models as m
from .policy import digest
from .test_business_v4_seal_ticket import BusinessV4SealTicketTests as fixture


class BusinessV4CrossTicketReplayTests(TransactionTestCase):
    promotion_owner = fixture.promotion_owner
    rebuild_plan = fixture.rebuild_plan
    finance_owner = fixture.finance_owner
    owner = fixture.owner
    collect = fixture.collect
    complete_mixed = fixture.complete_mixed
    database = fixture.database
    _identity = fixture._identity
    _role_connection = fixture._role_connection
    issue = fixture.issue
    claim = fixture.claim
    setUp = fixture.setUp
    tearDown = fixture.tearDown

    @staticmethod
    def _publish_netshop_revision(label):
        current = NetshopDataRevision.objects.select_for_update().get(
            domain="netshop")
        NetshopDataRevision.objects.filter(domain="netshop").update(
            revision=current.revision + 1,
            source_digest=digest([current.source_digest, label,
                                  current.revision + 1]))

    def _make_exactly_seventeen_owned_pages(self):
        base = NetshopRow.objects.get(source_row_key="v4-promo-0")
        values = {field.attname: getattr(base, field.attname)
                  for field in base._meta.concrete_fields if field.attname != "id"}
        # The existing 0036 fixture uses this same number to cross the
        # sixteen-page boundary. Extra rows are moved outside the requested
        # period only after a real owning-page probe finds page 17's final ID.
        with transaction.atomic():
            NetshopRow.objects.bulk_create([NetshopRow(**{**values,
                "source_row_key": f"v4-cross-ticket-{index}",
                "source_row_hash": f"{200000 + index:064x}",
                "source_row_number": 4000 + index,
                "sku_id": str(4000 + index)}) for index in range(1500)])
            self._publish_netshop_revision("v4-cross-ticket-insert")

        params = QueryDict(urlencode({**self.query, "limit": "100"}))
        spec, limit, cursor = owning.validate_request(params)
        seventeenth = None
        for sequence in range(1, 18):
            page = owning.read_page(spec, limit, cursor)
            self.assertTrue(page["items"], f"owning page {sequence} absent")
            if sequence == 17:
                seventeenth = page
                break
            self.assertTrue(page["pagination"]["hasMore"])
            cursor = page["pagination"]["nextCursor"]
        self.assertIsNotNone(seventeenth)
        if seventeenth["pagination"]["hasMore"]:
            cutoff = int(seventeenth["items"][-1]["rowId"])
            with transaction.atomic():
                changed = NetshopRow.objects.filter(
                    source_row_key__startswith="v4-cross-ticket-",
                    id__gt=cutoff).update(business_date="2025-01-01")
                self.assertGreater(changed, 0)
                self._publish_netshop_revision("v4-cross-ticket-window")

    def _wait_for_actual_lease_end(self, lease_until):
        # Issue checks clock_timestamp(), so use that database clock rather
        # than a frozen test clock or a host-clock estimate. This wait does
        # not change the 0041 ticket/claim policy or persisted timestamps.
        deadline = time.monotonic() + 185
        while True:
            with connection.cursor() as cursor:
                cursor.execute("SELECT EXTRACT(EPOCH FROM (%s - "
                    "clock_timestamp()))", [lease_until])
                remaining = float(cursor.fetchone()[0])
            if remaining < -0.1:
                return
            self.assertLess(time.monotonic(), deadline,
                "0041 claim lease did not naturally expire")
            time.sleep(min(max(remaining + 0.15, 0.1), 5.0))

    def test_real_seventeenth_page_uses_new_ticket_and_previous_receipt(self):
        self._make_exactly_seventeen_owned_pages()
        version, number = 1, 1
        while True:
            result = self.collect("promotion-current", version, number)
            version = result["runVersion"]
            if result["finished"]:
                break
            number += 1
            self.assertLessEqual(number, 17)
        self.assertEqual((result["pageCount"], number), (17, 17))
        finance_number = 1
        while True:
            finance = self.collect("finance-context", version, finance_number)
            version = finance["runVersion"]
            if finance["finished"]:
                break
            finance_number += 1
            self.assertLessEqual(finance_number, 16)
        prepared = validation.start_attempt(self.parent.id,
            version, self.principal)
        attempt_id = prepared["attemptId"]
        first_segment = validation.advance_segment(attempt_id,
            "promotion-current", 1, self.principal)
        second_segment = validation.advance_segment(attempt_id,
            "promotion-current", 2, self.principal)
        for finance_index in range(1, (finance["pageCount"] + 15) // 16 + 1):
            validation.advance_segment(attempt_id, "finance-context",
                finance_index, self.principal)
        self.assertEqual((first_segment["startSequence"],
            first_segment["endSequence"], second_segment["startSequence"],
            second_segment["endSequence"]), (1, 16, 17, 17))
        source = m.AiBusinessV4Source.objects.get(
            pk=self.sources["promotion-current"].pk)
        self.assertEqual(m.AiBusinessV4Chunk.objects.filter(
            source=source).count(), 17)
        self.assertEqual(m.AiBusinessV4ToolReceipt.objects.filter(
            source=source).count(), 17)
        self.assertEqual(m.AiBusinessV4ValidationSegment.objects.filter(
            attempt_id=attempt_id, source=source).count(), 2)

        derived_key = derive_segment_key(settings.DJANGO_INTERNAL_SECRET)
        _, nonce_one, _ = self.issue(attempt_id, request="cross-ticket-first")
        ticket_one, claim_one, lease_one = self.claim(attempt_id, nonce_one)
        parent, _, actor = self._identity(attempt_id)
        args = dict(run_id=parent.id, attempt_id=attempt_id,
            source_id=source.id, actor_email=actor.email,
            actor_version=actor.version, enabled=True)
        with self._role_connection("teruisi_ai_seal_writer") as db:
            self.assertTrue(db.autocommit)
            first = replay_one_claimed_segment(db, derived_key,
                segment_index=1, nonce=nonce_one, claim=claim_one, **args)
            db.execute("COMMIT")
        self.assertEqual(first["status"], "recorded_candidate")
        self.assertTrue(first["candidateOnly"])
        self.assertFalse(first["authorityVerified"])

        # 0041 permits only one active ticket/claim per attempt. A second
        # request cannot be issued merely because the first 60-second ticket
        # has expired: the 180-second claim must also expire naturally.
        with self.assertRaises(psycopg.Error):
            self.issue(attempt_id, request="cross-ticket-second")
        self._wait_for_actual_lease_end(lease_one)
        with self._role_connection("teruisi_ai_seal_writer") as db:
            with self.assertRaises(psycopg.Error):
                replay_one_claimed_segment(db, derived_key, segment_index=2,
                    nonce=nonce_one, claim=claim_one, **args)
            db.execute("ROLLBACK")
        _, nonce_two, _ = self.issue(attempt_id,
            request="cross-ticket-second")
        ticket_two, claim_two, _ = self.claim(attempt_id, nonce_two)
        self.assertNotEqual(ticket_two, ticket_one)
        self.assertNotEqual(claim_two, claim_one)
        with self._role_connection("teruisi_ai_seal_writer") as db:
            second = replay_one_claimed_segment(db, derived_key,
                segment_index=2, nonce=nonce_two, claim=claim_two, **args)
            self.assertEqual(second["status"], "recorded_candidate")
            replayed = replay_one_claimed_segment(db, derived_key,
                segment_index=2, nonce=nonce_two, claim=claim_two, **args)
            db.execute("COMMIT")
        self.assertEqual(second["status"], "recorded_candidate")
        self.assertEqual(replayed["status"], "existing_candidate")
        self.assertEqual(replayed["candidateDigest"], second["candidateDigest"])
        with connection.cursor() as cursor:
            cursor.execute("SELECT segment_index,ticket_id,candidate_json,"
                "candidate_digest,previous_candidate_digest,source_root "
                "FROM public.ai_business_v4_sealer_replay_progress "
                "WHERE attempt_id=%s AND source_id=%s ORDER BY segment_index",
                [attempt_id, source.id])
            rows = cursor.fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0][0], rows[1][0]), (1, 2))
        self.assertEqual((rows[0][1], rows[1][1]), (ticket_one, ticket_two))
        self.assertEqual(rows[1][4], rows[0][3])
        self.assertEqual(rows[1][5], rows[0][5])
        self.assertEqual(json.loads(rows[1][2])["previousCandidateDigest"],
            rows[0][3])
        self.assertEqual(rows[1][3], second["candidateDigest"])
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=parent.pk).status,
            "collecting")
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(run=parent).exists())
