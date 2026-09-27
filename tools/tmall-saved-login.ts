import type { Frame, Locator, Page } from "playwright-core";

import { readTmallRuntimeCredential, type TmallRuntimeCredential } from "./tmall-secure-credential";

export type TmallSavedCredentialLoginResult = {
  attempted: boolean;
  submitted: boolean;
  reason:
    | "submitted"
    | "login_form_missing"
    | "challenge_present"
    | "saved_credentials_missing"
    | "login_control_missing"
    | "login_control_ambiguous";
};

export type TmallLoginPageState = {
  challengePresent: boolean;
  credentialRejected: boolean;
  temporarilyLocked: boolean;
};

type LoginFrameProbe = {
  formFound: boolean;
  challengePresent: boolean;
  savedCredentialsReady: boolean;
  submitted: boolean;
  controlCount: number;
  passwordModeControlCount: number;
  passwordModeSwitched: boolean;
};

async function probeLoginFrame(frame: Frame, action: "probe" | "submit" | "switch_password_mode" = "probe"): Promise<LoginFrameProbe> {
  return frame.evaluate((requestedAction) => {
    const visible = (element: Element) => {
      const rect = element.getBoundingClientRect();
      const view = element.ownerDocument.defaultView;
      const style = view?.getComputedStyle(element);
      return rect.width > 2 && rect.height > 2 && style?.visibility !== "hidden" && style?.display !== "none";
    };
    const fieldName = (input: HTMLInputElement) => [
      input.type,
      input.name,
      input.id,
      input.getAttribute("autocomplete"),
      input.getAttribute("placeholder"),
      input.getAttribute("aria-label"),
    ].filter(Boolean).join(" ");
    const bodyText = String(document.body?.innerText ?? "");
    const challengePresent = /安全验证|人机验证|短信验证码|动态验证码|滑块验证|请.{0,8}(?:滑动|拖动)/.test(bodyText)
      || Array.from(document.querySelectorAll("iframe")).some((element) => visible(element)
        && /captcha|verify|challenge|punish/i.test(element.getAttribute("src") ?? ""))
      || Array.from(document.querySelectorAll("input")).some((input) => visible(input)
        && /captcha|verify|challenge|验证码|校验码|动态码/i.test(fieldName(input)));
    const inputs = Array.from(document.querySelectorAll("input")).filter(visible);
    const password = inputs.find((input) => String(input.type).toLowerCase() === "password"
      || /pass|密码/i.test(fieldName(input)));
    const account = inputs.find((input) => input !== password
      && /user|account|login|phone|mobile|name|账号|账户|会员名|手机号/i.test(fieldName(input)))
      ?? inputs.find((input) => input !== password
        && ["text", "tel", "email"].includes(String(input.type || "text").toLowerCase()));
    const passwordModeControls = Array.from(document.querySelectorAll('button,a,[role="button"],[role="tab"]'))
      .filter(visible)
      .filter((element) => ["密码登录", "账号密码登录"].includes(String(element.textContent ?? "").replace(/\s+/g, "").trim()));
    if (requestedAction === "switch_password_mode") {
      if (passwordModeControls.length === 1) (passwordModeControls[0] as HTMLElement).click();
      return {
        formFound: Boolean(account && password),
        challengePresent,
        savedCredentialsReady: false,
        submitted: false,
        controlCount: 0,
        passwordModeControlCount: passwordModeControls.length,
        passwordModeSwitched: passwordModeControls.length === 1,
      };
    }
    if (!account || !password) {
      return {
        formFound: false,
        challengePresent,
        savedCredentialsReady: false,
        submitted: false,
        controlCount: 0,
        passwordModeControlCount: passwordModeControls.length,
        passwordModeSwitched: false,
      };
    }
    account.focus();
    password.focus();
    account.focus();
    const accountAutofilled = (() => { try { return account.matches(":-webkit-autofill"); } catch { return false; } })();
    const passwordAutofilled = (() => { try { return password.matches(":-webkit-autofill"); } catch { return false; } })();
    if (!accountAutofilled || !passwordAutofilled || challengePresent) {
      return {
        formFound: true,
        challengePresent,
        savedCredentialsReady: accountAutofilled && passwordAutofilled,
        submitted: false,
        controlCount: 0,
        passwordModeControlCount: passwordModeControls.length,
        passwordModeSwitched: false,
      };
    }
    const controls = Array.from(document.querySelectorAll('button,input[type="submit"],[role="button"],a'))
      .filter(visible)
      .filter((element) => {
        const label = String(element.tagName === "INPUT"
          ? element.getAttribute("value") ?? ""
          : element.textContent ?? "").replace(/\s+/g, "").trim();
        return ["登录", "立即登录"].includes(label);
      })
      .filter((element, _index, candidates) => !candidates.some((other) => other !== element && element.contains(other)));
    if (controls.length !== 1) {
      return {
        formFound: true,
        challengePresent: false,
        savedCredentialsReady: true,
        submitted: false,
        controlCount: controls.length,
        passwordModeControlCount: passwordModeControls.length,
        passwordModeSwitched: false,
      };
    }
    const control = controls[0]!;
    if (control.hasAttribute("disabled") || control.getAttribute("aria-disabled") === "true") {
      return {
        formFound: true,
        challengePresent: false,
        savedCredentialsReady: true,
        submitted: false,
        controlCount: 0,
        passwordModeControlCount: passwordModeControls.length,
        passwordModeSwitched: false,
      };
    }
    if (requestedAction !== "submit") {
      return {
        formFound: true,
        challengePresent: false,
        savedCredentialsReady: true,
        submitted: false,
        controlCount: 1,
        passwordModeControlCount: passwordModeControls.length,
        passwordModeSwitched: false,
      };
    }
    for (const input of [account, password]) {
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    (control as HTMLElement).click();
    return {
      formFound: true,
      challengePresent: false,
      savedCredentialsReady: true,
      submitted: true,
      controlCount: 1,
      passwordModeControlCount: passwordModeControls.length,
      passwordModeSwitched: false,
    };
  }, action);
}

export async function inspectTmallLoginPageState(page: Pick<Page, "frames">): Promise<TmallLoginPageState> {
  const states = await Promise.all(page.frames().map(async (frame) => {
    const probe = await probeLoginFrame(frame).catch(() => null);
    const textState = await frame.evaluate(() => {
      const text = String(document.body?.innerText ?? "");
      return {
        credentialRejected: /账密错误|账号.{0,8}(?:密码|登录).{0,8}(?:错误|不正确)|密码.{0,8}(?:错误|不正确)|用户名.{0,8}(?:错误|不存在)|登录失败/.test(text),
        temporarilyLocked: /操作频繁|次数过多|账号.{0,8}(?:锁定|冻结)/.test(text),
      };
    }).catch(() => ({ credentialRejected: false, temporarilyLocked: false }));
    return {
      challengePresent: Boolean(probe?.challengePresent),
      credentialRejected: textState.credentialRejected,
      temporarilyLocked: textState.temporarilyLocked,
    };
  }));
  return states.reduce<TmallLoginPageState>((combined, state) => ({
    challengePresent: combined.challengePresent || state.challengePresent,
    credentialRejected: combined.credentialRejected || state.credentialRejected,
    temporarilyLocked: combined.temporarilyLocked || state.temporarilyLocked,
  }), { challengePresent: false, credentialRejected: false, temporarilyLocked: false });
}

/**
 * Submit only a username and password that Chromium has already autofilled in
 * the store's dedicated profile. Field values are never read or transported.
 */
export async function autoLoginTmallWithSavedBrowserCredentials(
  page: Pick<Page, "frames">,
  waitMs = 8_000,
  wait: (milliseconds: number) => Promise<void> = async (milliseconds) => {
    await new Promise((resolve) => setTimeout(resolve, milliseconds));
  },
): Promise<TmallSavedCredentialLoginResult> {
  const deadline = Date.now() + Math.max(0, waitMs);
  let formFound = false;
  let savedCredentialsReady = false;
  let passwordModeSelected = false;
  do {
    const frameProbes = await Promise.all(page.frames().map(async (frame) => ({
      frame,
      probe: await probeLoginFrame(frame).catch(() => ({
        formFound: false,
        challengePresent: false,
        savedCredentialsReady: false,
        submitted: false,
        controlCount: 0,
        passwordModeControlCount: 0,
        passwordModeSwitched: false,
      })),
    })));
    const probes = frameProbes.map(({ probe }) => probe);
    if (probes.some((probe) => probe.challengePresent)) {
      return { attempted: false, submitted: false, reason: "challenge_present" };
    }
    if (!probes.some((probe) => probe.formFound) && !passwordModeSelected) {
      const passwordModeFrames = frameProbes.filter(({ probe }) => probe.passwordModeControlCount === 1);
      const passwordModeControlCount = probes.reduce((total, probe) => total + probe.passwordModeControlCount, 0);
      if (passwordModeControlCount > 1) {
        return { attempted: false, submitted: false, reason: "login_control_ambiguous" };
      }
      if (passwordModeFrames.length === 1) {
        const switched = await probeLoginFrame(passwordModeFrames[0]!.frame, "switch_password_mode").catch(() => null);
        passwordModeSelected = Boolean(switched?.passwordModeSwitched);
        if (passwordModeSelected) {
          if (Date.now() >= deadline) break;
          await wait(Math.min(250, Math.max(0, deadline - Date.now())));
          continue;
        }
      }
    }
    const ready = probes.filter((probe) => probe.savedCredentialsReady);
    const eligibleFrames = frameProbes.filter(({ probe }) => probe.savedCredentialsReady && probe.controlCount === 1);
    if (ready.some((probe) => probe.controlCount > 1) || eligibleFrames.length > 1) {
      return { attempted: true, submitted: false, reason: "login_control_ambiguous" };
    }
    if (eligibleFrames.length === 1) {
      const submitted = await probeLoginFrame(eligibleFrames[0]!.frame, "submit").catch(() => null);
      if (submitted?.challengePresent) {
        return { attempted: false, submitted: false, reason: "challenge_present" };
      }
      if (submitted?.submitted) return { attempted: true, submitted: true, reason: "submitted" };
    }
    savedCredentialsReady ||= ready.some((probe) => probe.controlCount === 0);
    formFound ||= probes.some((probe) => probe.formFound);
    if (Date.now() >= deadline) break;
    await wait(Math.min(250, Math.max(0, deadline - Date.now())));
  } while (Date.now() <= deadline);
  return {
    attempted: savedCredentialsReady,
    submitted: false,
    reason: savedCredentialsReady
      ? "login_control_missing"
      : formFound ? "saved_credentials_missing" : "login_form_missing",
  };
}

async function visibleCandidates(locator: Locator, limit = 20) {
  const candidates: Locator[] = [];
  const count = Math.min(await locator.count().catch(() => 0), limit);
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (await candidate.isVisible().catch(() => false)) candidates.push(candidate);
  }
  return candidates;
}

async function secureLoginForm(frame: Frame) {
  const passwords = await visibleCandidates(frame.locator('input[type="password"],input[name*="password" i],input[id*="password" i]'));
  const accounts = (await visibleCandidates(frame.locator([
    "#fm-login-id",
    'input[name="fm-login-id"]',
    'input[autocomplete="username"]',
    'input[type="email"]',
    'input[type="tel"]',
    'input[type="text"]',
  ].join(",")))).filter((candidate) => candidate !== passwords[0]);
  if (passwords.length !== 1 || accounts.length !== 1) return null;
  return { frame, account: accounts[0]!, password: passwords[0]! };
}

/**
 * Fill one unique Taobao password form from the current Windows user's DPAPI
 * vault. Secrets are never accepted through n8n, arguments, environment
 * variables or logs, and field values are never read back from the page.
 */
export async function autoLoginTmallWithWindowsDpapiCredential(
  page: Page,
  storeKey: string,
  loadCredential: (key: string) => Promise<TmallRuntimeCredential> = readTmallRuntimeCredential,
  prepareLogin: (target: Page) => Promise<TmallSavedCredentialLoginResult> = (target) => (
    autoLoginTmallWithSavedBrowserCredentials(target, 3_000)
  ),
  validateFrame: (frame: Frame) => boolean = () => true,
): Promise<TmallSavedCredentialLoginResult> {
  const browserSaved = await prepareLogin(page);
  if (browserSaved.submitted || browserSaved.reason === "challenge_present"
    || browserSaved.reason === "login_control_ambiguous") return browserSaved;

  const forms = (await Promise.all(page.frames().map((frame) => secureLoginForm(frame).catch(() => null))))
    .filter((form): form is NonNullable<typeof form> => Boolean(form));
  if (forms.length !== 1) {
    return {
      attempted: false,
      submitted: false,
      reason: forms.length > 1 ? "login_control_ambiguous" : "login_form_missing",
    };
  }
  const form = forms[0]!;
  const beforeFill = await inspectTmallLoginPageState(page);
  if (beforeFill.challengePresent || beforeFill.credentialRejected || beforeFill.temporarilyLocked) {
    return { attempted: false, submitted: false, reason: "challenge_present" };
  }
  if (!validateFrame(form.frame)) throw new Error("waiting_login：登录表单来源已变化");
  const credential = await loadCredential(storeKey);
  let username = credential.username;
  let password = credential.password;
  try {
    if (!validateFrame(form.frame)) throw new Error("waiting_login：登录表单来源已变化");
    await form.account.fill(username);
    if (!validateFrame(form.frame)) throw new Error("waiting_login：登录表单来源已变化");
    await form.password.fill(password);
  } finally {
    username = "";
    password = "";
    credential.username = "";
    credential.password = "";
  }

  const probes = await Promise.all(page.frames().map((frame) => probeLoginFrame(frame).catch(() => null)));
  if (probes.some((probe) => probe?.challengePresent)) {
    return { attempted: false, submitted: false, reason: "challenge_present" };
  }
  const controls = await visibleCandidates(form.frame.locator('button,input[type="submit"],[role="button"],a'));
  const exactLoginControls: Locator[] = [];
  for (const control of controls) {
    const label = String(await control.textContent().catch(() => "")
      || await control.getAttribute("value").catch(() => ""))
      .replace(/\s+/g, "").trim();
    if (["登录", "立即登录"].includes(label)) exactLoginControls.push(control);
  }
  if (exactLoginControls.length !== 1) {
    return {
      attempted: true,
      submitted: false,
      reason: exactLoginControls.length > 1 ? "login_control_ambiguous" : "login_control_missing",
    };
  }
  if (!validateFrame(form.frame)) throw new Error("waiting_login：登录表单来源已变化");
  await exactLoginControls[0]!.click();
  return { attempted: true, submitted: true, reason: "submitted" };
}
