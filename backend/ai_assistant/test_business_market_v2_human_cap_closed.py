"""The public owner entry cannot inspect or mutate when its gate is closed."""
from unittest import TestCase
from unittest.mock import patch

from django.test import override_settings

from . import business_market_v2_human_cap_owner as owner
from .policy import AiError


class HumanCapClosedTests(TestCase):
    def test_all_entry_points_fail_before_database_or_provider_use(self):
        with override_settings(AI_MARKET_V2_HUMAN_CAP_ENABLED=False,
                DJANGO_PROCESS_ROLE="ai_writer"), patch.object(
                    owner.connection, "cursor") as cursor:
            for operation, args in ((owner.preview, ("a" * 64, None)),
                    (owner.approve, ("a" * 64, {}, None)),
                    (owner.revoke, ("a" * 64, {}, None)),
                    (owner.outcome, ("a" * 64, None))):
                with self.subTest(operation=operation.__name__), self.assertRaises(AiError):
                    operation(*args)
            cursor.assert_not_called()
