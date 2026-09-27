export type MarketMasterAdminCurrentUser = {
  email: string;
  role: "viewer" | "analyst" | "operator" | "admin";
} | null;

export type MarketMasterAdminMode = "database" | "subcategory" | "brand" | "mapping" | "data";

export type MarketMasterAdminPanelProps = {
  currentUser: MarketMasterAdminCurrentUser;
  mode?: MarketMasterAdminMode;
};

export function canCloseMarketSkuEditor(busyAction: string): boolean {
  return busyAction !== "update_sku_master";
}
