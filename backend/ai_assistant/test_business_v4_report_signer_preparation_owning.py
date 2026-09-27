"""Test-only owner preparation never invokes the legacy HMAC signer."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase,override_settings

from business_analysis.test_v4_report_signer_preparation_v1 import fixture

from . import business_v4_report_signer_preparation_owning as owning
from . import business_v4_seal_hmac as legacy_hmac
from .policy import AiError


@override_settings(DJANGO_ENVIRONMENT="test",
    DJANGO_PROCESS_ROLE="development")
class V4ReportSignerOwnerTests(SimpleTestCase):
    def test_real_owner_call_then_block_without_legacy_signer(self):
        principal=SimpleNamespace(email="admin@example.test",scope=None)
        with patch.object(owning.source_owner,"inspect_candidate",
                return_value=fixture()) as scanned, \
                patch.object(legacy_hmac,"sign",
                    side_effect=AssertionError("old signing oracle called")) as old:
            value=owning.inspect_candidate("v4-run",principal,enabled=True)
        scanned.assert_called_once()
        old.assert_not_called()
        self.assertEqual(value["status"],
            "blocked_no_finance_mapping_or_new_signer")
        self.assertTrue(value["sourceOwnerCalledInThisOperation"])
        self.assertFalse(value["keyMaterialLoaded"])
        self.assertFalse(value["reportCapableSealIssued"])
        self.assertFalse(value["downloadSupported"])

    def test_default_bad_role_or_forged_owner_receipt_refuse(self):
        principal=SimpleNamespace(email="admin@example.test",scope=None)
        with patch.object(owning.source_owner,"inspect_candidate") as scanned:
            with self.assertRaises(AiError):
                owning.inspect_candidate("v4-run",principal)
            scanned.assert_not_called()
        with override_settings(DJANGO_ENVIRONMENT="production"), \
                self.assertRaises(AiError):
            owning.inspect_candidate("v4-run",principal,enabled=True)
        forged=fixture()
        forged["resultDigest"]="0"*64
        with patch.object(owning.source_owner,"inspect_candidate",
                return_value=forged), self.assertRaises(AiError):
            owning.inspect_candidate("v4-run",principal,enabled=True)
