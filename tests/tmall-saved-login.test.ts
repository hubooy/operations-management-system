import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import type { Frame, Page } from "playwright-core";

import {
  autoLoginTmallWithSavedBrowserCredentials,
  autoLoginTmallWithWindowsDpapiCredential,
  inspectTmallLoginPageState,
} from "../tools/tmall-saved-login";

type Probe = {
  formFound: boolean;
  challengePresent: boolean;
  savedCredentialsReady: boolean;
  submitted: boolean;
  controlCount: number;
  passwordModeControlCount?: number;
  passwordModeSwitched?: boolean;
};

function fakePage(...probes: Probe[]): Pick<Page, "frames"> {
  return {
    frames: () => probes.map((probe) => ({
      evaluate: async () => probe,
    }) as unknown as Frame),
  };
}

test("天猫自动登录只提交 Chromium 已自动填充的保存密码", async () => {
  assert.deepEqual(await autoLoginTmallWithSavedBrowserCredentials(fakePage({
    formFound: true,
    challengePresent: false,
    savedCredentialsReady: true,
    submitted: true,
    controlCount: 1,
  }), 0), {
    attempted: true,
    submitted: true,
    reason: "submitted",
  });

  const source = await readFile(new URL("../tools/tmall-saved-login.ts", import.meta.url), "utf8");
  assert.match(source, /:-webkit-autofill/);
  assert.doesNotMatch(source, /(?:account|password)\.value\b|TMALL_(?:USERNAME|PASSWORD)|credentials\.json/i);
  assert.doesNotMatch(source, /console\.(?:log|error).*password/i);
});

test("天猫自动登录可唯一切换密码登录模式后再提交保存密码", async () => {
  let passwordMode = false;
  let submitted = false;
  const page = {
    frames: () => [{
      evaluate: async (_callback: unknown, action: string) => {
        if (action === "switch_password_mode") {
          passwordMode = true;
          return {
            formFound: false,
            challengePresent: false,
            savedCredentialsReady: false,
            submitted: false,
            controlCount: 0,
            passwordModeControlCount: 1,
            passwordModeSwitched: true,
          };
        }
        if (action === "submit") submitted = true;
        return passwordMode ? {
          formFound: true,
          challengePresent: false,
          savedCredentialsReady: true,
          submitted,
          controlCount: 1,
          passwordModeControlCount: 1,
          passwordModeSwitched: false,
        } : {
          formFound: false,
          challengePresent: false,
          savedCredentialsReady: false,
          submitted: false,
          controlCount: 0,
          passwordModeControlCount: 1,
          passwordModeSwitched: false,
        };
      },
    } as unknown as Frame],
  } as Pick<Page, "frames">;
  assert.deepEqual(await autoLoginTmallWithSavedBrowserCredentials(page, 1_000, async () => {}), {
    attempted: true,
    submitted: true,
    reason: "submitted",
  });
});

test("天猫自动登录遇到验证码、按钮歧义或缺少保存密码时失败关闭", async () => {
  assert.deepEqual(await autoLoginTmallWithSavedBrowserCredentials(fakePage({
    formFound: true,
    challengePresent: true,
    savedCredentialsReady: true,
    submitted: false,
    controlCount: 1,
  }), 0), {
    attempted: false,
    submitted: false,
    reason: "challenge_present",
  });
  assert.deepEqual(await autoLoginTmallWithSavedBrowserCredentials(fakePage({
    formFound: true,
    challengePresent: false,
    savedCredentialsReady: true,
    submitted: false,
    controlCount: 2,
  }), 0), {
    attempted: true,
    submitted: false,
    reason: "login_control_ambiguous",
  });
  assert.deepEqual(await autoLoginTmallWithSavedBrowserCredentials(fakePage({
    formFound: true,
    challengePresent: false,
    savedCredentialsReady: false,
    submitted: false,
    controlCount: 0,
  }), 0), {
    attempted: false,
    submitted: false,
    reason: "saved_credentials_missing",
  });
  assert.deepEqual(await autoLoginTmallWithSavedBrowserCredentials(fakePage({
    formFound: true,
    challengePresent: false,
    savedCredentialsReady: true,
    submitted: false,
    controlCount: 0,
  }), 0), {
    attempted: true,
    submitted: false,
    reason: "login_control_missing",
  });
  assert.deepEqual(await autoLoginTmallWithSavedBrowserCredentials(fakePage(
    {
      formFound: true,
      challengePresent: false,
      savedCredentialsReady: true,
      submitted: false,
      controlCount: 1,
    },
    {
      formFound: true,
      challengePresent: false,
      savedCredentialsReady: true,
      submitted: false,
      controlCount: 1,
    },
  ), 0), {
    attempted: true,
    submitted: false,
    reason: "login_control_ambiguous",
  });
});

function secureLoginPage(options: { challenge?: boolean; forms?: number } = {}) {
  const filled = { account: "", password: "", clicked: false };
  const forms = Array.from({ length: options.forms ?? 1 }, () => {
    const account = {
      isVisible: async () => true,
      fill: async (value: string) => { filled.account = value; },
    };
    const password = {
      isVisible: async () => true,
      fill: async (value: string) => { filled.password = value; },
    };
    const control = {
      isVisible: async () => true,
      textContent: async () => "登录",
      getAttribute: async () => null,
      click: async () => { filled.clicked = true; },
    };
    const collection = (items: unknown[]) => ({
      count: async () => items.length,
      nth: (index: number) => items[index],
    });
    return {
      evaluate: async () => ({
        formFound: true,
        challengePresent: Boolean(options.challenge),
        savedCredentialsReady: false,
        submitted: false,
        controlCount: 0,
        passwordModeControlCount: 0,
        passwordModeSwitched: false,
      }),
      locator: (selector: string) => {
        if (selector.includes('input[type="password"]')) return collection([password]);
        if (selector.includes("#fm-login-id")) return collection([account]);
        return collection([control]);
      },
    } as unknown as Frame;
  });
  return {
    page: { frames: () => forms } as unknown as Page,
    filled,
  };
}

test("Windows DPAPI 登录只向唯一表单填入内存凭据且提交后清空对象", async () => {
  const { page, filled } = secureLoginPage();
  const credential = { username: "vault-account", password: "vault-password" };
  const result = await autoLoginTmallWithWindowsDpapiCredential(
    page,
    "tmall-yijiu",
    async () => credential,
    async () => ({ attempted: false, submitted: false, reason: "saved_credentials_missing" }),
  );
  assert.deepEqual(result, { attempted: true, submitted: true, reason: "submitted" });
  assert.deepEqual(filled, { account: "vault-account", password: "vault-password", clicked: true });
  assert.deepEqual(credential, { username: "", password: "" });
});

test("Windows DPAPI 登录在跨 frame 歧义或验证码时不提交", async () => {
  let credentialReads = 0;
  const ambiguous = secureLoginPage({ forms: 2 });
  assert.deepEqual(await autoLoginTmallWithWindowsDpapiCredential(
    ambiguous.page,
    "tmall-yijiu",
    async () => { credentialReads += 1; return { username: "unused", password: "unused" }; },
    async () => ({ attempted: false, submitted: false, reason: "saved_credentials_missing" }),
  ), { attempted: false, submitted: false, reason: "login_control_ambiguous" });
  assert.equal(credentialReads, 0);

  const challenged = secureLoginPage({ challenge: true });
  assert.deepEqual(await autoLoginTmallWithWindowsDpapiCredential(
    challenged.page,
    "tmall-yijiu",
    async () => ({ username: "vault-account", password: "vault-password" }),
    async () => ({ attempted: false, submitted: false, reason: "saved_credentials_missing" }),
  ), { attempted: false, submitted: false, reason: "challenge_present" });
  assert.equal(challenged.filled.clicked, false);
});

test("登录结果检查区分验证码、凭据拒绝与频率限制且不读取字段值", async () => {
  const statePage = {
    frames: () => [{
      evaluate: async (callback: unknown) => {
        const source = String(callback);
        if (source.includes("credentialRejected")) {
          return { credentialRejected: true, temporarilyLocked: false };
        }
        return {
          formFound: true,
          challengePresent: true,
          savedCredentialsReady: false,
          submitted: false,
          controlCount: 0,
          passwordModeControlCount: 0,
          passwordModeSwitched: false,
        };
      },
    } as unknown as Frame, {
      evaluate: async (callback: unknown) => {
        const source = String(callback);
        if (source.includes("credentialRejected")) {
          return { credentialRejected: false, temporarilyLocked: true };
        }
        return {
          formFound: false,
          challengePresent: false,
          savedCredentialsReady: false,
          submitted: false,
          controlCount: 0,
          passwordModeControlCount: 0,
          passwordModeSwitched: false,
        };
      },
    } as unknown as Frame],
  } as Pick<Page, "frames">;
  assert.deepEqual(await inspectTmallLoginPageState(statePage), {
    challengePresent: true,
    credentialRejected: true,
    temporarilyLocked: true,
  });
  const source = await readFile(new URL("../tools/tmall-saved-login.ts", import.meta.url), "utf8");
  assert.doesNotMatch(source, /(?:account|password)\.value\b/);
});

test("n8n 的 A 节点先完成受控登录预检再生成目标日计划", async () => {
  const source = await readFile(new URL("../tools/tmall-sycm-cookie-pipeline.ts", import.meta.url), "utf8");
  const planBranch = source.slice(source.indexOf('request.url === "/plan"'), source.indexOf('request.url === "/fetch"'));
  assert.ok(planBranch.indexOf("ensureTmallStoreAuthenticatedSession") < planBranch.indexOf("planCommand"));
  assert.doesNotMatch(planBranch, /username|password|cookie\s*:/i);
});

test("DPAPI 凭据不通过 n8n、环境变量、命令参数或日志传递", async () => {
  const credentialSource = await readFile(new URL("../tools/tmall-secure-credential.ts", import.meta.url), "utf8");
  const vaultSource = await readFile(new URL("../tools/tmall-credential-vault.ps1", import.meta.url), "utf8");
  assert.doesNotMatch(credentialSource, /process\.env|console\.|--(?:username|password)|TMALL_(?:USERNAME|PASSWORD)/i);
  assert.doesNotMatch(vaultSource, /Write-(?:Host|Output).*password|ConvertTo-SecureString[^\n]+-Key\b/i);
  assert.match(vaultSource, /ProtectedData.*Protect/);
  assert.match(vaultSource, /DataProtectionScope.*CurrentUser/);
  assert.match(vaultSource, /Console\]::OutputEncoding.*utf8NoBom/);
  assert.match(vaultSource, /ZeroFreeBSTR/);
  assert.match(vaultSource, /config\\tmall-store-accounts\.json/);
  assert.match(vaultSource, /storeKey -ceq \$StoreKey/);
  assert.match(vaultSource, /windows_dpapi_credentials/);
});
