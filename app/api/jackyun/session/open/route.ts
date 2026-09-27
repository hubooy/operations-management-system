import { launchDedicatedChrome, waitForChrome } from "@/lib/jackyun/cdp-client";
import { resolveJackyunChromeProfileDirectory } from "@/lib/jackyun/runtime-path";
import { withJackyunRunLock } from "@/lib/jackyun/run-lock";
import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { safeApiErrorResponse } from "@/lib/http/api-error";

export const runtime = "nodejs";

const runtimeProcess = typeof process === "undefined" ? undefined : process;
const runtimeCwd = typeof runtimeProcess?.cwd === "function" ? runtimeProcess.cwd() : undefined;
const runtimeEnv = (runtimeProcess?.env ?? {}) as Record<string, string | undefined>;
const chromePath = runtimeEnv.JACKYUN_CHROME_PATH ?? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const profileDirectory = resolveJackyunChromeProfileDirectory({
  cwd: runtimeCwd,
  configuredProfileDirectory: runtimeEnv.JACKYUN_PROFILE_DIR,
});
const port = Number(runtimeEnv.JACKYUN_DEBUG_PORT ?? 9223);
const startUrl = "https://web.jackyun.com/login/login_web.html";

export async function POST() {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "吉客云登录会话", "修改");
    await withJackyunRunLock(
      { runId: "session-open", purpose: "manual_login_browser" },
      async () => {
        await launchDedicatedChrome({ executablePath: chromePath, profileDirectory, port, startUrl, headless: false });
        await waitForChrome(port);
      },
    );
    return Response.json({ ok: true, status: "login_browser_ready", url: startUrl }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "打开吉客云登录页失败", {
      shape: "import",
      headers: { "cache-control": "no-store" },
    });
  }
}
