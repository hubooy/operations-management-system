import type { ComponentType, ReactNode, SVGProps } from "react";

import type { ModuleKey } from "./navigation-catalog";

type ShellSvgIconProps = Omit<SVGProps<SVGSVGElement>, "children">;

function IconFrame({ children, ...props }: ShellSvgIconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

const DashboardIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="3.5" width="7" height="4.5" rx="1.5" />
    <rect x="13.5" y="11" width="7" height="9.5" rx="1.5" />
    <rect x="3.5" y="13" width="7" height="7.5" rx="1.5" />
  </IconFrame>
);

const MarketIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <path d="M4 19.5h16" />
    <path d="m5.5 16 4-4 3 2 5.5-7" />
    <path d="M14.5 7H18v3.5" />
  </IconFrame>
);

const SalesIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M15 8.5h-4.2a2 2 0 0 0 0 4H13a2 2 0 0 1 0 4H8.8" />
    <path d="M12 6.5v11" />
  </IconFrame>
);

const ShopIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <path d="M4 9.5h16l-1.6-5H5.6z" />
    <path d="M5.5 9.5v10h13v-10" />
    <path d="M9 19.5v-5h6v5" />
    <path d="M4 9.5a2.5 2.5 0 0 0 4 2 2.5 2.5 0 0 0 4 0 2.5 2.5 0 0 0 4 0 2.5 2.5 0 0 0 4-2" />
  </IconFrame>
);

const CustomerServiceIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <path d="M4 13v-1a8 8 0 0 1 16 0v1" />
    <path d="M4 12.5h2.5v5H5.7A1.7 1.7 0 0 1 4 15.8zM20 12.5h-2.5v5H20z" />
    <path d="M17.5 17.5c0 1.4-1.2 2.5-2.6 2.5H12" />
  </IconFrame>
);

const ProductIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <path d="m4 7.5 8-4 8 4-8 4z" />
    <path d="M4 7.5v9l8 4 8-4v-9" />
    <path d="M12 11.5v9" />
  </IconFrame>
);

const InventoryIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <path d="M4 8h16v12H4z" />
    <path d="m6 4h12l2 4H4z" />
    <path d="M9 12h6" />
  </IconFrame>
);

const OperationsIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <rect x="5" y="4" width="14" height="16" rx="2" />
    <path d="M8.5 9h7M8.5 13h7M8.5 17H13" />
    <path d="M9 4V2.8M15 4V2.8" />
  </IconFrame>
);

const AutomationIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <circle cx="6" cy="6" r="2" />
    <circle cx="18" cy="12" r="2" />
    <circle cx="6" cy="18" r="2" />
    <path d="M8 6h3a3 3 0 0 1 3 3v0a3 3 0 0 0 3 3M8 18h3a3 3 0 0 0 3-3v0a3 3 0 0 1 3-3" />
  </IconFrame>
);

const AiIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <path d="M12 3.5 13.8 8l4.7 1.8-4.7 1.8L12 16l-1.8-4.4-4.7-1.8L10.2 8z" />
    <path d="m18.5 15 .8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" />
  </IconFrame>
);

const ImportIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <path d="M12 3.5v11" />
    <path d="m8 10.5 4 4 4-4" />
    <path d="M5 17v3h14v-3" />
  </IconFrame>
);

const SettingsIcon = (props: ShellSvgIconProps) => (
  <IconFrame {...props}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.2 13.8a7.7 7.7 0 0 0 0-3.6l1.6-1.2-2-3.4-1.9.8a8 8 0 0 0-3.1-1.8L13.5 2h-4l-.3 2.6a8 8 0 0 0-3.1 1.8l-1.9-.8-2 3.4 1.6 1.2a7.7 7.7 0 0 0 0 3.6L2.2 15l2 3.4 1.9-.8a8 8 0 0 0 3.1 1.8l.3 2.6h4l.3-2.6a8 8 0 0 0 3.1-1.8l1.9.8 2-3.4z" />
  </IconFrame>
);

const moduleIcons: Record<ModuleKey, ComponentType<ShellSvgIconProps>> = {
  dashboard: DashboardIcon,
  market: MarketIcon,
  sales: SalesIcon,
  shop: ShopIcon,
  customer_service: CustomerServiceIcon,
  product: ProductIcon,
  inventory: InventoryIcon,
  workflow: OperationsIcon,
  n8n_workflows: AutomationIcon,
  ai: AiIcon,
  import: ImportIcon,
  settings: SettingsIcon,
};

export function ShellModuleIcon({ moduleKey, ...props }: ShellSvgIconProps & { moduleKey: ModuleKey }) {
  const Icon = moduleIcons[moduleKey];
  return <Icon {...props} />;
}

export function SidebarCollapseIcon({ collapsed, ...props }: ShellSvgIconProps & { collapsed: boolean }) {
  return (
    <IconFrame {...props}>
      <path d={collapsed ? "m9 6 6 6-6 6" : "m15 6-6 6 6 6"} />
    </IconFrame>
  );
}
