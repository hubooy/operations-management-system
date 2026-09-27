"""Read-only source endpoints and rejection of unknown report evidence versions."""
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, override_settings
from . import business_reports, models as m, tests as fixtures
from .policy import AiError, canonical


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessSourceRouteTests(TestCase):
    user = fixtures.AiDomainTests.user
    call = fixtures.AiDomainTests.call

    def setUp(self):
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("directory-routes@example.invalid", "admin", None)

    def test_source_routes_are_reader_only_and_reserved_source_names_are_unambiguous(self):
        with patch("ai_assistant.views.business_evidence.directory", return_value={"entries": []}) as directory, \
                patch("ai_assistant.views.business_evidence.source_detail", return_value={"sourceKey": "analysis"}) as detail, \
                patch("ai_assistant.views.business_evidence.analysis_table") as analysis, \
                patch("ai_assistant.views.authority"), override_settings(DJANGO_PROCESS_ROLE="ai_reader"):
            response = self.call("/api/ai/business-evidence/run-1/sources", principal=self.admin, method="GET")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(directory.call_args.args[0:2], ("run-1", {}))
            response = self.call("/api/ai/business-evidence/run-1/sources/analysis", principal=self.admin, method="GET")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(detail.call_args.args[0:2], ("run-1", "analysis"))
            analysis.assert_not_called()
            for path in ("sources", "sources/analysis"):
                self.assertEqual(self.call("/api/ai/business-evidence/run-1/"+path, {}, self.admin).status_code, 405)
            self.assertEqual(self.call("/api/ai/business-evidence/run-1/sources", principal=self.viewer, method="GET").status_code, 403)
            self.assertEqual(directory.call_count, 1)
            self.assertEqual(detail.call_count, 1)

    def test_unknown_report_evidence_rejection_precedes_workflow_and_model_activity(self):
        before = (m.AiReportRun.objects.count(), m.AiWorkflowRuns.objects.count())
        body = {"clientRequestId": "blocked-v2-report", "evidenceRunId": "v2-evidence", "question": "分析", "dryRun": False}
        with patch("ai_assistant.business_reports.workflows.create") as workflow, \
                patch("ai_assistant.provider.turn") as provider:
            for schema in ("business-evidence-v999", "unknown"):
                row = SimpleNamespace(status="sealed", plan_json=canonical({"schemaVersion": schema}))
                with patch("ai_assistant.business_reports.business_evidence.get_run", return_value=row):
                    with self.assertRaises(AiError) as caught:
                        business_reports.create(body, self.admin)
                    self.assertEqual(caught.exception.code, "conflict")
                    self.assertIn("尚未接入", str(caught.exception))
            workflow.assert_not_called()
            provider.assert_not_called()
        self.assertEqual(before, (m.AiReportRun.objects.count(), m.AiWorkflowRuns.objects.count()))
