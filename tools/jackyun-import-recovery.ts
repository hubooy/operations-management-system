import { readFile } from "node:fs/promises";
import path from "node:path";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
import { readN8nPreflightEvidence } from "./jackyun-preflight-recovery";
import { inspectImportRecovery, publishImportRecovery, type ImportRecoveryPermit } from "../lib/jackyun/import-recovery";
import { recoverySha } from "../lib/jackyun/preflight-recovery";
import { withJackyunRunLock } from "../lib/jackyun/run-lock";
import { verifyJackyunPreparedImports } from "./jackyun-export-first-pipeline";

async function main() {
  const [action, rootArg, proposalFile, approvedSha] = process.argv.slice(2);
  if (!rootArg || !path.isAbsolute(rootArg) || !["plan", "apply"].includes(action)
    || (action === "plan" ? process.argv.length !== 4 : process.argv.length !== 6)
    || (action === "apply" && !/^[a-f0-9]{64}$/.test(approvedSha ?? ""))) throw Error("用法：plan <root>；apply <root> <proposal.json> <approvedSha256>");
  const root = path.resolve(rootArg);
  await withJackyunRunLock({ runId: "n8n-export-first-890", purpose: "audited_import_recovery",
    lockDirectory: path.join(root, ".runtime/jackyun-automation.lock") }, async () => {
    const health = await fetch("http://127.0.0.1:5791/health", { signal: AbortSignal.timeout(10000) });
    const state = await health.json() as { ok: boolean; busy: boolean; activeWorkflow: unknown };
    if (!health.ok || !state.ok || state.busy || state.activeWorkflow) throw Error("helper 非空闲，拒绝签发导入续跑许可");
    const evidence = readN8nPreflightEvidence(path.join(homedir(), ".n8n/database.sqlite"), "891");
    const plan = JSON.parse(await readFile(path.join(root, "outputs/jackyun-export-first/n8n-export-first-890.json"), "utf8"));
    const policy = JSON.parse(await readFile(path.join(root, "config/jackyun-export-first-policy.json"), "utf8"));
    await verifyJackyunPreparedImports(root, plan, policy);
    if (action === "plan") {
      const proposal = await inspectImportRecovery(root, evidence, new Date().toISOString());
      console.log(JSON.stringify({ proposal, approvedSha256: recoverySha(JSON.stringify(proposal)) }));
    } else {
      const { proposal } = JSON.parse(await readFile(proposalFile, "utf8")) as { proposal: ImportRecoveryPermit };
      if (proposal.root !== root || Date.now() < Date.parse(proposal.createdAt) || Date.now() - Date.parse(proposal.createdAt) > 30 * 60_000) throw Error("许可目录或有效期不符");
      await publishImportRecovery(proposal, evidence, approvedSha);
      console.log(JSON.stringify({ status: "import_recovery_permit_published", failedExecutionId: "891", runId: "n8n-export-first-890" }));
    }
  });
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error instanceof Error ? error.message : "导入续跑拒绝"); process.exitCode = 1; });
}
