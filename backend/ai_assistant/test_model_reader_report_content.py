"""A real 19-column AI reader can recheck a promotion report's model."""
import secrets
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection

from . import business_promotion_runtime as runtime
from . import reports
from . import test_business_promotion_report_content as fixture
from .database_contract import provision
from .test_business_market_v2_material_role_bridge import session_role


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class ModelReaderReportContentTests(djtest.TransactionTestCase):
    user = fixture.PromotionReportContentTests.user
    call = fixture.PromotionReportContentTests.call
    collect_body = fixture.PromotionReportContentTests.collect_body
    bundle = fixture.PromotionReportContentTests.bundle
    input_for = fixture.PromotionReportContentTests.input_for
    insert = fixture.PromotionReportContentTests.insert
    seed = fixture.PromotionReportContentTests.seed
    setUp = fixture.PromotionReportContentTests.setUp
    request_body = fixture.PromotionReportContentTests.request_body
    current_catalog = fixture.PromotionReportContentTests.current_catalog
    create_fixed_report = fixture.PromotionReportContentTests.create_fixed_report
    base = fixture.PromotionReportContentTests.base
    read = fixture.PromotionReportContentTests.read
    append = fixture.PromotionReportContentTests.append
    package = fixture.PromotionReportContentTests.package
    promotion = fixture.PromotionReportContentTests.promotion
    complete = fixture.PromotionReportContentTests.complete
    running_job = fixture.PromotionReportContentTests.running_job
    five_completed = fixture.PromotionReportContentTests.five_completed

    def test_persisted_promotion_binding_uses_no_model_ciphertext(self):
        report = self.five_completed(promotion_reference=True)
        connection.ensure_connection()
        provision(connection.connection, secrets.token_hex(32),
            secrets.token_hex(32))
        with session_role("teruisi_ai_reader"), djtest.override_settings(
                DJANGO_PROCESS_ROLE="ai_reader"), patch.object(
                runtime.transport, "catalog", side_effect=self.current_catalog), \
                patch("ai_assistant.provider.turn") as provider:
            bound = runtime.bound_persisted(report.id, self.admin)
            provider.assert_not_called()
            # Full content needs a future narrow provider-ledger reader.  Do
            # not widen this role merely to make the report route pass.
            with self.assertRaises(DatabaseError):
                reports.detail(report.id, self.admin)
        self.assertEqual(bound["modelId"], report.workflow.model_id)
        self.assertEqual(bound["modelVersion"], report.workflow.model_version)
