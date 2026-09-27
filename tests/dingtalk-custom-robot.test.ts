import assert from "node:assert/strict";
import test from "node:test";
import {
  buildSignedDingTalkWebhook,
  createDingTalkSignature,
  sendDingTalkRobotText,
  validateDingTalkWebhook,
} from "../lib/dingtalk/custom-robot";
import { runDingTalkRobotCli } from "../tools/dingtalk-custom-robot-send";

const fakeWebhook = "https://oapi.dingtalk.com/robot/send?access_token=test-token";
const fakeSecret = "SECexample";

test("creates the DingTalk HMAC-SHA256 signature from timestamp and secret", () => {
  assert.equal(
    createDingTalkSignature(1_700_000_000_000, fakeSecret),
    "uroalHrWHORz59talE1o26b8HuX2kOp8WlAN91F9Pj0=",
  );
});

test("replaces stale signing parameters without exposing the secret", () => {
  const signed = buildSignedDingTalkWebhook(
    `${fakeWebhook}&timestamp=1&sign=stale`,
    fakeSecret,
    1_700_000_000_000,
  );

  assert.equal(signed.searchParams.get("access_token"), "test-token");
  assert.equal(signed.searchParams.get("timestamp"), "1700000000000");
  assert.equal(signed.searchParams.get("sign"), "uroalHrWHORz59talE1o26b8HuX2kOp8WlAN91F9Pj0=");
  assert.equal(signed.toString().includes(fakeSecret), false);
});

test("rejects non-DingTalk endpoints and malformed access tokens", () => {
  assert.throws(
    () => validateDingTalkWebhook("https://example.com/robot/send?access_token=test-token"),
    /official DingTalk custom-robot HTTPS URL/,
  );
  assert.throws(
    () => validateDingTalkWebhook("https://oapi.dingtalk.com/robot/send"),
    /exactly one access_token/,
  );
});

test("sends one signed text request and returns only the bounded result", async () => {
  let requestedUrl: URL | undefined;
  let requestInit: RequestInit | undefined;
  const fetchImpl: typeof fetch = async (input, init) => {
    requestedUrl = new URL(String(input));
    requestInit = init;
    return new Response(JSON.stringify({ errcode: 0, errmsg: "ok" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  const result = await sendDingTalkRobotText({
    webhook: fakeWebhook,
    secret: fakeSecret,
    text: "hello",
    timestamp: 1_700_000_000_000,
    fetchImpl,
  });

  assert.deepEqual(result, { errcode: 0, errmsg: "ok" });
  assert.equal(requestedUrl?.searchParams.get("timestamp"), "1700000000000");
  assert.equal(requestedUrl?.searchParams.get("sign"), "uroalHrWHORz59talE1o26b8HuX2kOp8WlAN91F9Pj0=");
  assert.equal(requestInit?.method, "POST");
  assert.equal(requestInit?.body, JSON.stringify({ msgtype: "text", text: { content: "hello" } }));
});

test("does not retry or include credentials when DingTalk rejects a message", async () => {
  let calls = 0;
  const fetchImpl: typeof fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify({ errcode: 310000, errmsg: "signature mismatch" }), { status: 200 });
  };

  await assert.rejects(
    sendDingTalkRobotText({
      webhook: fakeWebhook,
      secret: fakeSecret,
      text: "hello",
      fetchImpl,
    }),
    (error: unknown) => {
      assert(error instanceof Error);
      assert.match(error.message, /310000.*signature mismatch/);
      assert.equal(error.message.includes("test-token"), false);
      assert.equal(error.message.includes(fakeSecret), false);
      return true;
    },
  );
  assert.equal(calls, 1);
});

test("dry-run validates signing while redacting the webhook token", async () => {
  const result = await runDingTalkRobotCli(["--text", "hello", "--dry-run"], {
    DINGTALK_ROBOT_WEBHOOK: fakeWebhook,
    DINGTALK_ROBOT_SECRET: fakeSecret,
  });

  assert.equal(result.ok, true);
  assert.equal(result.dryRun, true);
  assert.equal(String(result.endpoint).includes("test-token"), false);
  assert.equal(JSON.stringify(result).includes(fakeSecret), false);
});
