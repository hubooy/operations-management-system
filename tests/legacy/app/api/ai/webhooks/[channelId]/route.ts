import { decryptSecret } from "@/lib/ai/crypto";
import {
  extractXmlElement,
  decryptWeComEnvelope,
  sha256Hex,
  verifyWeComSignature,
} from "@/lib/ai/channel-callbacks";
import {
  getAiChannelSecretById,
  recordAiChannelCallbackEvent,
  type AiChannelSecret,
} from "@/tests/legacy/ai/assistant-service";
import { getD1Database } from "@/lib/database/d1";
import { readAiBoundedText } from "@/app/api/ai/route-helpers";

type RouteContext = { params: Promise<{ channelId: string }> };
const MAX_CALLBACK_BYTES = 256 * 1024;

function callbackUnavailable(): Response {
  // Avoid revealing whether a channel exists or which validation step failed.
  return new Response("not found", { status: 404, headers: { "cache-control": "no-store" } });
}

function invalidCallback(): Response {
  return new Response("invalid callback", { status: 403, headers: { "cache-control": "no-store" } });
}

async function resolveCallbackChannel(context: RouteContext): Promise<AiChannelSecret | null> {
  const { channelId } = await context.params;
  if (!/^[a-zA-Z0-9_-]{1,160}$/.test(channelId)) return null;
  const channel = await getAiChannelSecretById(channelId, getD1Database());
  if (!channel || channel.status !== "enabled" || !channel.callbackEnabled || !channel.receiverId) return null;
  return channel;
}

async function handleWeComVerification(request: Request, channel: AiChannelSecret): Promise<Response> {
  if (!channel.callbackTokenEncrypted || !channel.aesKeyEncrypted) return callbackUnavailable();
  const params = new URL(request.url).searchParams;
  const token = await decryptSecret(channel.callbackTokenEncrypted);
  const aesKey = await decryptSecret(channel.aesKeyEncrypted);
  const encrypted = params.get("echostr") ?? "";
  const valid = await verifyWeComSignature({
    token,
    timestamp: params.get("timestamp") ?? "",
    nonce: params.get("nonce") ?? "",
    encrypt: encrypted,
    signature: params.get("msg_signature") ?? "",
  });
  if (!valid) return invalidCallback();
  try {
    const echo = await decryptWeComEnvelope({ encodingAesKey: aesKey, encrypted, expectedReceiverId: channel.receiverId });
    return new Response(echo, { headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } });
  } catch {
    return invalidCallback();
  }
}

async function handleWeComEvent(request: Request, channel: AiChannelSecret): Promise<Response> {
  if (!channel.callbackTokenEncrypted || !channel.aesKeyEncrypted) return callbackUnavailable();
  let body: string;
  try {
    body = await readAiBoundedText(request, MAX_CALLBACK_BYTES);
  } catch {
    return new Response("payload too large", { status: 413, headers: { "cache-control": "no-store" } });
  }
  const encrypted = extractXmlElement(body, "Encrypt");
  const params = new URL(request.url).searchParams;
  const token = await decryptSecret(channel.callbackTokenEncrypted);
  const aesKey = await decryptSecret(channel.aesKeyEncrypted);
  const valid = await verifyWeComSignature({
    token,
    timestamp: params.get("timestamp") ?? "",
    nonce: params.get("nonce") ?? "",
    encrypt: encrypted,
    signature: params.get("msg_signature") ?? "",
  });
  if (!valid) return invalidCallback();
  try {
    const eventXml = await decryptWeComEnvelope({ encodingAesKey: aesKey, encrypted, expectedReceiverId: channel.receiverId });
    const digest = await sha256Hex(eventXml);
    const eventKey = extractXmlElement(eventXml, "MsgId")
      || [extractXmlElement(eventXml, "FromUserName"), extractXmlElement(eventXml, "CreateTime"), extractXmlElement(eventXml, "Event")].filter(Boolean).join(":")
      || digest;
    await recordAiChannelCallbackEvent({ channelId: channel.id, eventKey, payloadDigest: digest });
    // Deliberately acknowledge only. A signed chat callback cannot directly trigger AI tools or writes.
    return new Response("success", { headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } });
  } catch {
    return invalidCallback();
  }
}

export async function GET(request: Request, context: RouteContext) {
  try {
    const channel = await resolveCallbackChannel(context);
    if (!channel) return callbackUnavailable();
    if (channel.kind !== "wechat_work_app") return callbackUnavailable();
    return handleWeComVerification(request, channel);
  } catch {
    return callbackUnavailable();
  }
}

export async function POST(request: Request, context: RouteContext) {
  try {
    const channel = await resolveCallbackChannel(context);
    if (!channel) return callbackUnavailable();
    if (channel.kind === "wechat_work_app") return handleWeComEvent(request, channel);
    return callbackUnavailable();
  } catch {
    return callbackUnavailable();
  }
}
