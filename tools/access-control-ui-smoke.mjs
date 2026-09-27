// Read-only deployed UI smoke. Always creates a fresh, headless browser context.
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const browser = await chromium.launch({
  executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  headless: true,
});
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === "http://127.0.0.1:3000") await route.continue();
    else await route.abort();
  });
  const page = await context.newPage();
  const errors = [];
  const responses = new Map();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/access-control/")) responses.set(url.pathname, response.status());
  });
  await page.goto("http://127.0.0.1:3000/?module=settings&view=permissions", { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.getByRole("heading", { name: "系统用户", exact: true }).waitFor({ timeout: 45_000 });
  await page.getByRole("heading", { name: "权限审计", exact: true }).waitFor();
  assert.equal(await page.locator(".access-control-summary article").count(), 4);
  assert.equal(await page.locator(".access-control-user-list button").count(), 1);
  await page.locator(".access-control-user-list button").first().click();
  await page.getByRole("heading", { name: "编辑用户权限", exact: true }).waitFor();
  await page.getByRole("button", { name: "新增用户", exact: true }).click();
  await page.getByRole("heading", { name: "新增系统用户", exact: true }).waitFor();
  // No submit: the formal production account and audit revision remain unchanged.
  assert.deepEqual(errors, []);
  for (const path of ["/api/access-control/users", "/api/access-control/roles", "/api/access-control/audits"]) assert.equal(responses.get(path), 200, path);
  console.log(JSON.stringify({ status: "passed", url: page.url(), roleCards: 4, userCount: 1, editAndCreateForms: "rendered", writes: 0, pageErrors: 0, apiStatuses: Object.fromEntries(responses) }));
} finally {
  await browser.close();
}
