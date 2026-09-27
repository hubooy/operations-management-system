"""Real sealed readers -> scan -> publish -> owning role package tests."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from business_analysis import screening_package as pure
from . import business_diagnostic_screening as screening, business_screening_packages as service
from . import business_screening_store as store, test_business_diagnostic_screening as fixtures
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ScreeningPackagesTests(djtest.TransactionTestCase):
    user = fixtures.DiagnosticScreeningTests.user
    call = fixtures.DiagnosticScreeningTests.call
    collect_body = fixtures.DiagnosticScreeningTests.collect_body
    bundle = fixtures.DiagnosticScreeningTests.bundle
    input_for = fixtures.DiagnosticScreeningTests.input_for
    insert = fixtures.DiagnosticScreeningTests.insert
    seed = fixtures.DiagnosticScreeningTests.seed
    setUp = fixtures.DiagnosticScreeningTests.setUp

    def publish(self):
        verified = screening.prepare_for_report(self.report.id, self.admin)
        return store.publish(verified, self.admin)["reference"]["id"]

    def test_real_all_roles_complete_and_stable_without_fact_queries_writes_or_models(self):
        run_id = self.publish()
        with patch.object(screening.Reader, "pages", side_effect=AssertionError("stored roles must not scan facts")), patch(
                "ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            prepared = service.prepare(run_id, self.admin)
            description = service.describe(prepared, self.admin)
            self.assertFalse(description["agentReadVerified"])
            self.assertEqual(set(description["roles"]), set(pure.ROLES))
            row, _, _ = store._loaded(run_id, self.admin)
            loaded = screening._load(self.report.id, self.admin)
            fixed_plan = screening._describe(loaded)["plan"]
            originals = pure.build(store._all(row), sources=loaded[4], source_infos=loaded[5], selection_plan=fixed_plan)
            for role in pure.ROLES:
                pages, offset = [], 0
                while offset is not None:
                    page = service.page(prepared, role, self.admin, offset=offset)
                    self.assertFalse(page["authorityVerified"])
                    self.assertLessEqual(len(canonical(page).encode("utf-8")), 38000)
                    self.assertEqual(page["pageDigest"], digest({k:v for k,v in page.items() if k != "pageDigest"}))
                    self.assertEqual(page["packageDigest"], description["roles"][role]["packageDigest"])
                    pages.append(page)
                    offset = page["pagination"]["nextOffset"]
                restored = pure.decode_pages(pages)
                self.assertEqual(restored, originals[role].unpack())
                self.assertEqual(restored["sources"], loaded[4])
                self.assertEqual(restored["sourceInfos"], loaded[5])
                self.assertEqual(pages[0]["selectionPlanDigest"], fixed_plan["planDigest"])
                self.assertTrue(any(item["kind"] == "table" for item in restored["coverage"]))
            self.assertEqual(service.describe(service.prepare(run_id, self.admin), self.admin), description)
        model.assert_not_called(); remote.assert_not_called()
        sql = [query["sql"].strip().lower() for query in queries]
        self.assertFalse(any("netshop_rows" in query or "sales_order_lines" in query for query in sql))
        self.assertFalse(any(query.startswith(("insert ", "update ", "delete ")) for query in sql))

    def test_public_json_cannot_restore_and_exact_roles_offsets(self):
        prepared = service.prepare(self.publish(), self.admin)
        for invalid in ({}, service.describe(prepared, self.admin), "{}", SimpleNamespace(_packages=prepared._packages)):
            with self.subTest(value=type(invalid).__name__), self.assertRaises(AiError):
                service.page(invalid, "commerce", self.admin)
        with self.assertRaises(AiError): service.PreparedPackages(None, "id", "{}", {}, {})
        for role in ("admin", "Commerce", "commerce ", None, True, ["commerce"]):
            with self.subTest(role=role), self.assertRaises(AiError): service.page(prepared, role, self.admin)
        for offset in (True, False, 1.0, "0", -1, pure.MAX_RECORDS, 10**100):
            with self.subTest(offset=offset), self.assertRaises(AiError): service.page(prepared, "commerce", self.admin, offset=offset)
        total = service.describe(prepared, self.admin)["roles"]["commerce"]["totalRecords"]
        with self.assertRaises(AiError): service.page(prepared, "commerce", self.admin, offset=total)

    def test_returned_pages_descriptions_and_unpacked_values_cannot_mutate_prepared(self):
        prepared = service.prepare(self.publish(), self.admin)
        first = service.page(prepared, "commerce", self.admin)
        expected = deepcopy(first)
        first["records"].clear(); first["directory"]["binding"]["ownerEmail"] = "forged"
        description = service.describe(prepared, self.admin)
        description["roles"]["commerce"]["packageDigest"] = "0"*64
        raw_package = prepared._packages[0][1]
        unpacked = raw_package.unpack()
        unpacked["sources"].clear(); unpacked["coverage"].clear()
        self.assertEqual(service.page(prepared, "commerce", self.admin), expected)
        self.assertEqual(service.describe(prepared, self.admin)["roles"]["commerce"]["packageDigest"], expected["packageDigest"])
        with self.assertRaises(FrozenInstanceError): prepared._identity_json = "{}"
        # Even deliberate mutation of a private pure object fails closed at the
        # owning boundary; a Python process-local token is not an auth secret.
        changed = json.loads(raw_package._raw)
        changed["records"][0][1] = {"forged":"self-consistent JSON must not replace the trusted package"}
        object.__setattr__(raw_package, "_raw", canonical(changed))
        with self.assertRaises(AiError): service.page(prepared, "commerce", self.admin)
        object.__setattr__(raw_package, "_digest", digest(changed))
        with self.assertRaises(AiError): service.page(prepared, "commerce", self.admin)

    def test_cross_account_scope_and_revocation_rechecked_for_every_read(self):
        run_id = self.publish(); prepared = service.prepare(run_id, self.admin)
        other = self.user("screening-package-other@example.invalid", "admin", None)
        from sales.auth import Principal
        scoped = Principal(self.admin.email, "scope", "admin", {"shops":["other"]})
        for actor in (other, self.viewer, scoped):
            with self.subTest(actor=actor.email):
                with self.assertRaises(AiError): service.prepare(run_id, actor)
                with self.assertRaises(AiError): service.page(prepared, "commerce", actor)
                with self.assertRaises(AiError): service.describe(prepared, actor)
        from access_control.models import AppUser
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError): service.page(prepared, "commerce", self.admin)

    def test_late_authorization_failure_does_not_return_prepared_page_or_reference(self):
        run_id = self.publish(); prepared = service.prepare(run_id, self.admin)
        original = store._loaded
        for invoke in (lambda:service.prepare(run_id,self.admin), lambda:service.page(prepared,"commerce",self.admin),
                lambda:service.describe(prepared,self.admin)):
            calls = []
            def loaded(*args):
                calls.append(1)
                if len(calls) == 2: raise AiError("synthetic late revocation", "forbidden", 403)
                return original(*args)
            with patch.object(store,"_loaded",loaded), self.assertRaises(AiError) as error: invoke()
            self.assertEqual(error.exception.status,403)
            self.assertEqual(len(calls),2)

    def test_changed_plan_binding_or_manifest_rejects_existing_container(self):
        run_id = self.publish(); prepared = service.prepare(run_id,self.admin)
        describe = screening._describe
        def changed(loaded):
            response = deepcopy(describe(loaded)); response["plan"]["planDigest"] = "0"*64
            return response
        with patch.object(screening,"_describe",changed), self.assertRaises(AiError): service.page(prepared,"commerce",self.admin)
        original = store._loaded
        for field, replacement in (("manifest_json", "{}"), ("binding_json", "{}"), ("stored_bytes",1)):
            def substituted(*args):
                row, binding, manifest = original(*args)
                setattr(row,field,replacement)
                return row,binding,manifest
            with self.subTest(field=field), patch.object(store,"_loaded",substituted), self.assertRaises(AiError):
                service.page(prepared,"commerce",self.admin)

    def test_corrupt_persisted_page_prevents_any_package_publication(self):
        run_id = self.publish()
        original = store._all
        def altered(row):
            bundle = original(row)
            bundle["pages"][-1]["payloadJson"] = canonical({"changed":"尾页"})
            bundle["pages"][-1]["payloadDigest"] = store.contract.raw_digest(bundle["pages"][-1]["payloadJson"])
            return bundle
        with patch.object(store,"_all",altered), self.assertRaises(AiError): service.prepare(run_id,self.admin)

    def test_trusted_source_metadata_cannot_be_replaced_by_caller_descriptions(self):
        run_id = self.publish()
        original = screening._load
        calls = []
        def substituted(*args):
            loaded = list(original(*args)); calls.append(1)
            if len(calls) == 2:
                loaded[5] = deepcopy(loaded[5])
                first = next(iter(loaded[5]))
                loaded[5][first]["pageCount"] += 1
            return tuple(loaded)
        with patch.object(screening,"_load",substituted), self.assertRaises(AiError): service.prepare(run_id,self.admin)
