"use client";

import {
  useEffect,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type MouseEvent,
} from "react";

import { navGroups, navItems, type ModuleKey, type ModuleViewKey, type NavItem } from "./navigation-catalog";
import { ShellModuleIcon, SidebarCollapseIcon } from "./shell-icons";

export type SidebarNavigationProps = {
  active: ModuleKey;
  activeView?: ModuleViewKey;
  collapsed: boolean;
  hrefForModule: (moduleKey: ModuleKey, view?: ModuleViewKey) => string;
  onNavigate: (event: MouseEvent<HTMLAnchorElement>, moduleKey: ModuleKey, view?: ModuleViewKey) => void;
  onToggleCollapsed: () => void;
};

const navItemsByKey: ReadonlyMap<ModuleKey, NavItem> = new Map(
  navItems.map((item) => [item.key, item]),
);

export default function SidebarNavigation({
  active,
  activeView = "assistant",
  collapsed,
  hrefForModule,
  onNavigate,
  onToggleCollapsed,
}: SidebarNavigationProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    const revealCurrent = () => {
      if (window.matchMedia("(max-width: 860px)").matches) return;
      const selected = viewport.querySelector<HTMLElement>("[aria-current='page']");
      if (!selected) return;
      const bounds = viewport.getBoundingClientRect();
      const item = selected.getBoundingClientRect();
      if (item.left < bounds.left) viewport.scrollLeft += item.left - bounds.left;
      else if (item.right > bounds.right) viewport.scrollLeft += item.right - bounds.right;
    };
    revealCurrent();
    const observer = new ResizeObserver(revealCurrent);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [active, activeView, collapsed]);
  const [tooltip, setTooltip] = useState<{
    id: string;
    label: string;
    description: string;
    top: number;
    left: number;
  } | null>(null);
  const showTooltip = (element: HTMLElement, item: NavItem, id: string) => {
    if (!collapsed) return;
    const navRect = element.closest("nav")?.getBoundingClientRect();
    const linkRect = element.getBoundingClientRect();
    setTooltip({
      id,
      label: item.label,
      description: item.description,
      top: linkRect.bottom - (navRect?.top ?? 0) + 8,
      left: Math.max(0, Math.min(linkRect.left - (navRect?.left ?? 0), (navRect?.width ?? 220) - 220)),
    });
  };
  const hideTooltip = (event: MouseEvent<HTMLElement> | FocusEvent<HTMLElement>) => {
    if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
    setTooltip(null);
  };
  const dismissTooltip = (event: KeyboardEvent<HTMLAnchorElement>) => {
    if (event.key === "Escape") setTooltip(null);
  };

  return (
    <nav className="main-nav sidebar-navigation" aria-label="主导航">
      <div ref={scrollRef} className="sidebar-navigation-scroll" onScroll={() => setTooltip(null)}>
        <ul className="sidebar-navigation-groups">
          {navGroups.map((group, groupIndex) => {
            const groupLabelId = `sidebar-navigation-group-${groupIndex}`;
            return (
              <li className="nav-group" key={group.label}>
                <p id={groupLabelId} aria-hidden={collapsed || undefined}>{group.label}</p>
                <ul
                  aria-label={collapsed ? group.label : undefined}
                  aria-labelledby={collapsed ? undefined : groupLabelId}
                >
                  {groupIndex === 0 && <li><a href={hrefForModule("ai", "assistant")}
                    className={`sidebar-navigation-link${active === "ai" && activeView === "assistant" ? " active" : ""}`}
                    aria-current={active === "ai" && activeView === "assistant" ? "page" : undefined}
                    title="AI 对话" aria-label="AI 对话" onClick={event => onNavigate(event, "ai", "assistant")}
                  ><span className="nav-icon" aria-hidden="true"><ShellModuleIcon moduleKey="ai" /></span>
                    <span className="nav-copy"><b>AI 对话</b><small>小特对话工作台</small></span></a></li>}
                  {group.keys.map((moduleKey) => {
                    const item = navItemsByKey.get(moduleKey);
                    if (!item) return null;
                    const tooltipId = `sidebar-navigation-tooltip-${moduleKey}`;
                    const selected = active === moduleKey && (moduleKey !== "ai" || activeView !== "assistant");
                    const tooltipVisible = collapsed && tooltip?.id === tooltipId;

                    return (
                      <li key={moduleKey}>
                        <a
                          href={hrefForModule(moduleKey, moduleKey === "ai" ? "agents" : undefined)}
                          className={`sidebar-navigation-link${selected ? " active" : ""}`}
                          aria-current={selected ? "page" : undefined}
                          aria-label={collapsed ? item.label : undefined}
                          aria-describedby={tooltipVisible ? tooltipId : undefined}
                          title={collapsed ? `${item.label} · ${item.description}` : undefined}
                          onClick={(event) => {
                            setTooltip(null);
                            onNavigate(event, moduleKey, moduleKey === "ai" ? "agents" : undefined);
                          }}
                          onMouseEnter={(event) => showTooltip(event.currentTarget, item, tooltipId)}
                          onMouseLeave={hideTooltip}
                          onFocus={(event) => showTooltip(event.currentTarget, item, tooltipId)}
                          onBlur={hideTooltip}
                          onKeyDown={dismissTooltip}
                        >
                          <span className="nav-icon" aria-hidden="true">
                            <ShellModuleIcon moduleKey={moduleKey} />
                          </span>
                          <span className="nav-copy">
                            <b>{item.label}</b>
                            <small>{item.description}</small>
                          </span>
                          {item.badge ? <em aria-label={`${item.badge} 项待处理`}>{item.badge}</em> : null}
                        </a>
                      </li>
                    );
                  })}
                </ul>
              </li>
            );
          })}
        </ul>
      </div>
      {collapsed && tooltip ? (
        <span
          className="sidebar-navigation-tooltip"
          id={tooltip.id}
          role="tooltip"
          style={{ top: tooltip.top, left: tooltip.left }}
        >
          <strong aria-hidden="true">{tooltip.label}</strong>
          <span>{tooltip.description}</span>
        </span>
      ) : null}
      <button
        type="button"
        className="collapse-button sidebar-collapse-button"
        aria-label={collapsed ? "展开菜单文字" : "紧凑显示菜单"}
        aria-controls="primary-navigation"
        aria-expanded={!collapsed}
        onClick={() => {
          setTooltip(null);
          onToggleCollapsed();
        }}
      >
        <SidebarCollapseIcon collapsed={collapsed} />
      </button>
    </nav>
  );
}
