from unittest.mock import patch
from django.test import TestCase, override_settings
from . import business_evidence as evidence, business_planning, business_reports, workflows, models as m
from . import test_business_evidence as evidence_fixtures, test_business_collection as collection_fixtures
from .policy import AiError


@override_settings(DJANGO_PROCESS_ROLE='development', DJANGO_ENVIRONMENT='test')
class BusinessWorkbenchFlowTests(TestCase):
    setUp = evidence_fixtures.BusinessEvidenceTests.setUp
    user = evidence_fixtures.BusinessEvidenceTests.user
    call = evidence_fixtures.BusinessEvidenceTests.call
    execute = collection_fixtures.AutomaticCollectionTests.execute
    tick = collection_fixtures.AutomaticCollectionTests.tick

    def test_preview_background_collection_manual_dry_analysis_and_server_recovery(self):
        request = {'question':'分析本店销售变化并解释缺少同期记录的影响',
            'startDate':self.query['startDate'],'endDate':self.query['endDate'],
            'shops':[{'platform':self.query['platform'],'shop':self.query['shop'],'datasets':[],
                      'salesChannels':[self.query['channel']]}],
            'windows':['current','previous','yearAgo'],'markets':[]}
        preview = business_planning.preview(request,self.admin)
        self.assertTrue(preview['canCollect'])
        body = {'clientRequestId':'workbench-synthetic',**preview['evidenceRequest'],'expectedPrincipalKey':preview['principalKey']}
        run_id = evidence.create(body,self.admin)['item']['id']
        self.catalog.append({**self.catalog[0],'name':'get_business_source_page'})
        with patch('ai_assistant.provider.turn') as model:
            self.tick()
            row = evidence.get_run(run_id,self.admin)
            self.assertEqual(row.status,'sealed')
            self.assertFalse(m.AiWorkflowRuns.objects.exists())
            self.assertFalse(m.AiAgentJobs.objects.exists())
            detail = evidence.detail(run_id,self.admin)
            self.assertEqual(detail['item']['plan']['analysisRequest']['question'],request['question'])
            self.assertEqual(sorted(v['rowCount'] for v in detail['item']['sources'].values()),[0,0,12])
            restored = evidence.listing({'clientRequestId':'workbench-synthetic'},self.admin)
            self.assertEqual(restored['items'][0]['id'],run_id)
            launch = {'clientRequestId':'workbench-analysis','evidenceRunId':run_id,'question':request['question'],
                      'dryRun':True,'expectedPrincipalKey':preview['principalKey']}
            with patch('ai_assistant.transport.catalog',return_value=self.catalog):
                report=business_reports.create(launch,self.admin)['item']
                self.assertTrue(business_reports.create(launch,self.admin)['replayed'])
            for _ in range(9): workflows.workflow_tick()
            self.assertEqual(m.AiWorkflowRuns.objects.get(pk=report['workflowId']).status,'completed')
            self.assertEqual(evidence.detail(run_id,self.admin)['reports'][0]['id'],report['id'])
            model.assert_not_called()

    def test_account_binding_rejects_before_evidence_lookup_or_report_dispatch(self):
        with self.assertRaises(AiError) as context:
            business_reports.create({'clientRequestId':'wrong-account','evidenceRunId':'not-looked-up',
                'question':'合成问题','dryRun':False,'expectedPrincipalKey':'0'*64},self.admin)
        self.assertEqual(context.exception.status,403)
        self.assertFalse(m.AiWorkflowRuns.objects.exists())
