"""Isolated PG: exact-column AI reader observes non-secret model settings."""
from copy import deepcopy
import secrets
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from access_control.models import AccessRole, AppUser

from . import business_market_v2_model_transport_owner as service
from . import models as m
from .database_contract import provision
from .policy import AiError, Principal, canonical
from .test_business_market_v2_material_role_bridge import session_role


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class MarketV2ModelTransportOwnerTests(djtest.TransactionTestCase):
    def setUp(self):
        AccessRole.objects.get_or_create(code="admin", defaults={
            "label": "admin", "description": "admin", "rank": 3,
            "permissions": []})
        AccessRole.objects.get_or_create(code="viewer", defaults={
            "label": "viewer", "description": "viewer", "rank": 0,
            "permissions": []})
        now = timezone.now()
        AppUser.objects.create(email="model-owner@example.invalid",
            display_name="model-owner", role_id="admin", scope=None,
            created_at=now, updated_at=now)
        AppUser.objects.create(email="model-viewer@example.invalid",
            display_name="model-viewer", role_id="viewer", scope=None,
            created_at=now, updated_at=now)
        self.admin = Principal("model-owner@example.invalid", "model-owner",
            "admin", None)
        self.viewer = Principal("model-viewer@example.invalid", "model-viewer",
            "viewer", None)
        self.model = m.AiModels.objects.create(id="market-fingerprint-model",
            version=4, name="Synthetic transport", protocol="openai_compatible",
            model_type="text", model_name="fictional-v1",
            base_url="https://MODEL.example.invalid:443/v1/",
            generation_options_json=canonical({"temperature": 0.2}),
            api_key_encrypted="sentinel-ciphertext-must-not-be-read",
            api_key_suffix="read", status="enabled", timeout_ms=60000,
            max_tokens=4096, reasoning_mode="auto", temperature_milli=200,
            max_tool_rounds=5, max_total_tool_calls=15)
        connection.ensure_connection()
        provision(connection.connection, secrets.token_hex(32),
            secrets.token_hex(32))
        # Prove this reader needs no ai_models table-level SELECT or key column.
        with connection.cursor() as cursor:
            cursor.execute("REVOKE SELECT ON TABLE public.ai_models "
                "FROM teruisi_ai_reader")
            cursor.execute("GRANT SELECT (" + ",".join(service.DB_FIELDS) +
                ") ON TABLE public.ai_models TO teruisi_ai_reader")

    def test_real_reader_projects_exact_columns_and_never_calls_provider(self):
        with session_role(service.READER), djtest.override_settings(
                AI_MARKET_V2_MODEL_TRANSPORT_OWNER_ENABLED=True), \
                patch("ai_assistant.provider.turn") as provider, \
                CaptureQueriesContext(connection) as captured:
            result = service.read(self.model.id, self.admin)
            provider.assert_not_called()
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT api_key_encrypted FROM public.ai_models "
                        "WHERE id=%s", [self.model.id])
        self.assertEqual(result["modelVersion"], 4)
        self.assertEqual(result["transport"]["canonicalBaseUrl"],
            "https://model.example.invalid/v1")
        self.assertEqual(result["transport"]["timeoutMs"], 60000)
        self.assertEqual(result["transport"]["reasoningMode"], "auto")
        self.assertEqual(result["transport"]["temperatureMilli"], 200)
        self.assertTrue(result["currentModelOwnedRead"])
        self.assertTrue(result["nonSecretColumnsDoubleChecked"])
        for field in ("credentialAccountVerified", "providerIdentityVerified",
                "rateSourceIndependentlyVerified", "humanCapApproved",
                "providerCallsAllowed"):
            self.assertFalse(result[field], field)
        text = repr(result)
        self.assertNotIn("sentinel-ciphertext", text)
        # The deliberate denial query above is excluded from this SQL check.
        model_queries = [item["sql"] for item in captured.captured_queries
            if 'FROM "ai_models"' in item["sql"]]
        self.assertEqual(len(model_queries), 2)
        self.assertTrue(all("api_key_encrypted" not in sql
            and "api_key_suffix" not in sql for sql in model_queries))

    def test_default_off_wrong_role_and_model_drift_are_closed(self):
        with self.assertRaisesRegex(AiError, "未启用"):
            service.read(self.model.id, self.admin)
        with djtest.override_settings(
                AI_MARKET_V2_MODEL_TRANSPORT_OWNER_ENABLED=True):
            with self.assertRaisesRegex(AiError, "AI reader"):
                service.read(self.model.id, self.admin)
            with session_role(service.READER):
                with self.assertRaises(AiError):
                    service.read(self.model.id, self.viewer)
                before = service._row(self.model.id)
                for field, value in (("temperature_milli", 201),
                        ("version", 5)):
                    after = deepcopy(before)
                    after[field] = value
                    with self.subTest(field=field), patch.object(service,
                            "_row", side_effect=[before, after]):
                        with self.assertRaisesRegex(AiError, "复核期间变化"):
                            service.read(self.model.id, self.admin)
        self.assertEqual(m.AiAgentProviderDispatches.objects.count(), 0)
        self.assertEqual(m.AiAgentToolDispatches.objects.count(), 0)
