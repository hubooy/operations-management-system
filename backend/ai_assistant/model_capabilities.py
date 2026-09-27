"""Explicit endpoint options; never infer capabilities from a model name."""
import json
import math
from .policy import AiError, canonical, fields, integer, text, choice, boolean

MAX_REPLY_CHARACTERS = 524288
MAX_PROVIDER_BYTES = 8 * 1024 * 1024
MAX_PROVIDER_STREAM_BYTES = 64 * 1024 * 1024  # SSE repeats protocol metadata per delta.
MAX_CHAT_SECONDS = 1_000_000
DEFAULTS = {
    "contextWindowTokens": 128000, "taskTimeoutMs": MAX_CHAT_SECONDS * 1000,
    "outputTokenParameter": "max_tokens", "temperatureMode": "custom",
    "reasoningFormat": "default", "reasoningEffort": "default",
    "thinkingBudgetTokens": 4096, "includeStreamUsage": False, "systemPrompt": "",
}


def options(model):
    try:
        value = json.loads(getattr(model, "generation_options_json", "{}"))
        if not isinstance(value, dict) or set(value) - set(DEFAULTS): raise ValueError()
        return {**DEFAULTS, "taskTimeoutMs": 260000, **value}  # Preserve pre-option configurations.
    except (ValueError, TypeError) as error:
        raise AiError("模型生成参数损坏，请重新保存配置", "invalid_request", 400) from error


def validate(value, *, protocol, max_tokens, reasoning_mode):
    if not isinstance(value, dict):
        raise AiError("生成参数必须为对象")
    fields(value, set(DEFAULTS))
    result = {**DEFAULTS, **value}
    result["contextWindowTokens"] = integer(result["contextWindowTokens"], "上下文窗口", 8192, 2000000)
    result["taskTimeoutMs"] = integer(result["taskTimeoutMs"], "任务总时限", 30000, MAX_CHAT_SECONDS * 1000)
    result["outputTokenParameter"] = choice(result["outputTokenParameter"], ["max_tokens", "max_completion_tokens"], "输出参数格式")
    result["temperatureMode"] = choice(result["temperatureMode"], ["default", "custom"], "温度模式")
    result["reasoningFormat"] = choice(result["reasoningFormat"], ["default", "thinking", "reasoning_effort", "anthropic_budget", "anthropic_adaptive"], "推理参数格式")
    result["reasoningEffort"] = choice(result["reasoningEffort"], ["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"], "推理强度")
    result["thinkingBudgetTokens"] = integer(result["thinkingBudgetTokens"], "思考预算", 1024, 131071)
    result["includeStreamUsage"] = boolean(result["includeStreamUsage"], "流式用量统计")
    result["systemPrompt"] = text(result["systemPrompt"], "附加系统提示词", 8000, empty=True)
    reserve = max(2048, math.ceil(result["contextWindowTokens"] * .05))
    if max_tokens + reserve + 1024 > result["contextWindowTokens"]:
        raise AiError("上下文窗口须容纳最大输出、安全余量及至少 1024 Token 输入空间")
    mode = result["reasoningFormat"]
    if protocol == "anthropic":
        if result["outputTokenParameter"] != "max_tokens" or mode in {"thinking", "reasoning_effort"} or result["includeStreamUsage"]:
            raise AiError("所选参数格式不适用于 Anthropic 协议")
    elif mode.startswith("anthropic_"):
        raise AiError("Anthropic 推理格式不能用于 OpenAI 兼容协议")
    if mode in {"anthropic_budget", "anthropic_adaptive"} and (reasoning_mode == "disabled" or result["temperatureMode"] != "default"):
        raise AiError("启用 Anthropic 思考时请跟随默认温度，且不能同时关闭推理")
    if mode == "anthropic_budget" and result["thinkingBudgetTokens"] >= max_tokens:
        raise AiError("思考预算必须小于最大输出 Token，给正文保留空间")
    if mode not in {"reasoning_effort", "thinking", "anthropic_adaptive"} and result["reasoningEffort"] != "default":
        raise AiError("当前推理格式不支持强度参数")
    if mode == "anthropic_adaptive" and result["reasoningEffort"] not in {"default", "low", "medium", "high", "max"}:
        raise AiError("自适应思考请使用默认、低、中、高或最大强度")
    if reasoning_mode == "disabled" and result["reasoningEffort"] not in {"default", "none"}:
        raise AiError("关闭推理时不能设置更高推理强度")
    return result


def provider_parameters(model):
    cfg = options(model)
    values = {cfg["outputTokenParameter"]: model.max_tokens}
    if cfg["temperatureMode"] == "custom":
        values["temperature"] = model.temperature_milli / 1000
    mode, effort = cfg["reasoningFormat"], cfg["reasoningEffort"]
    if mode == "default":
        if model.protocol == "openai_compatible" and model.reasoning_mode == "disabled":
            values["thinking"] = {"type": "disabled"}  # Preserve existing endpoint contract.
    elif mode == "thinking":
        values["thinking"] = {"type": "disabled" if model.reasoning_mode == "disabled" else "enabled"}
        if effort != "default": values["reasoning_effort"] = effort
    elif mode == "reasoning_effort":
        if model.reasoning_mode == "disabled": values["reasoning_effort"] = "none"
        elif effort != "default": values["reasoning_effort"] = effort
    elif mode == "anthropic_budget":
        values["thinking"] = {"type": "enabled", "budget_tokens": cfg["thinkingBudgetTokens"]}
    elif mode == "anthropic_adaptive":
        values["thinking"] = {"type": "adaptive"}
        if effort != "default": values["output_config"] = {"effort": effort}
    return values


def estimate_tokens(value):
    # Conservative UTF-8 heuristic, not a provider tokenizer or actual billing.
    return math.ceil(len(canonical(value).encode("utf-8")) / 2) + 16


def fit_context(model, transcript, system, tools):
    cfg = options(model)
    window = cfg["contextWindowTokens"]
    reserve = max(2048, math.ceil(window * .05))
    budget = window - model.max_tokens - reserve
    frames = list(transcript)
    # Discard complete old user turns only. Never split live tool calls/results.
    starts = [i for i, f in enumerate(frames) if f.get("role") == "user" and isinstance(f.get("content"), str)]
    dropped = 0
    if starts and starts[0] > 0:
        dropped = starts[0]
        frames = frames[dropped:]
        starts = [i - dropped for i in starts]
    def estimate(): return estimate_tokens({"system": system, "messages": frames, "tools": tools})
    while estimate() > budget and len(starts) > 1:
        count = starts[1]
        frames = frames[count:]; dropped += count
        starts = [i - count for i in starts[1:]]
    used = estimate()
    if used > budget or len(canonical(frames).encode()) > 4 * 1024 * 1024:
        raise AiError("当前问题与工具结果超过上下文预算，请增大上下文窗口、减小输出额度或缩小查询范围", "ai_context_budget_exceeded", 400)
    return frames, {"estimatedInputTokens": used, "contextWindowTokens": window, "reservedOutputTokens": model.max_tokens,
                    "droppedMessages": dropped, "tokenCountMethod": "utf8-estimate"}


def usage_numbers(usage):
    usage = usage if isinstance(usage, dict) else {}
    def number(value): return value if type(value) is int and 0 <= value <= 100000000 else None
    detail = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    return {"inputTokens": number(usage.get("prompt_tokens", usage.get("input_tokens"))),
            "outputTokens": number(usage.get("completion_tokens", usage.get("output_tokens"))),
            "reasoningTokens": number(detail.get("reasoning_tokens", detail.get("thinking_tokens"))) if isinstance(detail, dict) else None}
