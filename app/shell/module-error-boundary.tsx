"use client";

import { Component, createRef, type ReactNode } from "react";

type ModuleErrorBoundaryProps = {
  children: ReactNode;
  resetKey: string;
  onRetry: () => void;
  onOpenDashboard: () => void;
};

type ModuleErrorBoundaryState = { failed: boolean };

export default class ModuleErrorBoundary extends Component<ModuleErrorBoundaryProps, ModuleErrorBoundaryState> {
  state: ModuleErrorBoundaryState = { failed: false };
  private readonly fallbackRef = createRef<HTMLElement>();
  private focusFrame: number | null = null;

  static getDerivedStateFromError(): ModuleErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch() {
    // The fallback intentionally avoids exposing render details or business data.
    this.scheduleFocus(() => this.fallbackRef.current);
  }

  componentDidUpdate(previousProps: ModuleErrorBoundaryProps) {
    if (this.state.failed && previousProps.resetKey !== this.props.resetKey) {
      this.setState({ failed: false });
    }
  }

  componentWillUnmount() {
    if (this.focusFrame !== null && typeof window !== "undefined") {
      window.cancelAnimationFrame(this.focusFrame);
    }
  }

  private scheduleFocus = (resolveTarget: () => HTMLElement | null) => {
    if (typeof window === "undefined") return;
    if (this.focusFrame !== null) window.cancelAnimationFrame(this.focusFrame);
    const focus = () => {
      this.focusFrame = null;
      const target = resolveTarget();
      if (target?.isConnected) target.focus();
    };
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      focus();
      return;
    }
    this.focusFrame = window.requestAnimationFrame(focus);
  };

  private retryCurrentModule = () => {
    this.props.onRetry();
    this.setState({ failed: false }, () => {
      if (!this.state.failed) {
        this.scheduleFocus(() => document.getElementById("global-page-title"));
      }
    });
  };

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <section
        ref={this.fallbackRef}
        className="panel data-state data-state-error"
        role="alert"
        aria-live="assertive"
        aria-atomic="true"
        aria-labelledby="module-error-title"
        aria-describedby="module-error-description"
        tabIndex={-1}
      >
        <span className="state-symbol" aria-hidden="true">!</span>
        <strong id="module-error-title">当前模块发生异常</strong>
        <p id="module-error-description">导航和筛选仍可使用。你可以重试当前模块，或先返回 BI 看板继续工作。</p>
        <div className="data-state-actions">
          <button type="button" className="secondary-button" onClick={this.retryCurrentModule}>重试当前模块</button>
          <button type="button" className="primary-button" onClick={this.props.onOpenDashboard}>返回 BI 看板</button>
        </div>
      </section>
    );
  }
}
