"""Incremental provider SSE -> existing validated provider.turn result contract.

Reasoning/signatures/tool arguments remain request-local; only public text deltas
reach the client. No model-name heuristics, requests retries, or new tools.
"""
import codecs
import json
import re
from .policy import AiError
from .model_capabilities import MAX_REPLY_CHARACTERS


def invalid():
    return AiError("模型流式响应无效或未完整结束，本次请求未自动重试", "invalid_provider_response", 503)


class ProviderStream:
    def __init__(self, protocol, on_text):
        self.protocol, self.on_text = protocol, on_text
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.buffer, self.done, self.started, self.stopped = "", False, False, False
        self.message = {"content": "", "reasoning_content": ""}
        self.calls, self.blocks, self.partial_json = {}, {}, {}
        self.closed_blocks = set()
        self.result, self.reason, self.text_count = {}, None, 0

    def text(self, value):
        if not isinstance(value, str):
            raise invalid()
        self.text_count += len(value)
        if self.text_count > MAX_REPLY_CHARACTERS:
            raise AiError("模型回复超过系统消息上限", "response_too_large", 503)
        if value:
            self.on_text(value)

    def feed(self, chunk):
        self.buffer += self.decoder.decode(chunk)
        while not self.done:
            boundary = re.search(r"\r?\n\r?\n", self.buffer)
            if not boundary:
                break
            block = self.buffer[:boundary.start()]
            self.buffer = self.buffer[boundary.end():]
            data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
            if not data:
                continue
            if data == "[DONE]":
                if self.protocol == "anthropic" or self.reason not in {"stop", "length", "tool_calls"}:
                    raise invalid()
                self.done = True
                break
            try:
                event = json.loads(data)
            except ValueError as error:
                raise invalid() from error
            if not isinstance(event, dict) or event.get("error"):
                raise invalid()
            if self.protocol == "anthropic":
                self.anthropic(event)
            else:
                self.openai(event)
        if len(self.buffer.encode()) > 256 * 1024:
            raise invalid()

    def openai(self, event):
        if "id" in event:
            self.result["id"] = event["id"]
        if isinstance(event.get("usage"), dict):
            self.result["usage"] = event["usage"]
        choices = event.get("choices")
        if choices == [] and event.get("usage"):
            return
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict) or choices[0].get("index", 0) != 0:
            raise invalid()
        choice = choices[0]
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            raise invalid()
        if self.reason is not None:
            raise invalid()
        if delta.get("content") is not None:
            self.text(delta["content"])
            self.message["content"] += delta["content"]
        if delta.get("reasoning_content") is not None:
            if not isinstance(delta["reasoning_content"], str):
                raise invalid()
            self.message["reasoning_content"] += delta["reasoning_content"]
        updates = delta.get("tool_calls", [])
        if not isinstance(updates, list) or len(updates) > 8:
            raise invalid()
        for update in updates:
            if not isinstance(update, dict) or type(update.get("index")) is not int or not 0 <= update["index"] < 8:
                raise invalid()
            call = self.calls.setdefault(update["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            function = update.get("function", {})
            if not isinstance(function, dict):
                raise invalid()
            for container, key, value in [(call, "id", update.get("id")), (call["function"], "name", function.get("name")), (call["function"], "arguments", function.get("arguments"))]:
                if value is not None:
                    if not isinstance(value, str):
                        raise invalid()
                    container[key] += value
        if choice.get("finish_reason") is not None:
            self.reason = choice["finish_reason"]
            if self.reason not in {"stop", "length", "tool_calls"}:
                raise invalid()

    def anthropic(self, event):
        kind = event.get("type")
        if kind == "ping":
            return
        if kind == "message_start":
            if self.started or not isinstance(event.get("message"), dict):
                raise invalid()
            self.started = True
            self.result = {**event["message"]}
        elif not self.started or self.stopped:
            raise invalid()
        elif kind == "content_block_start":
            index, block = event.get("index"), event.get("content_block")
            if type(index) is not int or not 0 <= index < 32 or index in self.blocks or not isinstance(block, dict):
                raise invalid()
            self.blocks[index] = {**block}
            if block.get("type") == "text":
                self.text(block.get("text", ""))
        elif kind == "content_block_delta":
            index, delta = event.get("index"), event.get("delta")
            if type(index) is not int or index not in self.blocks or index in self.closed_blocks or not isinstance(delta, dict):
                raise invalid()
            block = self.blocks[index]
            mapping = {"text_delta": ("text", "text"), "thinking_delta": ("thinking", "thinking"), "signature_delta": ("thinking", "signature")}
            if delta.get("type") in mapping:
                block_type, key = mapping[delta["type"]]
                value = delta.get(key)
                if block.get("type") != block_type or not isinstance(value, str):
                    raise invalid()
                block[key] = block.get(key, "") + value
                if key == "text":
                    self.text(value)
            elif delta.get("type") == "input_json_delta" and block.get("type") == "tool_use" and isinstance(delta.get("partial_json"), str):
                self.partial_json[index] = self.partial_json.get(index, "") + delta["partial_json"]
            else:
                raise invalid()
        elif kind == "content_block_stop":
            index = event.get("index")
            if type(index) is not int or index not in self.blocks or index in self.closed_blocks:
                raise invalid()
            self.closed_blocks.add(index)
            if index in self.partial_json:
                try:
                    self.blocks[index]["input"] = json.loads(self.partial_json.pop(index))
                except ValueError as error:
                    raise invalid() from error
        elif kind == "message_delta":
            if not isinstance(event.get("delta"), dict) or not isinstance(event.get("usage", {}), dict):
                raise invalid()
            self.reason = event["delta"].get("stop_reason") or self.reason
            self.result["usage"] = {**self.result.get("usage", {}), **event.get("usage", {})}
        elif kind == "message_stop":
            if self.reason not in {"end_turn", "tool_use", "max_tokens", "stop_sequence"} or self.partial_json or set(self.blocks) != self.closed_blocks:
                raise invalid()
            self.done, self.stopped = True, True
        else:
            raise invalid()

    def finish(self):
        self.decoder.decode(b"", final=True)
        if not self.done:
            raise invalid()
        if self.protocol == "anthropic":
            return {**self.result, "content": [self.blocks[k] for k in sorted(self.blocks)], "stop_reason": self.reason}
        if self.calls:
            self.message["tool_calls"] = [self.calls[k] for k in sorted(self.calls)]
        return {**self.result, "choices": [{"message": self.message, "finish_reason": self.reason}]}
