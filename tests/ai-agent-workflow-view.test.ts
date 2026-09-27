import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  hasActiveAgentWork,
  resolveAgentPageContextDraft,
} from "../app/ai-agent-workflow-view";
import { createAiPageContext } from "../lib/ai/page-context";

const sourceUrl = new URL("../app/ai-agent-workflow-view.tsx", import.meta.url);
const moduleViewUrl = new URL("../app/ai-module-view.tsx", import.meta.url);

test("AI Agent 工作流视图接入 owner-only 列表与持久状态接口", async () => {
  const source = await readFile(sourceUrl, "utf8");

  assert.match(source, /\/api\/ai\/agent-jobs\?page=1&pageSize=30/);
  assert.match(source, /\/api\/ai\/workflow-runs\?page=1&pageSize=30/);
  assert.match(source, /\/api\/ai\/agent-jobs\/\$\{encodeURIComponent\(job\.id\)\}\/cancel/);
  assert.match(source, /\/api\/ai\/agent-jobs\/\$\{encodeURIComponent\(job\.id\)\}\/resume/);
  assert.match(source, /\/api\/ai\/workflow-runs\/\$\{encodeURIComponent\(run\.id\)\}\/cancel/);
  assert.match(source, /\/api\/ai\/workflow-runs\/\$\{encodeURIComponent\(run\.id\)\}\/resume/);
  assert.match(source, /expectedVersion: job\.version/);
  assert.match(source, /expectedVersion: run\.version/);
});

test("AI Agent 工作流视图限制 viewer 写入并支持 DAG dry-run 与人工复核 CAS", async () => {
  const source = await readFile(sourceUrl, "utf8");

  assert.match(source, /currentUser\?\.role === "analyst"/);
  assert.match(source, /currentUser\?\.role === "operator"/);
  assert.match(source, /currentUser\?\.role === "admin"/);
  assert.match(source, /viewer 可以查看和刷新自己的任务与工作流/);
  assert.match(source, /type: "human_review"/);
  assert.match(source, /const \[dryRun, setDryRun\] = useState\(true\)/);
  assert.match(source, /decision, comment: reviewComment\.trim\(\), expectedVersion: node\.version/);
  assert.match(source, /nodes\/\$\{encodeURIComponent\(node\.key\)\}\/review/);
});

test("AI Agent 工作流视图明确声明安全执行边界", async () => {
  const source = await readFile(sourceUrl, "utf8");

  assert.match(source, /const agentExecutorAvailable = true/);
  assert.match(source, /逐轮派发账本/);
  assert.match(source, /每轮都会重验账号、数据范围、模型版本和工具策略/);
  assert.match(source, /任一外部调用结果未知时会失败关闭且不自动重试/);
  assert.match(source, /正式 Agent 只使用中央注册表中的有界只读工具/);
  assert.match(source, /pandas 临时分析需要独立容器服务就绪/);
  assert.match(source, /只能使用当前账号获准的数据，不修改业务记录/);
  assert.match(source, /取消只能阻止尚未派发的下一步/);
  assert.match(source, /每个 Agent 节点都可能产生模型费用/);
});

test("AI Agent 工作流视图读取固定模型与工具策略快照并开放受控正式创建", async () => {
  const source = await readFile(sourceUrl, "utf8");

  assert.match(source, /modelId: string/);
  assert.match(source, /modelVersion: number/);
  assert.match(source, /allowedTools: string\[\]/);
  assert.match(source, /toolPolicyDigest: string/);
  assert.match(source, /providerRoundCount: number/);
  assert.match(source, /toolCallCount: number/);
  assert.match(source, /providerDispatchStartedAt: string \| null/);
  assert.match(source, /profile v\$\{job\.modelVersion\}/);
  assert.match(source, /profile v\$\{run\.modelVersion\}/);
  assert.match(source, /创建正式 Agent/);
  assert.match(source, /正式编排（Agent 节点会调用模型）/);
  assert.match(source, /disabled=\{!canMutate \|\| busyKey === "create-agent"\}/);
  assert.match(source, /disabled=\{!canMutate \|\| busyKey === "create-workflow"\}/);
});

test("当前页面上下文只在空白 Agent 创建表单中预填且不会自动提交", async () => {
  const pageContext = createAiPageContext({
    module: "inventory",
    view: "age",
    startDate: "2026-08-01",
    endDate: "2026-08-27",
  });
  const draft = resolveAgentPageContextDraft({
    currentTask: "",
    currentInput: "{}",
    initialContextPrompt: "分析当前库存页面",
    initialPageContext: pageContext,
  });
  assert.equal(draft?.task, "分析当前库存页面");
  assert.deepEqual(JSON.parse(draft?.structuredInput ?? "null"), { pageContext });
  assert.equal(resolveAgentPageContextDraft({
    currentTask: "保留已有任务",
    currentInput: "{}",
    initialContextPrompt: "不要覆盖",
    initialPageContext: pageContext,
  }), null);
  assert.equal(resolveAgentPageContextDraft({
    currentTask: "",
    currentInput: '{"custom":true}',
    initialContextPrompt: "不要覆盖",
    initialPageContext: pageContext,
  }), null);

  const [source, moduleView] = await Promise.all([
    readFile(sourceUrl, "utf8"),
    readFile(moduleViewUrl, "utf8"),
  ]);
  assert.match(moduleView, /<AiAgentWorkflowView[\s\S]*?initialContextPrompt=\{initialContextPrompt\}[\s\S]*?initialPageContext=\{initialPageContext\}/);
  assert.match(source, /请确认任务与输入后再创建，不会自动提交/);
  const prefillEffect = source.match(/useEffect\(\(\) => \{[\s\S]*?const prompt = Array\.from\(initialContextPrompt\.trim\(\)\)[\s\S]*?\}, \[agentInput, agentTask, initialContextPrompt, initialPageContext\]\);/)?.[0];
  assert.ok(prefillEffect);
  assert.doesNotMatch(prefillEffect, /fetch\(|createAgent\(/);
});

test("Agent 工作区只在可推进任务存在时执行防迟到的后台轮询", async () => {
  assert.equal(hasActiveAgentWork([{ status: "queued" }], []), true);
  assert.equal(hasActiveAgentWork([], [{ status: "running" }]), true);
  assert.equal(hasActiveAgentWork([{ status: "paused" }], [{ status: "waiting_review" }]), false);
  assert.equal(hasActiveAgentWork([{ status: "completed" }], [{ status: "failed" }]), false);

  const source = await readFile(sourceUrl, "utf8");
  assert.match(source, /if \(!hasActiveWork \|\| loading \|\| busyKey\) return/);
  assert.match(source, /await load\(\{ background: true \}\)/);
  assert.match(source, /window\.setTimeout\(\(\) => void tick\(\), 4_000\)/);
  assert.match(source, /controller\.signal\.aborted \|\| generation !== generationRef\.current/);
  assert.match(source, /generation === detailGenerationRef\.current/);
  assert.match(source, /listControllerRef\.current\?\.abort\(\)/);
  assert.match(source, /detailControllerRef\.current\?\.abort\(\)/);
});
