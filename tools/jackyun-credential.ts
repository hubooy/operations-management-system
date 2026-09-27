import path from "node:path";
import { fileURLToPath } from "node:url";
import { manageJackyunCredential, readJackyunLoginConfig } from "../lib/jackyun/windows-dpapi";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const action = process.argv[2];
if (process.argv.length !== 3 || !["setup", "status"].includes(action)) {
  throw new Error("仅支持 jackyun-credential.ts setup 或 status；不提供明文凭据输出命令。");
}
manageJackyunCredential(action as "setup" | "status", await readJackyunLoginConfig(root))
  .then(status => console.log(JSON.stringify(status)))
  .catch((error: unknown) => { console.error(error instanceof Error ? error.message : "吉客云 DPAPI 配置失败。"); process.exitCode = 1; });
