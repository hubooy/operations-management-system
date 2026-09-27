"""Bounded, request-scoped SSE. Completed receipts remain the only durable result.

Two existing primary permits are held until producer termination, including after
disconnect. A producer owns the request's DB connection; the WSGI reader closes
its connection before starting it. No executor, replay queue or automatic retry.
"""
import queue
import threading
from django.db import connections
from django.http import StreamingHttpResponse
from . import chat, transport
from .model_capabilities import MAX_CHAT_SECONDS
from .policy import AiError, canonical, revision


class ChatStream:
    def __init__(self, payload, principal, request_id):
        self.payload, self.principal, self.request_id = payload, principal, request_id
        self.queue = queue.Queue(maxsize=32)
        self.cancelled = threading.Event()
        self.thread = None
        self.release = None
        self.lock = threading.Lock()

    def check(self):
        if self.cancelled.is_set():
            raise AiError("已停止生成", "ai_request_cancelled", 499)

    def emit(self, event, value):
        self.check()
        try:
            self.queue.put((event, value), timeout=2)
        except queue.Full as error:
            self.cancelled.set()
            raise AiError("客户端读取超时，生成已停止", "ai_request_cancelled", 499) from error

    def release_once(self):
        with self.lock:
            release, self.release = self.release, None
        if release:
            release()

    def failure(self, message, code):
        if not self.cancelled.is_set():
            try:
                self.emit("failure", {"error": message, "code": code})
            except AiError:
                pass  # Consumer has gone; never log raw exception or re-dispatch.

    def run(self):
        try:
            with transport.request_budget(MAX_CHAT_SECONDS), transport.request_cancellation(self.check):
                self.check()
                result = chat.answer(self.payload, self.principal, self.request_id, on_event=self.emit)
                self.emit("done", result)
        except AiError as error:
            self.failure(str(error), error.code)
        except Exception:
            self.failure("生成失败，请核对已保存的对话记录。", "service_unavailable")
        finally:
            connections.close_all()
            self.release_once()

    def __iter__(self):
        try:
            self.check()
            # One database owner per primary request, not an extra connection.
            connections.close_all()
            self.thread = threading.Thread(target=self.run, name="ai-chat-stream", daemon=True)
            self.thread.start()
            serial = 0
            while True:
                self.check()
                try:
                    event, value = self.queue.get(timeout=5)
                except queue.Empty:
                    if not self.thread.is_alive():
                        break
                    yield b": heartbeat\n\n"
                    continue
                serial += 1
                yield f"id: {serial}\nevent: {event}\ndata: {canonical(value)}\n\n".encode()
                if event in {"done", "failure"}:
                    break
        finally:
            self.close()

    def close(self):
        self.cancelled.set()
        if self.thread is None:
            self.release_once()


def response(payload, principal, request_id):
    session = ChatStream(payload, principal, request_id)
    result = StreamingHttpResponse(session, content_type="text/event-stream; charset=utf-8")
    result.ai_stream = session
    result["Cache-Control"] = "private, no-store, no-transform"
    result["X-Accel-Buffering"] = "no"
    result["X-Content-Type-Options"] = "nosniff"
    result["X-AI-Revision"] = revision()
    return result
