import type { ImportSourceKey, ModuleKey } from "./navigation-catalog";

export type AppCurrentUser = {
  email: string;
  displayName: string;
  role: "viewer" | "analyst" | "operator" | "admin";
  roleLabel: string;
  scopeRestricted?: boolean;
};

export type AppNavigate = (key: ModuleKey, importSource?: ImportSourceKey) => void;
