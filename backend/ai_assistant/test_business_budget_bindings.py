"""Lightweight download binding and full calculation remain distinct."""
from copy import deepcopy
from unittest.mock import patch
from django.test import TestCase, override_settings
from . import test_business_budget_store as fixtures
from . import business_budget, business_budget_store as store, models as m
from .business_sealed import Reader
from .policy import AiError


@override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class BusinessBudgetBindingTests(TestCase):
    user = fixtures.BusinessBudgetStoreTests.user
    call = fixtures.BusinessBudgetStoreTests.call
    setUp = fixtures.BusinessBudgetStoreTests.setUp

    def test_light_binding_does_not_consume_facts_and_copies_are_detached(self):
        with patch.object(business_budget,"resolve",side_effect=AssertionError("light binding cannot calculate")), \
                patch.object(Reader,"pages",side_effect=AssertionError("light binding cannot scan facts")):
            fixed=store.binding_for_report(self.report,self.admin)
        self.assertEqual(type(fixed),store.BudgetBinding)
        self.assertFalse(hasattr(fixed,"result"))
        self.assertEqual(fixed.reference,self.prepared.reference)
        changed=fixed.plan;changed["targets"].clear()
        changed=fixed.binding;changed["catalogDigest"]="0"*64
        self.assertEqual(fixed.plan,self.prepared.plan)
        self.assertEqual(fixed.binding,self.prepared.binding)

    def test_full_load_reconciles_once_after_light_validation(self):
        with patch.object(business_budget,"resolve",wraps=business_budget.resolve) as calculate:
            full=store.load(self.report,self.admin)
        self.assertEqual(calculate.call_count,1)
        self.assertEqual(full.result,self.prepared.result)
        with patch.object(business_budget,"resolve",side_effect=AiError("synthetic changed facts")),self.assertRaises(AiError):
            store.load(self.report,self.admin)

    def test_light_binding_still_rejects_changed_parameter_digest_or_workflow_owner(self):
        row=m.AiBusinessBudgetPlan.objects.get(pk=self.prepared.id)
        altered=deepcopy(row);altered.binding_digest="0"*64
        with patch.object(m.AiBusinessBudgetPlan.objects,"filter") as plans:
            plans.return_value.first.return_value=altered
            with self.assertRaises(AiError):store.binding_for_report(self.report,self.admin)
        report=deepcopy(self.report);report.workflow.owner_email="other@example.invalid"
        with patch.object(m.AiReportRun.objects,"filter") as reports:
            reports.return_value.select_related.return_value.first.return_value=report
            with self.assertRaises(AiError):store.binding_for_report(report,self.admin)
