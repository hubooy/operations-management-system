"""Real new-profile reports; root runs isolated PostgreSQL after reader integration."""
from unittest.mock import patch
from django import test as djtest
from access_control.models import AppUser
from business_analysis.contracts import AnalysisContractError
from . import business_promotion_file_tables as service
from . import test_business_promotion_runtime_persisted as fixtures
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionFileTablesTests(djtest.TransactionTestCase):
    user = fixtures.PromotionPersistedRuntimeTests.user
    call = fixtures.PromotionPersistedRuntimeTests.call
    collect_body = fixtures.PromotionPersistedRuntimeTests.collect_body
    bundle = fixtures.PromotionPersistedRuntimeTests.bundle
    input_for = fixtures.PromotionPersistedRuntimeTests.input_for
    insert = fixtures.PromotionPersistedRuntimeTests.insert
    seed = fixtures.PromotionPersistedRuntimeTests.seed
    request_body = fixtures.PromotionPersistedRuntimeTests.request_body
    current_catalog = fixtures.PromotionPersistedRuntimeTests.current_catalog
    create_report = fixtures.PromotionPersistedRuntimeTests.create_report
    setUp = fixtures.PromotionPersistedRuntimeTests.setUp

    def test_actual_draft_two_tables_have_immutable_binding_and_close_lifecycle(self):
        report = self.create_report()
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog), patch("ai_assistant.provider.turn") as model:
            with service.open_tables(report.id, self.admin, draft=True) as (metadata, tables):
                self.assertEqual(len(tables), 2)
                value = metadata.value
                self.assertEqual(value["reportBinding"]["reportId"], report.id)
                self.assertFalse(value["deliveryAuthorized"])
                self.assertFalse(value["registeredRenderer"])
                self.assertEqual(value["metadataDigest"], digest({k:v for k,v in value.items() if k != "metadataDigest"}))
                value["reportBinding"].clear()
                self.assertEqual(metadata.value["reportBinding"]["reportId"], report.id)
                self.assertGreater(len(list(tables[0].rows)), 0)
                pending = tables[1].rows
            with self.assertRaises(AnalysisContractError): next(pending)
        model.assert_not_called()

    def test_unready_formal_is_rejected_before_prepare(self):
        report = self.create_report()
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(service.export, "prepare") as prepare:
            with self.assertRaisesRegex(AiError, "完整就绪"):
                with service.open_tables(report.id, self.admin): self.fail("formal not ready")
            prepare.assert_not_called()

    def test_late_revocation_during_consumption_fails_context_exit(self):
        report = self.create_report()
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog), self.assertRaises(AiError):
            with service.open_tables(report.id, self.admin, draft=True) as (_, tables):
                list(tables[0].rows)
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")

    def test_tampered_manifest_and_untyped_prepared_are_not_accepted(self):
        report = self.create_report()
        original = service.export.PreparedPromotionExport.manifest.fget
        def bad(prepared):
            value = original(prepared); value["reportBinding"]["reportId"] = "other"
            value["manifestDigest"] = digest({k:v for k,v in value.items() if k != "manifestDigest"})
            return value
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            with patch.object(service.export.PreparedPromotionExport, "manifest", property(bad)), self.assertRaises(AiError):
                with service.open_tables(report.id, self.admin, draft=True): self.fail("tamper escaped")
            with patch.object(service.export, "prepare", return_value={}), self.assertRaises(AiError):
                with service.open_tables(report.id, self.admin, draft=True): self.fail("DTO escaped")

    def test_wrong_owner_old_profile_and_capacity_fail_closed(self):
        report = self.create_report()
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            for report_id, actor in ((report.id,self.viewer), (self.report.id,self.admin), ("missing",self.admin)):
                with self.assertRaises(AiError):
                    with service.open_tables(report_id, actor, draft=True): pass
            with self.assertRaises(AiError) as caught:
                with service.open_tables(report.id, self.admin, draft=True, limits={"maxBytes":100}): pass
            self.assertEqual(caught.exception.status, 413)

    def test_caller_error_not_hidden_or_converted_to_success(self):
        report = self.create_report(); error = RuntimeError("caller cancelled")
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog), self.assertRaises(RuntimeError) as caught:
            with service.open_tables(report.id, self.admin, draft=True): raise error
        self.assertIs(caught.exception, error)
