export type JackyunPreflightRecoveryPolicy = "automatic" | "credential_ready" | "manual";

/** Only errors known to precede export callbacks; uncertain login submission is excluded. */
export function classifyJackyunPreflightFailure(message: string): JackyunPreflightRecoveryPolicy | null {
  if (/^API_LOGIN_PAGE_NOT_UNIQUE: eligible=\d{1,6}; total=\d{1,6}; blank=\d{1,6}; jackyun=\d{1,6}; other=[1-9]\d{0,5}$/.test(message)) return "manual";
  if (/^API_LOGIN_PAGE_NOT_UNIQUE(?:: (?:no_context|eligible=\d{1,6}; total=\d{1,6}; blank=\d{1,6}; jackyun=\d{1,6}; other=\d{1,6}))?$/.test(message)
    || message === "waiting_login：吉客云初始页面状态未在限定时间内确认。") return "automatic";
  if (/^waiting_login：吉客云 DPAPI 凭据配置或解密未完成（(?:initialize|binding|binding_input|binding_identity|binding_local_path|binding_vault_lookup|missing)(?: \/ [A-Za-z][A-Za-z0-9.,_-]{0,159})?(?: \/ lookup=provider:[01],file:[01],directory:[01])?）。$/.test(message)) return "credential_ready";
  if (/^waiting_login：吉客云登录已停止（(?:challenge_present|credential_rejected|tenant_mismatch|origin_mismatch|form_ambiguous)）。$/.test(message)
    || message === "waiting_login：专用浏览器端口已占用，自动任务不会接管已打开的浏览器。") return "manual";
  return null;
}
