"use client";

import { Component, lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { AI_PAGE_CONTEXT_CATALOG, type AiPageContext, type AiPageModule } from "@/lib/ai/page-context";
import type { AppCurrentUser } from "./shell/view-contract";

const Chat = lazy(() => import("./ai-assistant-view"));

class ChatBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div className="panel data-state data-state-error" role="alert">
      <strong>本板块对话暂时无法打开</strong>
      <p>其他页面可继续使用，已保存的聊天记录可以在刷新后恢复。</p>
      <button type="button" className="secondary-button" onClick={() => window.location.reload()}>刷新页面重试</button>
    </div>;
  }
}

/** One mounted controller per visited module; hiding never cancels an admitted request. */
export default function AiWorkspaceHost({ currentUser, module, context, contextError, open, fullPage, onClose, prompt, promptModule, promptId }: {
  currentUser: AppCurrentUser; module: AiPageModule; context: AiPageContext;
  contextError?: string;
  open: boolean; fullPage: boolean; onClose: () => void;
  prompt: string; promptModule?: string; promptId: number;
}) {
  const [visited, setVisited] = useState<AiPageModule[]>([]);
  useEffect(() => {
    if (open || fullPage) setVisited(current => current.includes(module) ? current : [...current, module]);
  }, [module, open, fullPage]);
  useEffect(() => {
    if (!open || fullPage) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) onClose();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open, fullPage, onClose]);
  return <>{visited.map(key => <section key={key}
    id={fullPage && key === "ai" ? "ai-panel-assistant" : undefined}
    role="region"
    hidden={key !== module || (!open && !fullPage)}
    className={fullPage && key === "ai" ? "ai-module-workspace" : "ai-module-drawer"}
    aria-label={`${AI_PAGE_CONTEXT_CATALOG[key].label} AI 对话`}>
    {!(fullPage && key === "ai") && <header className="ai-module-drawer-heading">
      <div><strong>{AI_PAGE_CONTEXT_CATALOG[key].label} · AI 对话</strong><small>本板块的会话自动保存</small></div>
      <button type="button" aria-label="关闭 AI 对话" onClick={onClose}>×</button>
    </header>}
    <ChatBoundary><Suspense fallback={<p role="status">正在恢复本板块对话…</p>}>
      <Chat currentUser={currentUser} workspaceModule={key} compact={key !== "ai" || !fullPage}
        initialPageContext={key === module ? context : undefined}
        contextError={key === module ? contextError : undefined}
        initialContextPrompt={promptModule === key ? prompt : ""}
        contextRequestId={promptModule === key ? promptId : 0} />
    </Suspense></ChatBoundary>
  </section>)}</>;
}
