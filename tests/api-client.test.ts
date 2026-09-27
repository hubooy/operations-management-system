import assert from "node:assert/strict";
import test from "node:test";

import { requestJson } from "../lib/http/api-client";
import { ApiError } from "../lib/http/api-error";

type FetchCall = {
  input: RequestInfo | URL;
  init: RequestInit | undefined;
};

function installFetchMock(
  implementation: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = implementation;
  return () => {
    globalThis.fetch = originalFetch;
  };
}

test("requestJson applies same-origin defaults and serializes JSON bodies", async () => {
  const calls: FetchCall[] = [];
  const restore = installFetchMock(async (input, init) => {
    calls.push({ input, init });
    return Response.json({ ok: true, count: 2 });
  });

  try {
    const result = await requestJson<{ ok: boolean; count: number }>("/api/example", {
      method: "POST",
      body: { query: "净水器", page: 1 },
    });

    assert.deepEqual(result, { ok: true, count: 2 });
    const call = calls[0];
    assert.equal(call?.input, "/api/example");
    assert.equal(call?.init?.credentials, "same-origin");
    assert.equal(call?.init?.cache, "no-store");
    assert.equal(call?.init?.body, JSON.stringify({ query: "净水器", page: 1 }));
    const headers = new Headers(call?.init?.headers);
    assert.equal(headers.get("accept"), "application/json");
    assert.equal(headers.get("content-type"), "application/json");
  } finally {
    restore();
  }
});

test("requestJson preserves explicit fetch options", async () => {
  const calls: FetchCall[] = [];
  const restore = installFetchMock(async (input, init) => {
    calls.push({ input, init });
    return Response.json({ ok: true });
  });

  try {
    await requestJson("/api/example", {
      credentials: "omit",
      cache: "reload",
      headers: { accept: "application/problem+json" },
    });
    const call = calls[0];
    assert.equal(call?.init?.credentials, "omit");
    assert.equal(call?.init?.cache, "reload");
    assert.equal(new Headers(call?.init?.headers).get("accept"), "application/problem+json");
  } finally {
    restore();
  }
});

test("requestJson returns undefined for successful empty responses", async () => {
  const restore = installFetchMock(async () => new Response(null, { status: 204 }));
  try {
    const result = await requestJson<void>("/api/no-content", { method: "DELETE" });
    assert.equal(result, undefined);
  } finally {
    restore();
  }
});

test("requestJson sends FormData without setting a multipart content type", async () => {
  const calls: FetchCall[] = [];
  const restore = installFetchMock(async (input, init) => {
    calls.push({ input, init });
    return Response.json({ uploaded: true });
  });

  try {
    const formData = new FormData();
    formData.set("file", new Blob(["content"], { type: "text/plain" }), "report.txt");
    await requestJson("/api/upload", { method: "POST", body: formData });

    const call = calls[0];
    assert.equal(call?.init?.body, formData);
    assert.equal(new Headers(call?.init?.headers).has("content-type"), false);
  } finally {
    restore();
  }
});

test("requestJson exposes structured HTTP failures as ApiError", async () => {
  let calls = 0;
  const restore = installFetchMock(async () => {
    calls += 1;
    return Response.json(
      {
        error: "当前账号没有执行此操作的权限",
        code: "insufficient_role",
        details: { required: ["admin"] },
      },
      { status: 403 },
    );
  });

  try {
    await assert.rejects(
      requestJson("/api/admin"),
      (error: unknown) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 403);
        assert.equal(error.code, "insufficient_role");
        assert.equal(error.message, "当前账号没有执行此操作的权限");
        assert.deepEqual(error.details, { required: ["admin"] });
        return true;
      },
    );
    assert.equal(calls, 1);
  } finally {
    restore();
  }
});

test("requestJson converts network failures without retrying", async () => {
  let calls = 0;
  const restore = installFetchMock(async () => {
    calls += 1;
    throw new TypeError("socket unavailable");
  });

  try {
    await assert.rejects(
      requestJson("/api/offline"),
      (error: unknown) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 0);
        assert.equal(error.code, "network_error");
        assert.equal(error.message, "网络请求失败");
        return true;
      },
    );
    assert.equal(calls, 1);
  } finally {
    restore();
  }
});

test("requestJson passes AbortError and the caller signal through unchanged", async () => {
  const controller = new AbortController();
  const abortError = new DOMException("The operation was aborted", "AbortError");
  let receivedSignal: AbortSignal | null | undefined;
  const restore = installFetchMock(async (_input, init) => {
    receivedSignal = init?.signal;
    throw abortError;
  });

  try {
    await assert.rejects(
      requestJson("/api/slow", { signal: controller.signal }),
      (error: unknown) => error === abortError,
    );
    assert.equal(receivedSignal, controller.signal);
  } finally {
    restore();
  }
});

test("requestJson rejects malformed successful JSON with a bounded ApiError", async () => {
  const restore = installFetchMock(async () =>
    new Response("not-json", {
      status: 200,
      headers: { "content-type": "text/plain" },
    }));

  try {
    await assert.rejects(
      requestJson("/api/broken"),
      (error: unknown) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 200);
        assert.equal(error.code, "invalid_json_response");
        assert.equal(error.message, "服务器返回的数据格式不正确");
        return true;
      },
    );
  } finally {
    restore();
  }
});
