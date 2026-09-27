"use client";

import type {
  ReactNode,
  RefObject,
} from "react";

type GlobalHeaderProps = {
  title: string;
  description: string;
  menuButtonRef: RefObject<HTMLButtonElement | null>;
  titleRef: RefObject<HTMLHeadingElement | null>;
  mobileOpen: boolean;
  onOpenMobile: () => void;
  actions: ReactNode;
};

export default function GlobalHeader({
  title,
  description,
  menuButtonRef,
  titleRef,
  mobileOpen,
  onOpenMobile,
  actions,
}: GlobalHeaderProps) {
  return (
    <header
      className="topbar"
      aria-labelledby="global-page-title"
      aria-describedby="global-page-description"
    >
      <div className="title-area">
        <button
          ref={menuButtonRef}
          type="button"
          className="mobile-menu-button"
          onClick={onOpenMobile}
          aria-label="打开主导航"
          aria-controls="primary-navigation"
          aria-expanded={mobileOpen}
          aria-haspopup="dialog"
        >
          <span aria-hidden="true">☰</span>
        </button>
        <div className="shell-page-heading">
          <h1 id="global-page-title" ref={titleRef} tabIndex={-1}>{title}</h1>
          <span id="global-page-description">{description}</span>
        </div>
      </div>
      <div className="topbar-actions" role="group" aria-label="页面工具">{actions}</div>
    </header>
  );
}
