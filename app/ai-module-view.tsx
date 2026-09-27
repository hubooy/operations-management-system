"use client";

import { lazy, Suspense, type KeyboardEvent } from "react";

import type { AiPageContext } from "@/lib/ai/page-context";
import type { ModuleViewKey } from "./shell/navigation-catalog";
import type { AppCurrentUser } from "./shell/view-contract";

const AiAssistantView = lazy(() => import("./ai-assistant-view"));
const AiAgentWorkflowView = lazy(() => import("./ai-agent-workflow-view"));
const AiMemoryView = lazy(() => import("./ai-memory-view"));
const AiSpaceView = lazy(() => import("./ai-space-view"));
const AiSpaceManagementView = lazy(() => import("./ai-space-management-view"));
const AiReportWorkbenchView = lazy(() => import("./ai-report-workbench-view"));
const AiPromptSettingsView = lazy(() => import("./ai-prompt-settings-view"));
const AiDingTalkSchedulesView = lazy(() => import("./ai-dingtalk-schedules-view"));

type AiView = ModuleViewKey<"ai">;

const aiViews: readonly AiView[] = ["reports", "skills", "pipelines", "agents", "memory", "space", "management", "scheduled", "configuration"];
const aiViewLabels: Record<AiView, string> = {
  reports: "报告模板",
  skills: "Skill 管理",
  pipelines: "AI 流水线",
  assistant: "AI 对话",
  configuration: "配置设置",
  agents: "Agent 工作流",
  memory: "全局记忆",
  space: "AI 空间",
  management: "AI 管理",
  scheduled: "AI定时任务",
};

function AiViewLoading({ label }: { label: string }) {
  return <section className="panel data-state" role="status" aria-live="polite">
    <span className="state-spinner" />
    <strong>正在加载{label}</strong>
    <p>仅载入当前工作区所需的界面与数据…</p>
  </section>;
}

export default function AiModuleView({
  currentUser,
  externalChat = false,
  initialContextPrompt = "",
  initialPageContext = null,
  moduleView,
  onModuleViewChange,
}: {
  currentUser: AppCurrentUser | null;
  externalChat?: boolean;
  initialContextPrompt?: string;
  initialPageContext?: AiPageContext | null;
  moduleView: AiView;
  onModuleViewChange: (view: AiView) => void;
}) {
  const canManage = currentUser?.role === "admin" && !currentUser.scopeRestricted;
  const availableViews = canManage ? aiViews : aiViews.filter((view) => view !== "management" && view !== "scheduled" && view !== "configuration" && view !== "reports" && view !== "skills" && view !== "pipelines");
  const changeWithKeyboard = (event: KeyboardEvent<HTMLButtonElement>, current: AiView) => {
    if (!(["ArrowLeft", "ArrowRight", "Home", "End"] as string[]).includes(event.key)) return;
    event.preventDefault();
    const index = availableViews.indexOf(current);
    const next = event.key === "Home"
      ? availableViews[0]!
      : event.key === "End"
        ? availableViews.at(-1)!
        : availableViews[(index + (event.key === "ArrowRight" ? 1 : -1) + availableViews.length) % availableViews.length]!;
    onModuleViewChange(next);
    window.setTimeout(() => document.getElementById(`ai-tab-${next}`)?.focus(), 0);
  };

  return <>
    {moduleView !== "assistant" && <div className="subnav ai-module-tabs" role="tablist" aria-label="AI 工作区">
      {availableViews.map((view) => <button
        key={view}
        id={`ai-tab-${view}`}
        type="button"
        role="tab"
        className={moduleView === view ? "active" : ""}
        aria-selected={moduleView === view}
        aria-controls={`ai-panel-${view}`}
        tabIndex={moduleView === view ? 0 : -1}
        onClick={() => onModuleViewChange(view)}
        onKeyDown={(event) => changeWithKeyboard(event, view)}
      >{aiViewLabels[view]}</button>)}
    </div>}

    {(["reports", "skills", "pipelines"] as string[]).includes(moduleView) && (canManage ? <div id={"ai-panel-" + moduleView} role="tabpanel" aria-labelledby={"ai-tab-" + moduleView}><Suspense fallback={<AiViewLoading label={aiViewLabels[moduleView]} />}><AiReportWorkbenchView key={moduleView} kind={moduleView === "reports" ? "templates" : moduleView === "skills" ? "skills" : "pipelines"} /></Suspense></div> : <section className="panel" role="alert">报告与方法管理仅允许无数据范围限制的管理员访问。</section>)}
    {moduleView === "configuration" && (canManage ? <div id="ai-panel-configuration" role="tabpanel" aria-labelledby="ai-tab-configuration"><Suspense fallback={<AiViewLoading label="配置设置" />}><AiPromptSettingsView /></Suspense></div> : <section className="panel" role="alert">配置设置仅允许无数据范围限制的管理员访问。</section>)}
    {moduleView === "assistant" && !externalChat && <div id="ai-panel-assistant" role="region" aria-label="AI 对话" tabIndex={0}>
      <Suspense fallback={<AiViewLoading label={aiViewLabels.assistant} />}>
        <AiAssistantView currentUser={currentUser} initialContextPrompt={initialContextPrompt} initialPageContext={initialPageContext} workspace="chat" />
      </Suspense>
    </div>}
    {moduleView === "agents" && <div id="ai-panel-agents" role="tabpanel" aria-labelledby="ai-tab-agents" tabIndex={0}>
      <Suspense fallback={<AiViewLoading label={aiViewLabels.agents} />}>
        <AiAgentWorkflowView
          currentUser={currentUser}
          initialContextPrompt={initialContextPrompt}
          initialPageContext={initialPageContext}
        />
      </Suspense>
    </div>}
    {moduleView === "memory" && <div id="ai-panel-memory" role="tabpanel" aria-labelledby="ai-tab-memory" tabIndex={0}>
      <Suspense fallback={<AiViewLoading label={aiViewLabels.memory} />}>
        <AiMemoryView currentUser={currentUser} />
      </Suspense>
    </div>}
    {moduleView === "space" && <div id="ai-panel-space" role="tabpanel" aria-labelledby="ai-tab-space" tabIndex={0}>
      <Suspense fallback={<AiViewLoading label={aiViewLabels.space} />}>
        <AiSpaceView onOpenManagement={() => onModuleViewChange("management")} />
      </Suspense>
    </div>}
    {moduleView === "management" && canManage && <div id="ai-panel-management" role="tabpanel" aria-labelledby="ai-tab-management" tabIndex={0}>
      <Suspense fallback={<AiViewLoading label={aiViewLabels.management} />}>
        <AiAssistantView currentUser={currentUser} workspace="management" />
        <AiSpaceManagementView currentUser={currentUser} />
      </Suspense>
    </div>}
    {moduleView === "scheduled" && canManage && <div id="ai-panel-scheduled" role="tabpanel" aria-labelledby="ai-tab-scheduled" tabIndex={0}>
      <Suspense fallback={<AiViewLoading label={aiViewLabels.scheduled} />}>
        <AiDingTalkSchedulesView />
      </Suspense>
    </div>}
    {moduleView === "scheduled" && !canManage && <section className="panel ai-permission-card" role="alert">
      <h2>AI定时任务不可用</h2><p>仅无数据范围限制的管理员可见并维护 AI定时任务。</p>
    </section>}
    {moduleView === "management" && !canManage && <section className="panel ai-permission-card" role="alert">
      <h2>AI 管理不可用</h2><p>仅无数据范围限制的管理员可见并维护 AI 管理工作区。</p>
    </section>}
  </>;
}
