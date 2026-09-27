"""Default-closed temporary v11 gate; SimpleTestCase forbids DB access."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest

from . import business_promotion_budget_v11_slim_stage_candidate as candidate
from .policy import AiError


class BudgetV11TemporaryStageTests(djtest.SimpleTestCase):
    @djtest.override_settings(AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED=False)
    def test_default_closed_before_any_owning_read(self):
        with patch.object(candidate, "import_module",
                side_effect=AssertionError("must not read")):
            with self.assertRaises(AiError):
                with candidate.open_unpublished("synthetic", SimpleNamespace()):
                    pass

    @djtest.override_settings(AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED=True)
    def test_only_exact_unpublished_v11_proof_can_leave_temporary_context(self):
        @contextmanager
        def prepared(*_args, **kwargs):
            self.assertEqual(kwargs["renderer_version"], 11)
            yield SimpleNamespace(manifest={"rendererVersion": 11,
                "promotionSlimProof": {"candidateOnly": True},
                "htmlPayloadVersion": 2, "deliveryAuthorized": False})

        with patch.object(candidate, "import_module",
                return_value=SimpleNamespace(_open_versioned=prepared)):
            with candidate.open_unpublished("synthetic", SimpleNamespace()) as item:
                self.assertEqual(item.manifest["htmlPayloadVersion"], 2)

        @contextmanager
        def forged(*_args, **_kwargs):
            yield SimpleNamespace(manifest={"rendererVersion": 11,
                "promotionSlimProof": {"candidateOnly": True},
                "htmlPayloadVersion": 2, "deliveryAuthorized": True})

        with patch.object(candidate, "import_module",
                return_value=SimpleNamespace(_open_versioned=forged)):
            with self.assertRaises(AiError):
                with candidate.open_unpublished("synthetic", SimpleNamespace()):
                    pass
