import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const credentialScript = fileURLToPath(new URL("./tmall-credential-vault.ps1", import.meta.url));

export type TmallRuntimeCredential = {
  username: string;
  password: string;
};

function validStoreKey(storeKey: string) {
  return /^[a-z0-9][a-z0-9-]*$/.test(storeKey);
}

export async function readTmallRuntimeCredential(storeKey: string): Promise<TmallRuntimeCredential> {
  if (!validStoreKey(storeKey)) throw new Error("天猫店铺键无效");
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
    throw new Error("waiting_login：该店铺的 Windows DPAPI 加密凭据缺失、损坏或无法由当前用户解密");
  }
  try {
    const parsed = JSON.parse(stdout) as Partial<TmallRuntimeCredential>;
    if (typeof parsed.username !== "string" || !parsed.username
      || typeof parsed.password !== "string" || !parsed.password) throw new Error("invalid");
    return { username: parsed.username, password: parsed.password };
  } catch {
    throw new Error("waiting_login：该店铺的 Windows DPAPI 加密凭据响应无效");
  } finally {
    stdout = "";
  }
}
