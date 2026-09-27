import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import { fileURLToPath } from "node:url";
import { inspectJackyunApiResumePermit, publishJackyunApiResumePermit, type JackyunApiResumePermit } from "../lib/jackyun/api-execution-resume";
import { recoverySha } from "../lib/jackyun/preflight-recovery";
import { withJackyunRunLock } from "../lib/jackyun/run-lock";
import { readJackyunLoginConfig } from "../lib/jackyun/windows-dpapi";
import { inspectSubmittedApiTask } from "./jackyun-api-export";
import { readN8nPreflightEvidence } from "./jackyun-preflight-recovery";

async function main() {
  const [action, first, second] = process.argv.slice(2);
  if (!first || !["plan", "apply"].includes(action) || action === "plan" && process.argv.length !== 4
    || action === "apply" && (process.argv.length !== 5 || !/^[a-f0-9]{64}$/.test(second ?? ""))) {
    throw new Error("用法：plan <executionId>；apply <proposal.json> <approvedSha256>");
  }
  const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const config = await readJackyunLoginConfig(sourceRoot);
  const root = path.dirname(path.dirname(path.resolve(config.profileDirectory)));
  const loaded = action === "apply" ? JSON.parse(await readFile(first, "utf8")) as { proposal: JackyunApiResumePermit } : null;
  const id = loaded?.proposal.executionId ?? first;
  if (!/^[1-9]\d{0,19}$/.test(id) || loaded && loaded.proposal.root !== root) throw new Error("API 续跑根目录或 execution 不一致。");
  await withJackyunRunLock({ runId: `n8n-export-first-${id}`, purpose: "api_export_resume_permit",
    lockDirectory: path.join(root, ".runtime/jackyun-automation.lock") }, async () => {
    const health = await fetch("http://127.0.0.1:5791/health", { signal: AbortSignal.timeout(10000) });
    const status = await health.json() as { ok: boolean; busy: boolean; activeWorkflow: unknown };
    if (!health.ok || !status.ok || status.busy || status.activeWorkflow) throw new Error("helper 非空闲。");
    const evidence = readN8nPreflightEvidence(path.join(homedir(), ".n8n/database.sqlite"), id);
    const policy = JSON.parse(await readFile(path.join(root, "config/jackyun-export-first-policy.json"), "utf8"));
    const task = await inspectSubmittedApiTask({ runId: `n8n-export-first-${id}`,
      outputRoot: path.join(root, "outputs/jackyun-import-runs"), allowedHosts: policy.browser?.allowedDownloadHosts,
      binding: loaded?.proposal.task });
    if (action === "plan") {
      const proposal = await inspectJackyunApiResumePermit(root, id, evidence, task, new Date().toISOString());
      console.log(JSON.stringify({ proposal, approvedSha256: recoverySha(JSON.stringify(proposal)) }));
    } else {
      if (!isDeepStrictEqual(loaded!.proposal.task, task)) throw new Error("API 原导出任务在批准后发生变化。");
      await publishJackyunApiResumePermit(loaded!.proposal, evidence, second);
      console.log(JSON.stringify({ status: "api_resume_permit_published", runId: loaded!.proposal.runId }));
    }
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(() => {
    console.error("API 导出续跑许可未通过；保留原任务和文件并停止。");
    process.exitCode = 1;
  });
}
