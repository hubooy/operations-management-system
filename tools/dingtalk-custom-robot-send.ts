import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildSignedDingTalkWebhook,
  sendDingTalkRobotText,
  validateDingTalkText,
  validateDingTalkWebhook,
} from "../lib/dingtalk/custom-robot";

type CliOptions = {
  text: string;
  dryRun: boolean;
};

export function parseCliOptions(args: string[]): CliOptions {
  let text = "";
  let dryRun = false;

  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--dry-run") {
      dryRun = true;
      continue;
    }
    if (argument === "--text") {
      text = args[index + 1] ?? "";
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${argument}`);
  }

  return { text: validateDingTalkText(text), dryRun };
}

function safeEndpoint(webhook: string): string {
  const parsed = validateDingTalkWebhook(webhook);
  parsed.searchParams.set("access_token", "[REDACTED]");
  parsed.searchParams.delete("timestamp");
  parsed.searchParams.delete("sign");
  return parsed.toString();
}

export async function runDingTalkRobotCli(
  args: string[],
  environment: Readonly<Record<string, string | undefined>> = process.env,
): Promise<Record<string, unknown>> {
  const options = parseCliOptions(args);
  const webhook = environment.DINGTALK_ROBOT_WEBHOOK ?? "";
  const secret = environment.DINGTALK_ROBOT_SECRET ?? "";

  if (options.dryRun) {
    buildSignedDingTalkWebhook(webhook, secret);
    return {
      ok: true,
      dryRun: true,
      endpoint: safeEndpoint(webhook),
      message: { msgtype: "text", text: { content: options.text } },
      signed: true,
    };
  }

  const result = await sendDingTalkRobotText({ webhook, secret, text: options.text });
  return { ok: true, delivered: true, ...result };
}

const isMain = path.resolve(process.argv[1] ?? "") === path.resolve(fileURLToPath(import.meta.url));
if (isMain) {
  runDingTalkRobotCli(process.argv.slice(2))
    .then((result) => console.log(JSON.stringify(result, null, 2)))
    .catch((error: unknown) => {
      const message = error instanceof Error ? error.message : "Unknown error";
      console.error(JSON.stringify({ ok: false, error: message }, null, 2));
      process.exitCode = 1;
    });
}
