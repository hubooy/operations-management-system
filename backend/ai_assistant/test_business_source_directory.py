"""Real PostgreSQL gates for the not-yet-exposed v2 directory foundation."""
from copy import deepcopy
from datetime import timedelta
from importlib import import_module
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.apps import apps
from django.db import DatabaseError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

from business_analysis.evidence_v2 import build_catalog
from . import models as m
from .database_contract import MODELS, READ_TABLES, WRITER_PRIVILEGES
from .policy import canonical, digest, uid
from .table_manifest import AI_TABLES


@skipUnless(connection.vendor == "postgresql", "source directory gates require PostgreSQL")
class BusinessSourceDirectoryTests(TestCase):
    def catalog(self, count=2):
        return build_catalog([{"key": f"source-{i}", "domain": "sales", "query": {
            "platform": "京东", "shop": f"测试店{i}", "channel": "合成渠道", "startDate": "2026-08-01", "endDate": "2026-08-02"
        }} for i in range(count)])

    def parent(self, catalog=None, **values):
        catalog = catalog or self.catalog()
        return m.AiBusinessEvidenceRun.objects.create(
            id=uid("evidence"), owner_email="directory@example.invalid", client_request_id=uid("client"),
            request_digest=catalog["planDigest"], plan_json=canonical(catalog["header"]), **values)

    def entry(self, parent, entry, **values):
        fields = {"id": uid("source"), "run": parent, "source_key": entry["key"], "ordinal": entry["ordinal"],
                  "domain": entry["domain"], "query_json": canonical(entry["query"]), "query_digest": entry["queryDigest"]}
        fields.update(values)
        return m.AiBusinessEvidenceSource.objects.create(**fields)

    def drain(self):
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ai_business_directory_complete IMMEDIATE")
            cursor.execute("SET CONSTRAINTS ai_business_directory_complete DEFERRED")

    def complete(self, count=2):
        catalog = self.catalog(count)
        parent = self.parent(catalog)
        sources = [self.entry(parent, entry) for entry in catalog["entries"]]
        self.drain()
        return parent, sources

    def chunk(self, parent, source, sequence=1):
        payload = canonical({"rows": [{"synthetic": "中文", "sequence": sequence}]})
        m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=parent, source_key=source.source_key,
            sequence=sequence, payload_json=payload, payload_digest=digest(payload))
        return len(payload.encode("utf-8"))

    def test_16_19_35_48_directory_rows_commit_without_touching_fact_limits(self):
        for count in (16, 19, 35, 48):
            with self.subTest(count=count):
                parent, sources = self.complete(count)
                self.assertEqual(len(sources), count)
                self.assertEqual(parent.stored_bytes, 0)
                self.assertEqual(parent.version, 1)
                self.assertEqual(parent.state_json, "{}")
                self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run=parent).exists())
                self.assertTrue(all(s.version == 1 and s.checkpoint_run_version == 1 and not s.finished for s in sources))
        self.assertIn("ai_business_evidence_sources", AI_TABLES)
        self.assertIn("ai_business_evidence_sources", READ_TABLES)
        self.assertEqual(MODELS["ai_business_evidence_sources"], m.AiBusinessEvidenceSource)
        self.assertEqual(WRITER_PRIVILEGES["ai_business_evidence_sources"], ("SELECT", "INSERT", "UPDATE"))
        self.assertEqual(len(AI_TABLES), 78)

    def test_missing_source_gap_duplicate_and_49th_are_rejected(self):
        for mode in ("missing", "ordinal_gap", "same_key", "same_ordinal", "same_query", "49"):
            with self.subTest(mode=mode), self.assertRaises(DatabaseError), transaction.atomic():
                catalog = self.catalog()
                if mode == "49":
                    catalog["header"]["sourceCount"] = 49
                parent = self.parent(catalog)
                self.entry(parent, catalog["entries"][0])
                second = deepcopy(catalog["entries"][1])
                if mode == "ordinal_gap":
                    second["ordinal"] = 3
                elif mode == "same_key":
                    second["key"] = catalog["entries"][0]["key"]
                elif mode == "same_ordinal":
                    second["ordinal"] = 1
                elif mode == "same_query":
                    second["queryDigest"] = catalog["entries"][0]["queryDigest"]
                if mode != "missing":
                    self.entry(parent, second)
                self.drain()
        self.assertFalse(m.AiBusinessEvidenceRun.objects.exists())

    def test_v1_rows_keep_exact_fields_and_never_accept_directory_rows(self):
        raw = '{ "schemaVersion": "business-evidence-v1", "sources": [] }'
        parent = self.parent({"header": {}, "planDigest": digest(raw)})
        # Create a separate true legacy row; the helper's {} row is also left untouched.
        legacy = m.AiBusinessEvidenceRun.objects.create(id=uid("legacy"), owner_email="directory@example.invalid",
            client_request_id=uid("legacy-client"), request_digest=digest(raw), plan_json=raw)
        before = m.AiBusinessEvidenceRun.objects.filter(pk=legacy.id).values().get()
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.entry(legacy, self.catalog()["entries"][0])
        self.drain()
        self.assertEqual(m.AiBusinessEvidenceRun.objects.filter(pk=legacy.id).values().get(), before)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=parent.id).plan_json, "{}")

    def test_source_initial_state_fixed_identity_no_delete_and_no_late_append(self):
        for field, value in (("version", 2), ("checkpoint_run_version", 2), ("page_count", 1),
                             ("stored_bytes", 1), ("row_count", 1), ("finished", True), ("checkpoint_json", '{"cursor":"x"}')):
            with self.subTest(field=field), self.assertRaises(DatabaseError), transaction.atomic():
                catalog = self.catalog(1)
                parent = self.parent(catalog)
                self.entry(parent, catalog["entries"][0], **{field: value})
        parent, sources = self.complete()
        source = sources[0]
        for field, value in (("source_key", "changed"), ("ordinal", 3), ("domain", "market"),
                             ("query_json", '{}'), ("query_digest", "b"*64), ("created_at", source.created_at-timedelta(seconds=1))):
            with self.subTest(field=field), self.assertRaises(DatabaseError), transaction.atomic():
                m.AiBusinessEvidenceSource.objects.filter(pk=source.id).update(**{field: value}, version=2, checkpoint_run_version=2)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceSource.objects.filter(pk=source.id).delete()
        m.AiBusinessEvidenceRun.objects.filter(pk=parent.id).update(version=2)
        self.drain()
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.entry(parent, self.catalog(3)["entries"][2])

    def test_checkpoint_requires_parent_cas_and_can_share_one_parent_advance(self):
        parent, sources = self.complete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceSource.objects.filter(pk=sources[0].id).update(version=2, checkpoint_run_version=2, checkpoint_json='{"cursor":"one"}')
            self.drain()
        self.assertEqual(m.AiBusinessEvidenceSource.objects.get(pk=sources[0].id).version, 1)
        with transaction.atomic():
            for source in sources:
                m.AiBusinessEvidenceSource.objects.filter(pk=source.id).update(version=2, checkpoint_run_version=2, checkpoint_json='{"cursor":"one"}')
            # Multiple page/checkpoint advances can target the same final parent CAS.
            m.AiBusinessEvidenceSource.objects.filter(pk=sources[0].id).update(version=3, checkpoint_run_version=2, checkpoint_json='{"cursor":"two"}')
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.id).update(version=2)
            self.drain()
        self.assertEqual(m.AiBusinessEvidenceSource.objects.get(pk=sources[0].id).version, 3)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceSource.objects.filter(pk=sources[0].id).update(version=4, checkpoint_run_version=2)

    def test_fact_totals_and_parent_stored_bytes_are_checked_at_commit(self):
        parent, sources = self.complete()
        for updates, parent_bytes in (([{"page_count": 1001}, {"page_count": 1000}], 0),
                                      ([{"stored_bytes": 33554432}, {"stored_bytes": 33554433}], 67108864),
                                      ([{"stored_bytes": 1}, {}], 0),
                                      ([{"row_count": 9007199254740991}, {"row_count": 1}], 0)):
            with self.subTest(updates=updates), self.assertRaises(DatabaseError), transaction.atomic():
                for source, values in zip(sources, updates):
                    m.AiBusinessEvidenceSource.objects.filter(pk=source.id).update(version=2, checkpoint_run_version=2, **values)
                m.AiBusinessEvidenceRun.objects.filter(pk=parent.id).update(version=2, stored_bytes=parent_bytes)
                self.drain()
        # A successful checkpoint must describe actual UTF-8 payloads, not invented counters.
        total = 0
        with transaction.atomic():
            for source in sources:
                size = sum(self.chunk(parent, source, sequence) for sequence in (1, 2))
                total += size
                m.AiBusinessEvidenceSource.objects.filter(pk=source.id).update(version=2, checkpoint_run_version=2, page_count=2, stored_bytes=size)
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.id).update(version=2, stored_bytes=total)
            self.drain()
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=parent.id).stored_bytes, total)
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run=parent).count(), 4)

    def test_chunk_ledger_rejects_unaccounted_gaps_wrong_source_and_missing_parent_cas(self):
        parent, sources = self.complete()
        for mode in ("chunk_only", "gap", "wrong_source", "characters_not_bytes", "no_parent_cas", "counters_without_chunks"):
            with self.subTest(mode=mode), self.assertRaises(DatabaseError), transaction.atomic():
                size = self.chunk(parent, sources[0], 2 if mode == "gap" else 1) if mode != "counters_without_chunks" else 2
                if mode != "chunk_only":
                    target = sources[1] if mode == "wrong_source" else sources[0]
                    m.AiBusinessEvidenceSource.objects.filter(pk=target.id).update(
                        version=2, checkpoint_run_version=2, page_count=1,
                        stored_bytes=size - 4 if mode == "characters_not_bytes" else size)
                    if mode != "no_parent_cas":
                        m.AiBusinessEvidenceRun.objects.filter(pk=parent.id).update(version=2,
                            stored_bytes=size - 4 if mode == "characters_not_bytes" else size)
                self.drain()
            self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run=parent).exists())
            self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=parent.id).version, 1)
            self.assertTrue(all(s.version == 1 and s.page_count == 0 and s.stored_bytes == 0
                                for s in m.AiBusinessEvidenceSource.objects.filter(run=parent)))

    def test_v2_header_rejects_float_and_exponent_integer_lexemes(self):
        catalog = self.catalog(1)
        raw = canonical(catalog["header"])
        for field, integer in (("sourceCount", "1"), ("version", "1"), ("pageSize", "100"),
                               ("factBytes", "67108864"), ("factPages", "2000")):
            for replacement in (integer + ".0", integer + "e0"):
                with self.subTest(field=field, replacement=replacement), self.assertRaises(DatabaseError), transaction.atomic():
                    changed = raw.replace('"' + field + '":' + integer, '"' + field + '":' + replacement)
                    self.assertNotEqual(changed, raw)
                    parent = m.AiBusinessEvidenceRun.objects.create(id=uid("evidence"), owner_email="directory@example.invalid",
                        client_request_id=uid("client"), request_digest=digest(changed), plan_json=changed)
                    self.entry(parent, catalog["entries"][0])
                    self.drain()

    def test_finished_seal_cancel_and_cross_run_chunk_binding(self):
        parent, sources = self.complete()
        other, other_sources = self.complete(1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=other, source_key=sources[1].source_key,
                sequence=1, payload_json="{}", payload_digest=digest("{}"))
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.id).update(version=2, status="sealed")
            self.drain()
        with transaction.atomic():
            for source in sources:
                m.AiBusinessEvidenceSource.objects.filter(pk=source.id).update(version=2, checkpoint_run_version=2, finished=True)
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.id).update(version=2, status="sealed")
            self.drain()
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceSource.objects.filter(pk=sources[0].id).update(version=3, checkpoint_run_version=3)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=parent, source_key=sources[0].source_key,
                sequence=1, payload_json="{}", payload_digest=digest("{}"))
        m.AiBusinessEvidenceRun.objects.filter(pk=other.id).update(version=2, status="cancelled")
        self.drain()
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceSource.objects.filter(pk=other_sources[0].id).update(version=2, checkpoint_run_version=3)

    def test_query_checkpoint_and_directory_bytes_are_bounded(self):
        for value in ('[]', '"text"', '{"padding":"'+'x'*4096+'"}'):
            with self.subTest(query=value[:20]), self.assertRaises(DatabaseError), transaction.atomic():
                catalog = self.catalog(1)
                parent = self.parent(catalog)
                self.entry(parent, catalog["entries"][0], query_json=value)
        with self.assertRaises(DatabaseError), transaction.atomic():
            catalog = self.catalog(48)
            parent = self.parent(catalog)
            for entry in catalog["entries"]:
                self.entry(parent, entry, query_json=canonical({"padding": "x"*3000, "key": entry["key"]}))
            self.drain()
        parent, sources = self.complete(1)
        for value in ('[]', '{"padding":"'+'x'*32768+'"}'):
            with self.subTest(checkpoint=value[:20]), self.assertRaises(DatabaseError), transaction.atomic():
                m.AiBusinessEvidenceSource.objects.filter(pk=sources[0].id).update(version=2, checkpoint_run_version=2, checkpoint_json=value)

    def test_profile_and_reverse_migration_fail_closed_with_v2_records(self):
        for change in ({"capacityProfile": "unknown"}, {"limits": {"factBytes": 268435456, "factPages": 8000}},
                       {"collector": {"version": 2}}, {"catalogDigest": "not-sha"}, {"sources": []}):
            with self.subTest(change=change), self.assertRaises(DatabaseError), transaction.atomic():
                catalog = self.catalog(1)
                catalog["header"].update(change)
                parent = self.parent(catalog)
                self.entry(parent, catalog["entries"][0])
                self.drain()
        self.complete(1)
        uninstall = import_module("ai_assistant.migrations.0019_business_source_directory").uninstall
        with self.assertRaisesMessage(RuntimeError, "不能回退"):
            uninstall(apps, None)


@skipUnless(connection.vendor == "postgresql", "source directory races require PostgreSQL")
class BusinessSourceDirectoryConcurrencyTests(TransactionTestCase):
    # These fixtures are committed before worker connections start; TestCase savepoints
    # would hide them from the independent transactions and invalidate the race.
    catalog = BusinessSourceDirectoryTests.catalog
    parent = BusinessSourceDirectoryTests.parent
    entry = BusinessSourceDirectoryTests.entry
    drain = BusinessSourceDirectoryTests.drain
    complete = BusinessSourceDirectoryTests.complete

    def race(self, same_source):
        with transaction.atomic():
            parent, sources = self.complete()
        rendezvous = Barrier(2)

        def advance(index):
            db = connections["default"]
            try:
                with transaction.atomic():
                    with db.cursor() as cursor:
                        cursor.execute("SET LOCAL statement_timeout='5s'")
                        cursor.execute("SET LOCAL lock_timeout='5s'")
                    source_id = sources[0 if same_source else index].id
                    seen_parent = m.AiBusinessEvidenceRun.objects.get(pk=parent.id)
                    seen_source = m.AiBusinessEvidenceSource.objects.get(pk=source_id)
                    self.assertEqual((seen_parent.version, seen_source.version), (1, 1))
                    # No row locks are held while waiting for the other reader.
                    rendezvous.wait(timeout=5)
                    payload = canonical({"rows": [{"synthetic": "中文", "worker": index}]})
                    size = len(payload.encode("utf-8"))
                    m.AiBusinessEvidenceSource.objects.filter(pk=source_id).update(
                        version=seen_source.version + 1, checkpoint_run_version=seen_parent.version + 1,
                        checkpoint_json=canonical({"worker": index}), page_count=1, stored_bytes=size, row_count=1)
                    m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run_id=parent.id,
                        source_key=seen_source.source_key, sequence=1, payload_json=payload, payload_digest=digest(payload))
                    m.AiBusinessEvidenceRun.objects.filter(pk=parent.id).update(
                        version=seen_parent.version + 1, stored_bytes=size)
                return ("committed", source_id, size)
            except DatabaseError as exc:
                return ("rejected", str(exc), 0)
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(advance, index) for index in (0, 1)]
            results = [future.result(timeout=20) for future in futures]
        self.assertCountEqual([result[0] for result in results], ["committed", "rejected"])
        rejected = next(result for result in results if result[0] == "rejected")
        self.assertIn("ai_business_source_checkpoint_fence", rejected[1])
        winner = next(result for result in results if result[0] == "committed")
        parent.refresh_from_db()
        self.assertEqual((parent.version, parent.stored_bytes), (2, winner[2]))
        chunks = list(m.AiBusinessEvidenceChunk.objects.filter(run=parent))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0].payload_json.encode("utf-8")), winner[2])
        for source in m.AiBusinessEvidenceSource.objects.filter(run=parent):
            if source.id == winner[1]:
                self.assertEqual((source.version, source.checkpoint_run_version, source.page_count,
                                  source.stored_bytes, source.row_count), (2, 2, 1, winner[2], 1))
                self.assertEqual(source.source_key, chunks[0].source_key)
            else:
                self.assertEqual((source.version, source.checkpoint_run_version, source.page_count,
                                  source.stored_bytes, source.row_count, source.checkpoint_json), (1, 1, 0, 0, 0, "{}"))

    def test_same_source_stale_concurrent_checkpoint_rolls_back_atomically(self):
        self.race(same_source=True)

    def test_different_sources_cannot_spend_the_same_parent_cas(self):
        self.race(same_source=False)
