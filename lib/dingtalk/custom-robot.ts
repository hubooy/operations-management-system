import { createHmac } from "node:crypto";

const DINGTALK_ROBOT_HOST = "oapi.dingtalk.com";
const DINGTALK_ROBOT_PATH = "/robot/send";
const MAX_TEXT_BYTES = 20_000;

export type DingTalkRobotResult = {
  errcode: 0;
  errmsg: string;
};

type FetchLike = typeof fetch;

function requireNonEmpty(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) {
    throw new Error(`${label} is required.`);
  }
  return normalized;
}

export function validateDingTalkWebhook(webhook: string): URL {
  const parsed = new URL(requireNonEmpty(webhook, "DINGTALK_ROBOT_WEBHOOK"));

  if (
    parsed.protocol !== "https:" ||
    parsed.hostname !== DINGTALK_ROBOT_HOST ||
    parsed.pathname !== DINGTALK_ROBOT_PATH ||
    parsed.username ||
    parsed.password ||
    parsed.hash
  ) {
    throw new Error("DINGTALK_ROBOT_WEBHOOK must be an official DingTalk custom-robot HTTPS URL.");
  }

  const accessTokens = parsed.searchParams.getAll("access_token");
  if (accessTokens.length !== 1 || !accessTokens[0]?.trim()) {
    throw new Error("DINGTALK_ROBOT_WEBHOOK must contain exactly one access_token.");
  }

  const allowedQueryParameters = new Set(["access_token", "timestamp", "sign"]);
  for (const key of parsed.searchParams.keys()) {
    if (!allowedQueryParameters.has(key)) {
      throw new Error(`DINGTALK_ROBOT_WEBHOOK contains an unsupported query parameter: ${key}.`);
    }
  }

  return parsed;
}

export function createDingTalkSignature(timestamp: number | string, secret: string): string {
  const normalizedSecret = requireNonEmpty(secret, "DINGTALK_ROBOT_SECRET");
  const normalizedTimestamp = String(timestamp);
  if (!/^\d+$/.test(normalizedTimestamp)) {
    throw new Error("DingTalk signature timestamp must be milliseconds since the Unix epoch.");
  }

  return createHmac("sha256", normalizedSecret)
    .update(`${normalizedTimestamp}\n${normalizedSecret}`, "utf8")
    .digest("base64");
}

export function buildSignedDingTalkWebhook(webhook: string, secret: string, timestamp = Date.now()): URL {
  const signedWebhook = validateDingTalkWebhook(webhook);
  signedWebhook.searchParams.delete("timestamp");
  signedWebhook.searchParams.delete("sign");
  signedWebhook.searchParams.set("timestamp", String(timestamp));
  signedWebhook.searchParams.set("sign", createDingTalkSignature(timestamp, secret));
  return signedWebhook;
}

export function validateDingTalkText(text: string): string {
  if (!text.trim()) {
    throw new Error("DingTalk robot text must not be empty.");
  }
  if (Buffer.byteLength(text, "utf8") > MAX_TEXT_BYTES) {
    throw new Error(`DingTalk robot text must not exceed ${MAX_TEXT_BYTES} UTF-8 bytes.`);
  }
  return text;
}

export async function sendDingTalkRobotText(options: {
  webhook: string;
  secret: string;
  text: string;
  timestamp?: number;
  fetchImpl?: FetchLike;
}): Promise<DingTalkRobotResult> {
  const text = validateDingTalkText(options.text);
  const signedWebhook = buildSignedDingTalkWebhook(options.webhook, options.secret, options.timestamp);
  const fetchImpl = options.fetchImpl ?? fetch;

  const response = await fetchImpl(signedWebhook, {
    method: "POST",
    headers: {
      "content-type": "application/json; charset=utf-8",
    },
    body: JSON.stringify({
      msgtype: "text",
      text: { content: text },
    }),
    signal: AbortSignal.timeout(30_000),
  });

  const responseText = await response.text();
  if (!response.ok) {
    throw new Error(`DingTalk robot request failed with HTTP ${response.status}.`);
  }

  let result: { errcode?: unknown; errmsg?: unknown };
  try {
    result = JSON.parse(responseText) as { errcode?: unknown; errmsg?: unknown };
  } catch {
    throw new Error("DingTalk robot returned an invalid JSON response.");
  }

  if (result.errcode !== 0) {
    const code = typeof result.errcode === "number" ? result.errcode : "unknown";
    const message = typeof result.errmsg === "string" ? result.errmsg : "Unknown DingTalk error";
    throw new Error(`DingTalk robot rejected the message (${code}): ${message}`);
  }

  return {
    errcode: 0,
    errmsg: typeof result.errmsg === "string" ? result.errmsg : "ok",
  };
}
