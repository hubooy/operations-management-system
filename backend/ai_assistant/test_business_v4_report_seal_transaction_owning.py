"""Two real owner paths are required before a still-blocked ticket proposal."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase,override_settings

from business_analysis.test_v4_report_seal_transaction_protocol_v1 import (
    fixture)

from . import business_v4_report_seal_transaction_owning as owning
from .policy import AiError


@override_settings(DJANGO_ENVIRONMENT="test",
    DJANGO_PROCESS_ROLE="development")
class V4ReportSealTransactionOwnerTests(SimpleTestCase):
    def test_both_owner_rechecks_then_no_ticket_claim_or_signer(self):
        linked,streams,_=fixture()
        principal=SimpleNamespace(email="admin@example.test",scope=None)
        with patch.object(owning.linked_owner,"inspect_candidate",
                return_value=linked) as link, \
                patch.object(owning.stream_owner,"inspect_candidate",
                    return_value=streams) as replay:
            value=owning.inspect_candidate("report-one","v4-run",
                principal,enabled=True)
        link.assert_called_once()
        replay.assert_called_once()
        self.assertEqual(value["status"],
            "blocked_no_protected_finance_map_or_v2_signer")
        self.assertEqual(value["reportId"],"report-one")
        self.assertTrue(value["bothOwnerPathsInvoked"])
        for key in ("protectedTicketIssued","protectedClaimed",
                    "protectedSealCommitted","protectedConsumptionRecorded",
                    "authorityVerified","reportGenerationSupported",
                    "rendererRegistered","downloadSupported"):
            self.assertIs(value[key],False)

    def test_cross_report_run_and_default_roles_refuse(self):
        linked,streams,_=fixture()
        principal=SimpleNamespace(email="admin@example.test",scope=None)
        bad=deepcopy(linked)
        bad["v4RunId"]="other-run"
        from business_analysis.contracts import digest
        bad["resultDigest"]=digest({key:item for key,item in bad.items()
            if key!="resultDigest"})
        with patch.object(owning.linked_owner,"inspect_candidate",
                return_value=bad), \
                patch.object(owning.stream_owner,"inspect_candidate",
                    return_value=streams), self.assertRaises(AiError):
            owning.inspect_candidate("report-one","v4-run",principal,
                enabled=True)
        with patch.object(owning.linked_owner,"inspect_candidate") as link:
            with self.assertRaises(AiError):
                owning.inspect_candidate("report-one","v4-run",principal)
            link.assert_not_called()
        with override_settings(DJANGO_ENVIRONMENT="production"), \
                self.assertRaises(AiError):
            owning.inspect_candidate("report-one","v4-run",principal,
                enabled=True)
