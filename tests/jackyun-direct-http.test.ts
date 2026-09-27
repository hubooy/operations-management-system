import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { JackyunHttpSession, signJackyunForm, type JackyunSession } from "../lib/jackyun/direct-http";

const session: JackyunSession = { accessToken: "fixture-access", refreshToken: "fixture-refresh", appkey: "fixture-app", signingSecret: "fixture-secret" };
const json = (body: object, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const expired = () => json({ subCode: "0190210003", result: {} }, 401);

test("website signature matches ASCII sorting, JSON, and transmitted-but-unsigned empty values", () => {
  const form = signJackyunForm({ z: "中文 &+", A: { ids: [1, 2] }, empty: "", space: " \t", nil: null, unset: undefined, zero: 0, flag: false }, session, 123);
  const canonical = 'A{"ids":[1,2]}access_tokenBearer fixture-accessappkeyfixture-appflagfalsenilnulltimestamp123z中文 &+zero0';
  assert.equal(form.get("sign"), createHash("md5").update("fixture-secret" + canonical + "fixture-secret").digest("hex").toUpperCase());
  assert.equal(form.get("unset"), "undefined");
  assert.equal(form.get("space"), " \t");
  assert.equal(new URLSearchParams(form.toString()).get("z"), "中文 &+");
  assert.throws(() => signJackyunForm({ access_token: "collision" }, session, 123), /PARAMETER/);
  const cyclic: Record<string, unknown> = {}; cyclic.self = cyclic;
  assert.throws(() => signJackyunForm({ cyclic }, session, 123), /PARAMETER/);
});

test("concurrent expiry has one refresh and publishes the complete token pair before replay", async () => {
  let refreshCount = 0, publishes = 0, requests = 0;
  const client = new JackyunHttpSession({ ...session, cookie: "route=fixture; token=fixture-access", userAgent: "fixture-browser", ati: "fixture-ati" }, { allowRefresh: true,
    publishSession: async (previous, next) => {
      assert.equal(previous.refreshToken, "fixture-refresh");
      assert.equal(next.accessToken, "next-access"); assert.equal(next.refreshToken, "next-refresh"); publishes++;
      assert.equal(next.cookie, "route=fixture; token=next-access");
    }, fetch: (async (url, init) => {
      assert.equal(init?.redirect, "error");
      assert.equal(new Headers(init?.headers).get("User-Agent"), "fixture-browser");
      assert.equal(new Headers(init?.headers).get("ati"), "fixture-ati");
      if (String(url).endsWith("/auth/refresh")) { refreshCount++; return json({ access_token: "next-access", refresh_token: "next-refresh" }); }
      requests++;
      if (new URL(String(url)).searchParams.get("access_token") === "Bearer fixture-access") return expired();
      assert.equal(publishes, 1);
      assert.equal(new Headers(init?.headers).get("Cookie"), "route=fixture; token=next-access");
      return json({ code: 200, result: { data: [], pageInfo: { total: 0 } } });
    }) as typeof fetch,
  });
  const results = await Promise.all([client.request("tasks", { pageIndex: 0 }), client.request("tasks", { pageIndex: 0 })]);
  assert.equal(results.length, 2); assert.equal(refreshCount, 1); assert.equal(publishes, 1); assert.equal(requests, 4);
  assert.equal(JSON.stringify(client), "{}");
});

test("uncertain refresh or failed token publication poisons the owner without a second rotation", async t => {
  for (const failure of ["network", "partial", "publish"]) await t.test(failure, async () => {
    let calls = 0;
    const client = new JackyunHttpSession(session, { allowRefresh: true,
      publishSession: async () => { throw new Error("private CAS details"); },
      fetch: (async url => {
        calls++;
        if (!String(url).endsWith("/auth/refresh")) return expired();
        if (failure === "network") throw new Error("fixture-refresh secret URL");
        return json(failure === "partial" ? { access_token: "next" } : { access_token: "next", refresh_token: "next" });
      }) as typeof fetch,
    });
    await assert.rejects(client.request("tasks", {}), /^Error: JACKYUN_SESSION_RELOGIN_REQUIRED$/);
    await assert.rejects(client.request("tasks", {}), /RELOGIN_REQUIRED/);
    assert.equal(calls, 2);
  });
});

test("only read-only transport failures retry once; exports, risk challenges and invalid responses never replay", async t => {
  for (const mode of ["expired-export", "risk", "risk-with-success-code", "network-export", "network-tasks", "html", "oversize", "refresh-disabled"]) await t.test(mode, async () => {
    let calls = 0;
    const client = new JackyunHttpSession(session, { allowRefresh: mode !== "refresh-disabled", publishSession: async () => assert.fail(),
      fetch: (async () => {
        calls++;
        if (mode.startsWith("network-")) throw new Error("signed URL and fixture-access");
        if (mode === "html") return new Response("login form");
        if (mode === "oversize") return json({ data: "x".repeat(2 * 1024 * 1024) });
        if (mode === "risk") return json({ subCode: "0190210006", result: {} }, 401);
        if (mode === "risk-with-success-code") return json({ code: 200, subCode: "0031117002", result: { data: { verifyId: "fixture" } } });
        return expired();
      }) as typeof fetch,
    });
    await assert.rejects(client.request(mode === "expired-export" || mode === "network-export" ? "submitExport" : "tasks", {}), e =>
      e instanceof Error && /^JACKYUN_(HTTP_|EXPORT_)/.test(e.message) && !e.message.includes("fixture"));
    assert.equal(calls, mode === "network-tasks" ? 2 : 1);
  });
});
