"""Read-only bridge from one persisted calling dispatch to an owning tool.

The signed internal caller supplies a dispatch primary key and the provider's
exact name/arguments/call ID. None is authority by itself: every value is
compared with the current job lease and saved provider/tool ledgers. This
module writes no result or Agent reading receipt.

The prior tool prefix is checked for continuous known results and intact
digests here. It is not semantically replayed by this one-call bridge. Before
any persisted tool output is sent back to a model, the provider-turn path must
run a separate bounded four-tool ledger verifier against owning readers.
"""
from copy import deepcopy

from . import business_promotion_agent_tool as keyword_tool
from . import business_promotion_read_receipts as receipts
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as contract
from . import business_promotion_tools as tools
from . import models as m
from .policy import AiError, canonical, current_principal, digest, identifier, passive

OPERATIONS = {contract.PACKAGE_TOOL:"package", contract.TABLE_TOOL:"analysis",
    contract.BUDGET_TOOL:"budget"}


def _reject(message="词货工具派发与当前Agent或模型回执不一致", code="conflict", status=409):
    raise AiError(message, code, status)


def _arguments(value):
    if type(value) is not dict:
        _reject("工具参数必须为对象", "invalid_request", 400)
    try:
        value = passive(value, receipts.MAX_ARGUMENT_BYTES)
        encoded = canonical(value)
        if len(encoded.encode("utf-8")) > receipts.MAX_ARGUMENT_BYTES:
            _reject("工具参数超出固定容量", "payload_too_large", 413)
        return value, encoded
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AiError("工具参数不是有界规范JSON", "invalid_request", 400) from error


def _current(dispatch_id, name, arguments, provider_call_id, principal):
    current_principal(principal, admin=True)
    if principal.scope is not None:
        _reject("词货派发只允许无范围管理员", "access_denied", 403)
    if type(name) is not str or name not in contract.TOOLS:
        _reject("工具名称不属于固定四项", "invalid_request", 400)
    if type(provider_call_id) is not str or not 1 <= len(provider_call_id) <= 200:
        _reject("模型调用ID无效", "invalid_request", 400)
    arguments, encoded = _arguments(arguments)
    dispatch = m.AiAgentToolDispatches.objects.filter(pk=identifier(dispatch_id, "dispatchId")).first()
    if dispatch is None:
        _reject("工具派发不存在", "not_found", 404)
    report, role, job_guard = tools._job(dispatch.job_id, principal)
    job = m.AiAgentJobs.objects.get(pk=dispatch.job_id)
    fixed = runtime.bound_persisted(report.id, principal)
    if (fixed["contentReady"] is not True or fixed["screeningStatus"] != "ready"
            or dispatch.state != "calling"
            or dispatch.tool_name != name or dispatch.provider_call_id != provider_call_id
            or dispatch.arguments_json != encoded or dispatch.arguments_digest != digest(arguments)
            or dispatch.lease_epoch != job.lease_epoch
            or m.AiAgentToolResults.objects.filter(tool_dispatch_id=dispatch.id).exists()):
        _reject("词货工具派发不是当前精确待调用记录")
    if name == contract.PROMOTION_TOOL:
        if role not in contract.PROMOTION_ROLES:
            _reject("实际Agent角色无权读取词货视图", "access_denied", 403)
        if keyword_tool._active(job.id, principal)[0] != report.id:
            _reject("第四工具的实际Agent绑定不一致")
    providers = receipts._providers(job)
    provider = providers.get(dispatch.provider_dispatch_id)
    if provider is None or provider[0].job_id != job.id:
        _reject("工具派发没有已知成功的同Agent模型回执")
    saved_call = provider[1].get(provider_call_id)
    if (saved_call is None or saved_call["name"] != name
            or canonical(saved_call["arguments"]) != encoded):
        _reject("模型回执工具名称、调用ID或参数与派发不一致")
    rows = list(m.AiAgentToolDispatches.objects.filter(job_id=job.id).order_by(
        "tool_call_ordinal")[:receipts.MAX_TOOLS + 1])
    if (len(rows) > receipts.MAX_TOOLS or not rows or rows[-1].id != dispatch.id
            or dispatch.tool_call_ordinal != len(rows) or job.tool_call_count != len(rows)-1
            or job.provider_round_count != len(providers)):
        _reject("词货工具连续派发计数或当前序号不一致")
    for ordinal, prior in enumerate(rows[:-1], 1):
        result = m.AiAgentToolResults.objects.filter(tool_dispatch_id=prior.id).first()
        # This is a continuity fence, not semantic authorization of earlier
        # output; provider-turn reconstruction must replay the saved prefix.
        if (prior.job_id != job.id or prior.tool_call_ordinal != ordinal
                or prior.state != "succeeded" or result is None
                or digest(result.result_json) != result.result_digest):
            _reject("先前工具派发结果未知或账本不连续", "tool_dispatch_unknown")
    ledger = receipts._ledger_fence(job.id)
    guard = {"dispatchId":dispatch.id, "jobId":job.id, "role":role,
        "reportId":report.id, "providerId":dispatch.provider_dispatch_id,
        "invocationId":dispatch.invocation_id, "name":name,
        "argumentsJson":encoded, "providerCallId":provider_call_id,
        "leaseEpoch":job.lease_epoch, "jobGuard":job_guard,
        "bound":fixed, "ledgerDigest":ledger}
    return job.id, report.id, guard


def read(dispatch_id, name, arguments, provider_call_id, principal):
    """Return one owning result, leaving the calling dispatch for its writer."""
    job_id, report_id, guard = _current(dispatch_id, name, arguments, provider_call_id, principal)
    if name == contract.PROMOTION_TOOL:
        result = keyword_tool.read(job_id, arguments, principal)
    else:
        result = tools.read(job_id, OPERATIONS[name], arguments, principal)
    # A successful owning context has exited; a concurrent lease, account,
    # provider or dispatch change must discard its uncommitted response.
    if _current(dispatch_id, name, arguments, provider_call_id, principal) != (job_id, report_id, guard):
        _reject("词货工具执行期间实际派发或封存根已变化")
    return deepcopy(result)
