import { readFile } from "node:fs/promises";
import path from "node:path";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
import { readN8nPreflightEvidence } from "./jackyun-preflight-recovery";
import { recoverySha } from "../lib/jackyun/preflight-recovery";
import { inspectJackyunResumePermit, publishJackyunResumePermit, type JackyunResumePermit } from "../lib/jackyun/execution-resume";
import { readJackyunLoginConfig } from "../lib/jackyun/windows-dpapi";
import { withJackyunRunLock } from "../lib/jackyun/run-lock";

async function main() {
  const [action, first, second] = process.argv.slice(2);
  if (!first || !second || !["plan", "apply"].includes(action) || process.argv.length !== 5) throw new Error("用法：plan <executionId> <task-binding.json>；apply <proposal.json> <approvedSha256>");
  const config = await readJackyunLoginConfig(path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."));
  const root = path.dirname(path.dirname(path.resolve(config.profileDirectory)));
  const loaded = action === "apply" ? JSON.parse(await readFile(first, "utf8")) as { proposal: JackyunResumePermit } : null;
  const id = loaded?.proposal.executionId ?? first;
  if (loaded && loaded.proposal.root !== root) throw new Error("续跑根目录不一致。");
  await withJackyunRunLock({ runId: `n8n-export-first-${id}`, purpose: "export_resume_permit",
    lockDirectory: path.join(root, ".runtime/jackyun-automation.lock") }, async () => {
    const health = await fetch("http://127.0.0.1:5791/health", { signal: AbortSignal.timeout(10000) });
    const status = await health.json() as { ok: boolean; busy: boolean; activeWorkflow: unknown };
    if (!health.ok || !status.ok || status.busy || status.activeWorkflow) throw new Error("helper 非空闲。");
    const evidence = readN8nPreflightEvidence(path.join(homedir(), ".n8n/database.sqlite"), id);
    if (action === "plan") {
      const task = JSON.parse(await readFile(second, "utf8"));
      const proposal = await inspectJackyunResumePermit(root, id, evidence, task, new Date().toISOString());
      console.log(JSON.stringify({ proposal, approvedSha256: recoverySha(JSON.stringify(proposal)) }));
    } else {
      await publishJackyunResumePermit(loaded!.proposal, evidence, second);
      console.log(JSON.stringify({ status: "resume_permit_published", runId: loaded!.proposal.runId }));
    }
  });
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(() => { console.error("导出续跑许可未通过；保留原任务和文件并停止。"); process.exitCode = 1; });
}
