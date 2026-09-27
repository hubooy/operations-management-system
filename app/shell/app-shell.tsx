"use client";

import {
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";

type AppShellProps = {
  collapsed: boolean;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  sidebar: ReactNode;
  header: ReactNode;
  children: ReactNode;
};

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[contenteditable='true']",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function focusTrapTargetIndex(
  activeIndex: number,
  itemCount: number,
  movingBackward: boolean,
): number | null {
  if (itemCount <= 0) return null;
  if (activeIndex < 0) return movingBackward ? itemCount - 1 : 0;
  if (movingBackward && activeIndex === 0) return itemCount - 1;
  if (!movingBackward && activeIndex === itemCount - 1) return 0;
  return null;
}

function scheduleFocus(element: HTMLElement | null) {
  if (!element) return () => undefined;
  const focus = () => {
    if (element.isConnected && !element.closest("[inert],[aria-hidden='true']")) element.focus();
  };
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    focus();
    return () => undefined;
  }
  const frame = window.requestAnimationFrame(focus);
  return () => window.cancelAnimationFrame(frame);
}

export default function AppShell({
  collapsed,
  mobileOpen,
  onCloseMobile,
  sidebar,
  header,
  children,
}: AppShellProps) {
  const shellRef = useRef<HTMLDivElement>(null);
  const mastheadRef = useRef<HTMLDivElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const [mobileViewport, setMobileViewport] = useState(false);
  const mobileDrawerActive = mobileOpen && mobileViewport;
  const mobileDrawerHidden = mobileViewport && !mobileDrawerActive;

  useEffect(() => {
    const masthead = mastheadRef.current;
    const shell = shellRef.current;
    if (!masthead || !shell) return;
    const measure = () => shell.style.setProperty("--app-sticky-offset", `${masthead.getBoundingClientRect().height}px`);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(masthead);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 860px)");
    const syncViewport = (matches: boolean) => {
      setMobileViewport(matches);
      if (!matches && mobileOpen) onCloseMobile();
    };
    syncViewport(media.matches);
    const onChange = (event: MediaQueryListEvent) => syncViewport(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [mobileOpen, onCloseMobile]);

  useEffect(() => {
    if (!mobileDrawerActive) return;

    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const sidebarElement = sidebarRef.current;
    const currentItem = sidebarElement?.querySelector<HTMLElement>("[aria-current='page']");
    const firstFocusable = sidebarElement?.querySelector<HTMLElement>(focusableSelector);
    const cancelInitialFocus = scheduleFocus(currentItem ?? firstFocusable ?? null);

    const onKeyDown = (event: KeyboardEvent) => {
      const foregroundDialog = Array.from(
        document.querySelectorAll<HTMLElement>("[role='dialog'][aria-modal='true']"),
      ).some((dialog) => dialog !== sidebarElement && !sidebarElement?.contains(dialog));
      if (foregroundDialog) return;

      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        onCloseMobile();
        return;
      }
      if (event.key !== "Tab" || !sidebarElement) return;
      const focusable = Array.from(sidebarElement.querySelectorAll<HTMLElement>(focusableSelector))
        .filter((element) => (
          element.tabIndex >= 0
          && !element.hidden
          && !element.closest("[hidden],[inert],[aria-hidden='true']")
        ));
      if (focusable.length === 0) {
        event.preventDefault();
        sidebarElement.focus();
        return;
      }
      const activeIndex = focusable.indexOf(document.activeElement as HTMLElement);
      const targetIndex = focusTrapTargetIndex(activeIndex, focusable.length, event.shiftKey);
      if (targetIndex !== null) {
        event.preventDefault();
        focusable[targetIndex]?.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      cancelInitialFocus();
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      scheduleFocus(previousFocus);
    };
  }, [mobileDrawerActive, onCloseMobile]);

  return (
    <div ref={shellRef} className={`app-shell top-navigation-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <div ref={mastheadRef} className="shell-masthead">
        <section
          ref={sidebarRef}
          id="primary-navigation"
          className={`sidebar ${mobileDrawerActive ? "mobile-open" : ""}`}
          aria-label="顶部应用导航"
          aria-hidden={mobileDrawerHidden || undefined}
          aria-modal={mobileDrawerActive || undefined}
          inert={mobileDrawerHidden || undefined}
          role={mobileDrawerActive ? "dialog" : undefined}
          tabIndex={mobileDrawerActive ? -1 : undefined}
        >
          {sidebar}
          <button
            type="button"
            className="mobile-navigation-close"
            onClick={onCloseMobile}
            aria-label="关闭主导航"
          >
            <span aria-hidden="true">×</span>
          </button>
        </section>
        {mobileDrawerActive && (
          <button
            type="button"
            className="mobile-overlay"
            onClick={onCloseMobile}
            aria-label="关闭导航"
            tabIndex={-1}
          />
        )}
        <div className="masthead-tools" inert={mobileDrawerActive || undefined}>{header}</div>
      </div>
      <main id="main-content" className="workspace" inert={mobileDrawerActive || undefined}>
        {children}
      </main>
    </div>
  );
}
