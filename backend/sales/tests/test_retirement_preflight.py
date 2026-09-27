from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from sales.models import SalesCutoverAttestation, SalesWriteAuthority
from sales.retirement_preflight import (
    REQUIRED_SMOKE_CHECKS,
    RETIREMENT_PREFLIGHT_VERSION,
    SMOKE_RESPONSE_CONTRACTS,
    SMOKE_RECEIPT_VERSION,
    RetirementPreflightError,
    _canonical_json,
    validate_smoke_receipt,
    smoke_online_paths,
)
from sales.smoke_receipt import (
    SMOKE_CHECK_EVIDENCE_VERSION,
    SmokeReceiptGenerationError,
    _writer_rollback_evidence,
    generate_smoke_receipt_bundle,
)
from sales.tests.cutover_fixtures import install_lightweight_attestation
from sales.tests.factories import install_fixture


CUTOVER_ID = "retirement-preflight-test-001"
PLAN_ID = "9" * 64


def _receipt_payload(attestation_sha256: str, *, now: datetime) -> dict[str, object]:
    checked_at = now.replace(microsecond=0).astimezone(UTC)
    return {
        "version": SMOKE_RECEIPT_VERSION,
        "planId": PLAN_ID,
        "cutoverId": CUTOVER_ID,
        "attestationPayloadSha256": attestation_sha256,
        "checkedAt": checked_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expiresAt": (checked_at + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requiredChecks": list(REQUIRED_SMOKE_CHECKS),
        "results": {
            name: {"status": "passed", "evidenceSha256": f"{index + 1:x}" * 64}
            for index, name in enumerate(REQUIRED_SMOKE_CHECKS)
        },
    }


def _receipt(payload: dict[str, object]) -> dict[str, object]:
    return {
        "payload": payload,
        "payloadSha256": hashlib.sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest(),
    }


class RetirementPreflightTests(TestCase):
    def setUp(self) -> None:
        self.attestation = install_lightweight_attestation(CUTOVER_ID)
        SalesWriteAuthority.objects.filter(id=1).update(
            status="active", cutover_id=CUTOVER_ID
        )
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)

    def _write_receipt(self, payload: dict[str, object]) -> tuple[Path, str]:
        destination = Path(self.root.name) / "smoke.json"
        evidence_directory = Path(f"{destination}.evidence")
        evidence_directory.mkdir()
        observed = datetime.strptime(
            payload["checkedAt"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=UTC)
        online_paths = smoke_online_paths(observed)
        for name in REQUIRED_SMOKE_CHECKS:
            if name in online_paths:
                evidence = {
                    "version": SMOKE_CHECK_EVIDENCE_VERSION,
                    "check": name,
                    "method": "GET",
                    "path": online_paths[name],
                    "statusCode": 200,
                    "bodySha256": "8" * 64,
                    "salesDataRevision": "" if name == "writer_readiness" else "8:5",
                    "salesSourceRevision": "" if name == "writer_readiness" else "8:5",
                    "responseContract": SMOKE_RESPONSE_CONTRACTS[name],
                    "observedAt": payload["checkedAt"],
                }
            else:
                evidence = {
                    "version": SMOKE_CHECK_EVIDENCE_VERSION,
                    "check": name,
                    "status": "passed",
                    "cutoverId": CUTOVER_ID,
                    "authorityEpoch": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "requestReceiptRollbackVerified": True,
                    "observedAt": payload["checkedAt"],
                }
            evidence_raw = f"{_canonical_json(evidence)}\n".encode("utf-8")
            (evidence_directory / f"{name}.json").write_bytes(evidence_raw)
            payload["results"][name]["evidenceSha256"] = hashlib.sha256(
                evidence_raw
            ).hexdigest()
        raw = (
            json.dumps(_receipt(payload), ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8")
        destination.write_bytes(raw)
        return destination, hashlib.sha256(raw).hexdigest()

    def test_command_returns_exact_live_authority_attestation_and_smoke_evidence(self):
        path, file_sha256 = self._write_receipt(
            _receipt_payload(self.attestation.payload_sha256, now=datetime.now(UTC))
        )
        stdout = StringIO()
        with patch.object(
            SalesWriteAuthority.objects,
            "select_for_update",
            side_effect=AssertionError("writer authority access must stay read-only"),
        ), patch.object(
            SalesCutoverAttestation.objects,
            "select_for_update",
            side_effect=AssertionError("writer attestation access must stay read-only"),
        ):
            call_command(
                "sales_cutover_retirement_preflight",
                plan_id=PLAN_ID,
                cutover_id=CUTOVER_ID,
                attestation_sha256=self.attestation.payload_sha256,
                smoke_receipt=str(path.resolve()),
                smoke_receipt_sha256=file_sha256,
                stdout=stdout,
            )
        result = json.loads(stdout.getvalue())
        self.assertEqual(set(result), {
            "status", "version", "planId", "cutoverId",
            "attestationPayloadSha256", "pgAuthorityStatus", "pgAuthorityEpoch",
            "migrationVerifyRunId", "requiredChecks", "checkedAt", "expiresAt",
            "smokeReceiptSha256", "evidenceSha256",
        })
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["version"], RETIREMENT_PREFLIGHT_VERSION)
        self.assertEqual(result["planId"], PLAN_ID)
        self.assertEqual(result["cutoverId"], CUTOVER_ID)
        self.assertEqual(result["attestationPayloadSha256"], self.attestation.payload_sha256)
        self.assertEqual(result["pgAuthorityStatus"], "active")
        self.assertEqual(result["requiredChecks"], list(REQUIRED_SMOKE_CHECKS))
        self.assertEqual(result["smokeReceiptSha256"], file_sha256)
        evidence_sha = result.pop("evidenceSha256")
        self.assertEqual(
            evidence_sha,
            hashlib.sha256(_canonical_json(result).encode("utf-8")).hexdigest(),
        )

    def test_validator_rejects_extra_keys_missing_checks_wrong_types_and_bad_ttl(self):
        now = datetime(2026, 8, 29, 0, 0, 0, tzinfo=UTC)
        valid = _receipt_payload(self.attestation.payload_sha256, now=now)
        mutations = []

        extra = json.loads(json.dumps(valid))
        extra["unexpected"] = "not-secret"
        mutations.append(extra)

        missing = json.loads(json.dumps(valid))
        missing["requiredChecks"] = missing["requiredChecks"][:-1]
        mutations.append(missing)

        wrong_type = json.loads(json.dumps(valid))
        wrong_type["results"][REQUIRED_SMOKE_CHECKS[0]]["status"] = True
        mutations.append(wrong_type)

        expired = json.loads(json.dumps(valid))
        expired["expiresAt"] = "2026-08-28T23:59:59Z"
        mutations.append(expired)

        excessive_ttl = json.loads(json.dumps(valid))
        excessive_ttl["expiresAt"] = "2026-08-29T00:10:01Z"
        mutations.append(excessive_ttl)

        for payload in mutations:
            with self.subTest(payload=payload.get("expiresAt")):
                with self.assertRaises(RetirementPreflightError):
                    validate_smoke_receipt(
                        _receipt(payload),
                        expected_plan_id=PLAN_ID,
                        expected_cutover_id=CUTOVER_ID,
                        expected_attestation_sha256=self.attestation.payload_sha256,
                        now=now,
                    )

    def test_command_rejects_inactive_pg_or_missing_attestation_without_path_or_secret(self):
        path, file_sha256 = self._write_receipt(
            _receipt_payload(self.attestation.payload_sha256, now=datetime.now(UTC))
        )
        SalesWriteAuthority.objects.filter(id=1).update(status="pending")
        with self.assertRaises(CommandError) as raised:
            call_command(
                "sales_cutover_retirement_preflight",
                plan_id=PLAN_ID,
                cutover_id=CUTOVER_ID,
                attestation_sha256=self.attestation.payload_sha256,
                smoke_receipt=str(path.resolve()),
                smoke_receipt_sha256=file_sha256,
            )
        self.assertNotIn(str(path), str(raised.exception))
        self.assertNotIn("postgresql://", str(raised.exception))

        SalesWriteAuthority.objects.filter(id=1).update(status="active")
        self.attestation.delete()
        with self.assertRaises(CommandError) as raised:
            call_command(
                "sales_cutover_retirement_preflight",
                plan_id=PLAN_ID,
                cutover_id=CUTOVER_ID,
                attestation_sha256="0" * 64,
                smoke_receipt=str(path.resolve()),
                smoke_receipt_sha256=file_sha256,
            )
        self.assertNotIn(str(path), str(raised.exception))

    def test_unexpected_errors_are_redacted_by_command(self):
        with patch(
            "sales.management.commands.sales_cutover_retirement_preflight.verify_retirement_preflight",
            side_effect=RuntimeError("secret-row-value postgresql://user:secret@host/db"),
        ):
            with self.assertRaises(CommandError) as raised:
                call_command(
                    "sales_cutover_retirement_preflight",
                    plan_id=PLAN_ID,
                    cutover_id=CUTOVER_ID,
                    attestation_sha256=self.attestation.payload_sha256,
                    smoke_receipt=str(Path(self.root.name) / "secret.json"),
                    smoke_receipt_sha256="0" * 64,
                )
        message = str(raised.exception)
        self.assertNotIn("secret-row-value", message)
        self.assertNotIn("postgresql://", message)

    @override_settings(
        DJANGO_INTERNAL_SECRET="retirement-smoke-test-secret-at-least-32-bytes"
    )
    def test_generator_runs_four_real_http_shapes_and_atomic_rollback_evidence_bundle(self):
        calls: list[tuple[str, dict[str, str]]] = []
        install_fixture()

        def requester(url: str, headers: dict[str, str], _timeout: int):
            calls.append((url, headers))
            if url.endswith("/health/ready"):
                return 200, {"content-type": "application/json"}, json.dumps({
                    "status": "ready", "writer": "ready"
                }).encode()
            parsed = urlsplit(url)
            path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            response = self.client.get(path_and_query, headers=headers)
            return response.status_code, {
                key.lower(): value for key, value in response.items()
            }, bytes(response.content)

        def writer_probe(cutover_id: str, attestation_sha256: str, observed_at: str):
            self.assertEqual(cutover_id, CUTOVER_ID)
            self.assertEqual(attestation_sha256, self.attestation.payload_sha256)
            return {
                "version": SMOKE_CHECK_EVIDENCE_VERSION,
                "check": "sales_write_transaction_rollback_probe",
                "status": "passed",
                "cutoverId": cutover_id,
                "authorityEpoch": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "requestReceiptRollbackVerified": True,
                "observedAt": observed_at,
            }

        output_directory = Path(self.root.name) / "smoke-bundle"
        with patch.dict(
            os.environ,
            {"TERUISI_DJANGO_INTERNAL_SECRET": "retirement-smoke-test-secret-at-least-32-bytes"},
        ):
            result = generate_smoke_receipt_bundle(
                plan_id=PLAN_ID,
                cutover_id=CUTOVER_ID,
                attestation_sha256=self.attestation.payload_sha256,
                output_directory=str(output_directory.resolve()),
                requester=requester,
                writer_probe=writer_probe,
                now=datetime.now(UTC),
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][0], "http://127.0.0.1:8002/health/ready")
        self.assertFalse(any(key.lower().startswith("x-teruisi") for key in calls[0][1]))
        self.assertTrue(all(
            any(key.lower() == "x-teruisi-signature" for key in headers)
            for _, headers in calls[1:]
        ))
        receipt_path = output_directory / "receipt.json"
        receipt_sha256 = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        stdout = StringIO()
        call_command(
            "sales_cutover_retirement_preflight",
            plan_id=PLAN_ID,
            cutover_id=CUTOVER_ID,
            attestation_sha256=self.attestation.payload_sha256,
            smoke_receipt=str(receipt_path.resolve()),
            smoke_receipt_sha256=receipt_sha256,
            stdout=stdout,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "verified")

    def test_generator_rejects_non_writer_database_role_and_non_loopback_urls(self):
        with self.assertRaises(SmokeReceiptGenerationError):
            _writer_rollback_evidence(
                CUTOVER_ID, self.attestation.payload_sha256, "2026-08-29T00:00:00Z"
            )
        with self.assertRaises(SmokeReceiptGenerationError):
            generate_smoke_receipt_bundle(
                plan_id=PLAN_ID,
                cutover_id=CUTOVER_ID,
                attestation_sha256=self.attestation.payload_sha256,
                output_directory=str((Path(self.root.name) / "not-created").resolve()),
                reader_base_url="http://example.com:8001",
                requester=lambda *_args: (200, {}, b"{}"),
                writer_probe=lambda *_args: {},
            )
        self.assertFalse((Path(self.root.name) / "not-created").exists())

    def test_writer_rollback_probe_reads_attestation_without_for_update(self):
        class WriterCursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def execute(self, sql: str) -> None:
                self.sql = sql

            def fetchone(self) -> tuple[str]:
                return ("teruisi_sales_writer",)

        class WriterConnection:
            vendor = "postgresql"

            def cursor(self) -> WriterCursor:
                return WriterCursor()

        authority = SalesWriteAuthority.objects.get(id=1)
        with patch("sales.smoke_receipt.connection", WriterConnection()), patch(
            "sales.smoke_receipt.lock_active_write_authority",
            return_value=authority,
        ), patch(
            "sales.smoke_receipt.require_valid_cutover_attestation",
            return_value=self.attestation.payload,
        ) as require_attestation:
            evidence = _writer_rollback_evidence(
                CUTOVER_ID,
                self.attestation.payload_sha256,
                "2026-08-29T00:00:00Z",
            )
        require_attestation.assert_called_once_with(
            cutover_id=CUTOVER_ID,
            payload_sha256=self.attestation.payload_sha256,
        )
        self.assertTrue(evidence["requestReceiptRollbackVerified"])

    @override_settings(
        DJANGO_INTERNAL_SECRET="retirement-smoke-test-secret-at-least-32-bytes"
    )
    def test_generator_rejects_empty_sales_payload_or_mismatched_revision_headers(self):
        for mode in ("empty", "mismatch"):
            output = Path(self.root.name) / f"rejected-{mode}"

            def requester(url: str, _headers: dict[str, str], _timeout: int):
                if url.endswith("/health/ready"):
                    return 200, {}, b'{"status":"ready","writer":"ready"}'
                headers = {
                    "x-sales-data-revision": "8:5",
                    "x-sales-source-revision": "9:5" if mode == "mismatch" else "8:5",
                }
                return 200, headers, b"{}"

            with self.subTest(mode=mode):
                with self.assertRaises(SmokeReceiptGenerationError):
                    generate_smoke_receipt_bundle(
                        plan_id=PLAN_ID,
                        cutover_id=CUTOVER_ID,
                        attestation_sha256=self.attestation.payload_sha256,
                        output_directory=str(output.resolve()),
                        requester=requester,
                        writer_probe=lambda *_args: {},
                    )
                self.assertFalse(output.exists())
