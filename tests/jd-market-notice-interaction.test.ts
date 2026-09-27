import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";
import { chromium } from "playwright-core";
import { dismissRankingNotice } from "../tools/jd-market-ranking-daily";

const chromePath = process.env.CHROME_PATH ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";

test("JD ranking only dismisses a unique ordinary announcement", { skip: !existsSync(chromePath) }, async (t) => {
  const browser = await chromium.launch({ executablePath: chromePath, headless: true });
  try {
    const page = await browser.newPage();
    const notice = '<div class="jd-modal-wrap"><img alt="公告图片"><span class="close-modal" onclick="this.parentElement.remove()">关闭</span></div>';
    await t.test("closes the observed notice and ignores its hidden alternative close", async () => {
      await page.setContent(notice.replace('<img', '<button aria-label="Close" style="display:none">关闭</button><img'));
      await dismissRankingNotice(page, 50);
      assert.equal(await page.locator(".jd-modal-wrap").count(), 0);
    });
    await t.test("does not touch a security verification dialog", async () => {
      await page.setContent('<div class="jd-modal-wrap"><img alt="安全验证"><span class="close-modal" onclick="this.parentElement.remove()">关闭</span></div>');
      await dismissRankingNotice(page, 50);
      assert.equal(await page.locator(".jd-modal-wrap").count(), 1);
    });
    await t.test("rejects duplicate announcements without clicking either", async () => {
      await page.setContent(notice + notice);
      await assert.rejects(dismissRankingNotice(page, 50), /公告不唯一/);
      assert.equal(await page.locator(".jd-modal-wrap").count(), 2);
    });
    await t.test("rejects duplicate visible close controls without clicking", async () => {
      await page.setContent(notice.replace('<img', '<button aria-label="Close">关闭</button><img'));
      await assert.rejects(dismissRankingNotice(page, 50), /关闭按钮不唯一/);
      assert.equal(await page.locator(".jd-modal-wrap").count(), 1);
    });
    await t.test("does not dismiss an unrelated overlay with a close button", async () => {
      await page.setContent('<div class="jd-modal-wrap"><button aria-label="Close" onclick="this.parentElement.remove()">关闭</button></div>');
      await dismissRankingNotice(page, 50);
      assert.equal(await page.locator(".jd-modal-wrap").count(), 1);
    });
  } finally {
    await browser.close();
  }
});
