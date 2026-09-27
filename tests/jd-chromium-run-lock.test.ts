import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { withJdChromiumRunLock } from "../lib/jd/chromium-run-lock";

test("all JD Chromium stages share one exclusive owner lock", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-chromium-lock-"));
  const lockDirectory = path.join(root, "global.lock");
  let releaseFirst!: () => void;
  let markAcquired!: () => void;
  const acquired = new Promise<void>((resolve) => { markAcquired = resolve; });
  const hold = new Promise<void>((resolve) => { releaseFirst = resolve; });
  try {
    const first = withJdChromiumRunLock("product-master", async () => {
      markAcquired();
      await hold;
      return "first";
    }, lockDirectory);
    await acquired;
    await assert.rejects(
      withJdChromiumRunLock("market-ranking", async () => "second", lockDirectory),
      /已有任务正在执行/,
    );
    releaseFirst();
    assert.equal(await first, "first");
    assert.equal(await withJdChromiumRunLock("product-detail", async () => "third", lockDirectory), "third");
  } finally {
    releaseFirst?.();
    await rm(root, { recursive: true, force: true });
  }
});

test("JD child scripts use owner-validated recoverable locks instead of orphanable wx files", async () => {
  const [master, detail] = await Promise.all([
    readFile("tools/jackyun-ware-export.ts", "utf8"),
    readFile("tools/jdsz-product-detail-export.ts", "utf8"),
  ]);
  for (const source of [master, detail]) {
    assert.match(source, /withJdChromiumRunLock\([\s\S]*?\.run-lock/);
    assert.doesNotMatch(source, /open\(lockPath,\s*["']wx["']/);
  }
});
