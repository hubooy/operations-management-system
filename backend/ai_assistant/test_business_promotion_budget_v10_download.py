"""Real reader-role v10 chunks; isolated PostgreSQL only."""
import base64
import hashlib
import json
import secrets
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest
from django.db import connection

from . import business_promotion_budget_v10_download as service
from . import business_volume_files, models as m
from . import test_business_promotion_budget_v10_reader_fence as fixture
from .database_contract import provision
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BudgetV10DownloadTests(djtest.TransactionTestCase):
    user = fixture.BudgetV10ReaderFenceTests.user
    call = fixture.BudgetV10ReaderFenceTests.call
    collect_body = fixture.BudgetV10ReaderFenceTests.collect_body
    bundle = fixture.BudgetV10ReaderFenceTests.bundle
    input_for = fixture.BudgetV10ReaderFenceTests.input_for
    insert = fixture.BudgetV10ReaderFenceTests.insert
    seed = fixture.BudgetV10ReaderFenceTests.seed
    request_body = fixture.BudgetV10ReaderFenceTests.request_body
    current_catalog = fixture.BudgetV10ReaderFenceTests.current_catalog
    create_fixed_report = fixture.BudgetV10ReaderFenceTests.create_fixed_report
    base = fixture.BudgetV10ReaderFenceTests.base
    read = fixture.BudgetV10ReaderFenceTests.read
    append = fixture.BudgetV10ReaderFenceTests.append
    package = fixture.BudgetV10ReaderFenceTests.package
    promotion = fixture.BudgetV10ReaderFenceTests.promotion
    complete = fixture.BudgetV10ReaderFenceTests.complete
    running_job = fixture.BudgetV10ReaderFenceTests.running_job
    five_completed = fixture.BudgetV10ReaderFenceTests.five_completed
    approved = fixture.BudgetV10ReaderFenceTests.approved
    _complete_budget_report = fixture.BudgetV10ReaderFenceTests._complete_budget_report
    _stage = fixture.BudgetV10ReaderFenceTests._stage
    _database = staticmethod(fixture.BudgetV10ReaderFenceTests._database)
    _body = staticmethod(fixture.BudgetV10ReaderFenceTests._body)
    _attest = fixture.BudgetV10ReaderFenceTests._attest
    _role = fixture.BudgetV10ReaderFenceTests._role
    complete_flow = fixture.BudgetV10ReaderFenceTests.complete_flow
    _ready = fixture.BudgetV10ReaderFenceTests._ready

    def setUp(self):
        fixture.BudgetV10ReaderFenceTests.setUp(self)
        # The isolated harness precreates roles, but only the formal runtime
        # contract grants reader its exact table privileges.
        connection.ensure_connection()
        provision(connection.connection, secrets.token_hex(32), secrets.token_hex(32))

    def _as_reader(self, callback):
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
            try:
                return callback()
            finally:
                cursor.execute("RESET SESSION AUTHORIZATION")

    @djtest.override_settings(AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED=False)
    def test_flag_is_closed_before_reading_any_database_proof(self):
        with self.assertRaises(AiError), patch.object(service,
                "_narrow_receipt", side_effect=AssertionError("must stay closed")):
            service.chunk(SimpleNamespace(id="unused"), 1, "html", 1, self.admin)

    @djtest.override_settings(AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED=True)
    def test_real_reader_reassembles_all_descriptors_with_exact_hashes(self):
        _, row, _ = self._ready(budget=True)
        from business_analysis import volume_delivery
        compact = volume_delivery.validate(json.loads(row.manifest_json),
            binding_digest=row.binding_digest, attempt=row.attempt,
            draft=False, renderer_version=10)
        descriptors = [*compact["files"], compact["manifestFile"]]
        def download():
            with connection.cursor() as cursor:
                cursor.execute("SELECT has_table_privilege(current_user,%s,'SELECT'),"
                    "has_table_privilege(current_user,%s,'SELECT'),"
                    "has_table_privilege(current_user,%s,'SELECT'),"
                    "has_table_privilege(current_user,%s,'SELECT')",
                    ["public.access_control_users", "public.ai_business_file_runs",
                     "public.ai_business_volume_chunks",
                     "public.ai_business_promotion_budget_v10_attestations"])
                self.assertEqual(cursor.fetchone(), (True, True, True, False))
            for descriptor in descriptors:
                pieces = []
                for sequence in range(1, descriptor["chunkCount"]+1):
                    part = business_volume_files.chunk(row.id,
                        str(descriptor["volumeIndex"]), descriptor["format"],
                        {"sequence": str(sequence)}, self.admin)
                    self.assertEqual(part["schemaVersion"], service.SCHEMA)
                    self.assertEqual(part["attempt"], row.attempt)
                    self.assertEqual(part["fileSha256"], descriptor["sha256"])
                    self.assertEqual(part["manifestFileSha256"],
                        compact["manifestFile"]["sha256"])
                    raw = base64.b64decode(part["base64"], validate=True)
                    self.assertEqual((part["bytes"], part["sha256"]),
                        (len(raw), hashlib.sha256(raw).hexdigest()))
                    pieces.append(raw)
                actual = b"".join(pieces)
                self.assertEqual((len(actual), hashlib.sha256(actual).hexdigest()),
                    (descriptor["bytes"], descriptor["sha256"]))
        self._as_reader(download)

    @djtest.override_settings(AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED=True)
    def test_wrong_role_tampered_part_and_changed_second_receipt_fail(self):
        _, row, _ = self._ready()
        with self.assertRaises(AiError):
            service.chunk(row, 1, "html", 1, self.admin)
        original = service._narrow_receipt
        def changed(*args):
            value = original(*args)
            changed.calls += 1
            return {**value, "publicationFenceDigest": "0" * 64} if changed.calls == 2 else value
        changed.calls = 0
        with patch.object(service, "_narrow_receipt", side_effect=changed):
            with self.assertRaises(AiError):
                self._as_reader(lambda: service.chunk(row, 1, "html", 1, self.admin))
        fake = SimpleNamespace(content=b"tampered", content_digest="0" * 64)
        with patch.object(m.AiBusinessVolumeChunk.objects, "filter") as query:
            query.return_value.first.return_value = fake
            with self.assertRaises(AiError):
                self._as_reader(lambda: service.chunk(row, 1, "html", 1, self.admin))

    @djtest.override_settings(AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED=True)
    def test_admin_revoked_after_first_receipt_denies_chunk_and_fresh_receipt(self):
        _, row, _ = self._ready(budget=True)
        original = service._narrow_receipt
        calls = 0

        def revoke_after_first(actual, principal):
            nonlocal calls
            result = original(actual, principal)
            calls += 1
            if calls == 1:
                # A separate privileged connection commits the real account
                # change while the request is running as the isolated reader.
                with self._database() as db:
                    changed = db.execute("UPDATE public.access_control_users "
                        "SET status='disabled' WHERE email=%s AND status='active'",
                        [self.admin.email.lower()]).rowcount
                    self.assertEqual(changed, 1)
            return result

        with patch.object(service, "_narrow_receipt", side_effect=revoke_after_first):
            with self.assertRaises(AiError):
                self._as_reader(lambda: service.chunk(row, 1, "html", 1, self.admin))
        self.assertEqual(calls, 1)
        # The second application fence can stop at current_principal before
        # SQL; independently prove the 0059 receipt itself is also revoked.
        with self.assertRaises(AiError):
            self._as_reader(lambda: original(row, self.admin))
