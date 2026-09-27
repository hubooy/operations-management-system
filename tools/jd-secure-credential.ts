import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const credentialScript = fileURLToPath(new URL("./jd-credential-vault.ps1", import.meta.url));

export type JdRuntimeCredential = {
  username: string;
  password: string;
};

export async function readJdRuntimeCredential(storeKey: string): Promise<JdRuntimeCredential> {
  if (!/^[a-z0-9][a-z0-9-]*$/.test(storeKey)) throw new Error("京东店铺键无效");
  let stdout = "";
  try {
    const result = await execFileAsync("powershell.exe", [
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy", "Bypass",
      "-File", credentialScript,
      "-Action", "read",
      "-StoreKey", storeKey,
    ], {
      cwd: path.dirname(credentialScript),
      encoding: "utf8",
      maxBuffer: 32 * 1024,
      timeout: 15_000,
      windowsHide: true,
    });
    stdout = result.stdout.trim();
  } catch {
    throw new Error("waiting_login：该京东店铺的 Windows DPAPI 加密凭据缺失、损坏或无法由当前用户解密");
  }
  try {
    const parsed = JSON.parse(stdout) as Partial<JdRuntimeCredential>;
    if (typeof parsed.username !== "string" || !parsed.username
      || typeof parsed.password !== "string" || !parsed.password) throw new Error("invalid");
    return { username: parsed.username, password: parsed.password };
  } catch {
    throw new Error("waiting_login：该京东店铺的 Windows DPAPI 加密凭据响应无效");
  } finally {
    stdout = "";
  }
}
