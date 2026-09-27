"""Default-closed, temporary renderer-11 staging candidate.

No AiBusinessFileRun row, chunk, ready receipt, download route or production
registration exists for version 11. Current SQL allows only renderer <=10.
The caller may inspect these short-lived files in an isolated rehearsal; a
future migration must separately establish durable stage and publication.
"""
from contextlib import contextmanager
from importlib import import_module

from django.conf import settings

from .policy import AiError


@contextmanager
def open_unpublished(report_id, principal, *, checkpoint=None,
                     material_limits=None, max_tables=120,
                     max_rows=1_000_000, max_volumes=100):
    if getattr(settings, "AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED", False) is not True:
        raise AiError("renderer 11 压缩HTML暂存候选默认关闭", "conflict", 409)
    approved_budget = import_module("ai_assistant.business_promotion_budget_v10_volumes")
    with approved_budget._open_versioned(report_id, principal,
            renderer_version=11, checkpoint=checkpoint,
            material_limits=material_limits, max_tables=max_tables,
            max_rows=max_rows, max_volumes=max_volumes) as prepared:
        if (prepared.manifest["rendererVersion"] != 11 or
                prepared.manifest["promotionSlimProof"]["candidateOnly"] is not True or
                prepared.manifest["htmlPayloadVersion"] != 2 or
                prepared.manifest["deliveryAuthorized"] is not False):
            raise AiError("renderer 11 临时证明或默认关闭状态无效", "conflict", 409)
        yield prepared
