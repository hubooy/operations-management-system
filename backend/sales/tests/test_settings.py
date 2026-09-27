from __future__ import annotations

import os
from unittest.mock import patch

from django.test import SimpleTestCase

from teruisi_backend import settings as service_settings


class DeploymentSettingsTests(SimpleTestCase):
    def test_security_boolean_configuration_is_strict(self):
        with patch.dict(os.environ, {"STRICT_BOOLEAN_TEST": "treu"}):
            with self.assertRaisesRegex(RuntimeError, "明确的布尔值"):
                service_settings.env_bool("STRICT_BOOLEAN_TEST")

    def test_production_secrets_reject_short_and_placeholder_values(self):
        for value in ("short", "replace-with-at-least-32-random-bytes", "x" * 64):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "至少 32 字节"):
                    service_settings.validate_secret("TEST_SECRET", value, required=True)

    def test_production_secret_accepts_non_placeholder_32_byte_value(self):
        value = "aB3!" * 8
        self.assertEqual(
            service_settings.validate_secret("TEST_SECRET", value, required=True), value
        )

    def test_production_database_is_fixed_to_loopback_postgres(self):
        with patch.object(service_settings, "DJANGO_ENVIRONMENT", "production"):
            with self.assertRaisesRegex(RuntimeError, "127.0.0.1:5432"):
                service_settings.database_from_url(
                    "postgresql://reader:strong-password@localhost:5432/teruisi_sales"
                )
            configuration = service_settings.database_from_url(
                "postgresql://reader:strong-password@127.0.0.1:5432/teruisi_sales"
            )
        self.assertTrue(configuration["CONN_HEALTH_CHECKS"])
        self.assertEqual(configuration["HOST"], "127.0.0.1")
