export type LocalDirectAccessContext = {
  enabled: string | undefined;
  runtimeEnvironment: string | undefined;
  viteDevelopment: boolean;
  viteProduction: boolean;
  nodeEnvironment?: string;
  localBuild?: boolean;
};

export type LocalDirectAccessDecision =
  | "disabled"
  | "allowed"
  | "role_denied";

export function isLoopbackRequestHost(value: string | null | undefined): boolean {
  const input = value?.trim();
  if (!input || /[\\/@]/.test(input)) return false;
  try {
    const hostname = new URL(`http://${input}`).hostname.toLowerCase().replace(/\.$/, "");
    return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
  } catch {
    return false;
  }
}

/**
 * Local direct access is deliberately fail-closed. Besides an explicit opt-in,
 * it requires both the deployment binding and either Vite development mode or
 * an explicit local-only build stamp. A hosted production build therefore
 * cannot be turned into anonymous admin access by a stray runtime variable.
 */
export function decideLocalDirectAccess(
  allowedRoles: readonly string[],
  context: LocalDirectAccessContext,
  localRole = "admin",
): LocalDirectAccessDecision {
  const enabled = context.enabled?.trim().toLowerCase() === "true";
  const declaredDevelopment =
    context.runtimeEnvironment?.trim().toLowerCase() === "development";
  const verifiedDevelopment =
    context.localBuild === true ||
    (context.viteProduction !== true && (
      context.viteDevelopment === true ||
      context.nodeEnvironment?.trim().toLowerCase() === "development"
    ));

  if (!enabled || !declaredDevelopment || !verifiedDevelopment) {
    return "disabled";
  }

  return allowedRoles.includes(localRole) ? "allowed" : "role_denied";
}
