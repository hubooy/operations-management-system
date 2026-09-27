import { readFile, stat } from "node:fs/promises";
import { isDeepStrictEqual } from "node:util";
import { readN8nReplacementEvidence } from "./n8n-preflight-evidence";
import { classifyJackyunPreflightFailure } from "./preflight-failure";
import { assertClosedPreflight, inspectPreflightClosure, preflightClosurePath, publishReplacementPreflightClosure } from "./preflight-recovery";
import { manageJackyunCredential, readJackyunLoginConfig } from "./windows-dpapi";

type RecoveryEvidence = ReturnType<typeof readN8nReplacementEvidence>;
export type AutomaticPreflightDependencies = {
  readEvidence?: (previousId: string, replacementId: string) => RecoveryEvidence;
  credentialReady?: () => Promise<boolean>;
};

/** Only called from a fresh plan-api entry while holding the original global run lock. */
export async function recoverPreviousJackyunPreflight(root: string, previousId: string, replacementId: string, closedAt: string,
  deps: AutomaticPreflightDependencies = {}) {
  const readEvidence = (oldId: string, newId: string) => {
    try { return (deps.readEvidence ?? readN8nReplacementEvidence)(oldId, newId); }
    catch { throw new Error("原运行的 n8n 恢复证据不可用，需要人工核查；保留旧任务。"); }
  };
  if (await assertClosedPreflight(root, previousId, replacementId).then(() => true, () => false)) {
    const receipt = JSON.parse(await readFile(preflightClosurePath(root, previousId), "utf8"));
    if (receipt.version === 3) {
      const current = readEvidence(previousId, replacementId);
      if (current.replacement.mode !== receipt.replacementMode || !isDeepStrictEqual(current.evidence, receipt.proof.evidence)) throw new Error("绑定恢复执行或原失败证据已变化。");
    }
    return;
  }
  if (await stat(preflightClosurePath(root, previousId)).then(() => true, error => {
    if (error.code === "ENOENT") return false; throw error;
  })) throw new Error("旧任务闭合回执未通过复验或已绑定另一执行，需要人工核查；禁止覆盖。");
  const context = readEvidence(previousId, replacementId);
  const policy = classifyJackyunPreflightFailure(context.evidence.error);
  // Other failures retain the existing task/download/import permit mechanisms.
  if (!policy) return;
  if (context.replacement.executionId !== replacementId) throw new Error("新完整执行身份不一致。");
  if (!Number.isFinite(Date.parse(closedAt)) || Date.parse(context.replacement.startedAt) > Date.parse(closedAt)) throw new Error("恢复执行时间不一致。");
  if (policy === "manual" && context.replacement.mode !== "manual") throw new Error("旧任务登录验证或身份异常，需要人工处理后手动完整运行；不自动重试。");
  const proof = await inspectPreflightClosure(root, previousId, context.evidence, closedAt);
  let credentialReadinessVerified = false;
  if (policy === "credential_ready") {
    try {
      credentialReadinessVerified = await (deps.credentialReady ?? (async () => {
        const status = await manageJackyunCredential("status", await readJackyunLoginConfig(root));
        return status.ok && status.ready && status.status === "ready";
      }))() === true;
    } catch { /* The status probe cannot expose raw credential/parser diagnostics. */ }
    if (!credentialReadinessVerified) throw new Error("吉客云凭据仍未就绪，需要人工处理；保留旧任务。");
  }
  if (!isDeepStrictEqual(readEvidence(previousId, replacementId), context)) throw new Error("恢复期间 n8n 执行证据变化，需要人工核查。");
  await publishReplacementPreflightClosure(root, proof, context.replacement, credentialReadinessVerified);
}
