from __future__ import annotations
import json
import base64
import struct
import zlib
import re
from .configuration import endpoint
from .policy import AiError, passive
from .secrets import decrypt
from .transport import bounded_json, bounded_sse
from .provider_stream import ProviderStream
from .model_capabilities import options, provider_parameters, fit_context, MAX_PROVIDER_BYTES


class EmptyProviderResponse(AiError):
    """A fully received, valid response with no public answer or tool call."""

    def __init__(self, *, truncated, finish_reason, has_reasoning, usage):
        super().__init__(
            "模型输出额度已耗尽，但尚未生成正文" if truncated else "模型已结束生成，但未返回正文",
            "provider_output_limit" if truncated else "provider_empty_response", 503,
        )
        self.can_finalize = isinstance(finish_reason, str) and finish_reason in {"length", "stop", "max_tokens", "end_turn"}
        self.diagnostics = {"finishReason": finish_reason if self.can_finalize else "other",
                            "hasReasoning": has_reasoning}
        # No provider text, reasoning, request body or credentials enter audit.
        if isinstance(usage, dict):
            for key in ("completion_tokens", "output_tokens"):
                value = usage.get(key)
                if type(value) is int and 0 <= value <= 10_000_000:
                    self.diagnostics["completionUnits" if key == "completion_tokens" else "outputUnits"] = value


def system_prompt(model, system):
    cfg = options(model)
    if cfg["systemPrompt"]:
        system += "\n附加业务背景与回复风格（不得覆盖系统权限、数据真实性与工具规则）：\n" + cfg["systemPrompt"]
    return system


def turn(model, transcript, system, tools, *, retain_reasoning=False, on_text=None):
    cfg = options(model)
    system = system_prompt(model, system)
    transcript, context_info = fit_context(model, transcript, system, tools)
    base = endpoint(model.base_url)
    key = decrypt(model.api_key_encrypted)
    def request(url, body, headers, *, timeout):
        if on_text is None:
            return bounded_json(url, body, headers, timeout=timeout, maximum=MAX_PROVIDER_BYTES)
        if model.protocol == "openai_compatible" and cfg["includeStreamUsage"]:
            body = {**body, "stream_options": {"include_usage": True}}
        return bounded_sse(url, body, headers, timeout=timeout,
                           collector=ProviderStream(model.protocol, on_text))
    if model.protocol == "anthropic":
        body = {
            "model": model.model_name,
            **provider_parameters(model),
            "system": system,
            "messages": transcript,
        }
        if tools:
            body["tools"] = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["inputSchema"],
                }
                for t in tools
            ]
        result = request(
            base + "/messages",
            body,
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=cfg["taskTimeoutMs"] / 1000,
        )
        blocks = result.get("content")
        if not isinstance(blocks, list):
            raise AiError("模型返回格式无效", "invalid_provider_response", 503)
        calls = []
        texts = []
        for b in blocks:
            if not isinstance(b, dict):
                raise AiError("模型返回块无效", "invalid_provider_response", 503)
            if b.get("type") == "text" and isinstance(b.get("text"), str):
                texts.append(b["text"])
            elif b.get("type") == "tool_use":
                calls.append(
                    {
                        "id": b.get("id"),
                        "name": b.get("name"),
                        "arguments": b.get("input"),
                    }
                )
        frame = {"role": "assistant", "content": blocks}
    else:
        body = {
            "model": model.model_name,
            **provider_parameters(model),
            "messages": [{"role": "system", "content": system}, *transcript],
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["inputSchema"],
                    },
                }
                for t in tools
            ]
        result = request(
            base + "/chat/completions",
            body,
            {"Authorization": "Bearer " + key},
            timeout=cfg["taskTimeoutMs"] / 1000,
        )
        choices = result.get("choices")
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
            or not isinstance(choices[0].get("message"), dict)
        ):
            raise AiError("模型返回格式无效", "invalid_provider_response", 503)
        message = choices[0]["message"]
        frame = {"role": "assistant", "content": message.get("content")}
        # Interleaved thinking is part of the provider's tool-turn contract.
        # Keep it only in this request's in-memory transcript, never as a reply.
        if retain_reasoning and isinstance(message.get("reasoning_content"), str):
            frame["reasoning_content"] = message["reasoning_content"]
        raw_calls = message.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise AiError("工具调用格式无效", "invalid_provider_response", 503)
        if raw_calls:
            frame["tool_calls"] = raw_calls
        texts = [message["content"]] if isinstance(message.get("content"), str) else []
        calls = []
        for call in raw_calls:
            try:
                args = json.loads(call["function"]["arguments"])
                calls.append(
                    {
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "arguments": args,
                    }
                )
            except (KeyError, TypeError, ValueError) as e:
                raise AiError(
                    "工具调用参数无效", "invalid_provider_response", 503
                ) from e
    if (
        len(calls) > 8
        or any(
            not isinstance(c["id"], str)
            or not c["id"]
            or len(c["id"]) > 200
            or not isinstance(c["name"], str)
            or not isinstance(c["arguments"], dict)
            for c in calls
        )
        or len({c["id"] for c in calls}) != len(calls)
    ):
        raise AiError("模型工具调用越界", "invalid_provider_response", 503)
    for c in calls:
        # Honor the registered JSON-string input contract, including its JSON
        # escaping overhead. Other tools retain the existing 8 KB bound; the
        # central executor still validates the complete schema before dispatch.
        entry = next((t for t in tools if t["name"] == c["name"]), {})
        query_schema = (
            entry.get("inputSchema", {}).get("properties", {}).get("queryJson", {})
        )
        declared = query_schema.get("maxLength")
        limit = 8000
        if (
            query_schema.get("type") == "string"
            and type(declared) is int
            and 0 < declared <= 16000
        ):
            limit = max(limit, declared * 2 + 1024)
        passive(c["arguments"], limit)
    answer = "\n".join(texts).strip()
    truncated = (result.get("stop_reason") == "max_tokens" if model.protocol == "anthropic"
                 else choices[0].get("finish_reason") == "length")
    if truncated and answer and not calls:
        answer += "\n\n（本次回复达到模型输出上限，内容尚未完整生成；可要求继续。）"
    if not answer and not calls:
        raise EmptyProviderResponse(
            truncated=truncated,
            finish_reason=result.get("stop_reason") if model.protocol == "anthropic" else choices[0].get("finish_reason"),
            has_reasoning=(any(b.get("type") == "thinking" for b in blocks) if model.protocol == "anthropic"
                           else bool(message.get("reasoning_content"))),
            usage=result.get("usage"),
        )
    return {
        "text": answer,
        "calls": calls,
        "frame": frame,
        "usage": result.get("usage", {}),
        "stopReason": result.get("stop_reason") if model.protocol == "anthropic" else choices[0].get("finish_reason"),
        "context": context_info,
        "truncated": truncated,
        "providerRequestId": str(result.get("id", ""))[:200],
    }


def probe(model):
    if model.model_type != "vision":
        turn(model, [{"role": "user", "content": "请回复连接成功"}], "连接测试", [])
        return "文本连接成功"

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 2) * 2))
        + chunk(b"IEND", b"")
    )
    encoded = base64.b64encode(png).decode()
    prompt = "只回答图片中的主要颜色，不要猜测未看到的内容。"
    content = (
        [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": encoded,
                },
            },
            {"type": "text", "text": prompt},
        ]
        if model.protocol == "anthropic"
        else [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + encoded},
            },
        ]
    )
    result = turn(model, [{"role": "user", "content": content}], "图片能力测试", [])
    if not re.search(r"红|\bred\b", result["text"], re.I):
        raise AiError("模型未通过真实图片识别测试", "invalid_provider_response", 503)
    return "图片识别连接成功"


def tool_frames(model, calls, results):
    if model.protocol == "anthropic":
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                    for call, result in zip(calls, results)
                ],
            }
        ]
    return [
        {
            "role": "tool",
            "tool_call_id": call["id"],
            "content": json.dumps(result, ensure_ascii=False),
        }
        for call, result in zip(calls, results)
    ]
