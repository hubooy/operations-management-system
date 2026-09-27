import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  enforceDynamicCachePolicy,
  resolveDynamicCacheControl,
  type DynamicCachePolicyInput,
} from "../worker/cache-policy";

const projectRoot = new URL("../", import.meta.url);
const immutablePrivateImageCache = "private, max-age=31536000, immutable";

function policy(overrides: Partial<DynamicCachePolicyInput> = {}) {
  return resolveDynamicCacheControl({
    pathname: "/",
    method: "GET",
    status: 200,
    requestAccept: null,
    requestRsc: null,
    responseCacheControl: null,
    responseContentType: null,
    ...overrides,
  });
}

test("public headers cache hashed assets immutably and versionless root assets for a bounded period", async () => {
  const headers = await readFile(new URL("public/_headers", projectRoot), "utf8");
  assert.match(headers, /\/assets\/\*\s+Cache-Control: public, max-age=31536000, immutable/);

  for (const asset of ["favicon.svg", "og.png", "file.svg", "globe.svg", "window.svg"]) {
    const escapedAsset = asset.replace(".", "\\.");
    const rule = new RegExp(`/${escapedAsset}\\s+Cache-Control: public, max-age=86400, stale-while-revalidate=604800`);
    assert.match(headers, rule);
  }

  const versionlessRules = headers.split("\n").filter((line) => line.includes("max-age=86400"));
  assert.equal(versionlessRules.length, 5);
  assert.equal(versionlessRules.some((line) => line.includes("immutable")), false);
});

test("favicon uses the Xiao Te brand mark", async () => {
  const favicon = await readFile(new URL("public/favicon.svg", projectRoot), "utf8");
  assert.match(favicon, /fill="#293F32"/);
  assert.match(favicon, /stroke="#CCE8D9"/);
  assert.doesNotMatch(favicon, /#0C79D8|#2E9EFF|#68C4FF/i);
});

test("dynamic policy leaves fingerprinted bundles untouched", () => {
  assert.equal(policy({
    pathname: "/assets/page-deadbeef.js",
    responseCacheControl: "public, max-age=31536000, immutable",
    responseContentType: "text/javascript",
  }), null);
});

test("dynamic policy forces ordinary and failed API responses to no-store", () => {
  assert.equal(policy({
    pathname: "/api/sales/summary",
    responseCacheControl: "public, max-age=60",
    responseContentType: "application/json",
  }), "no-store");

  const hash = "a".repeat(64);
  assert.equal(policy({
    pathname: `/api/market/images/${hash}`,
    status: 404,
    responseCacheControl: immutablePrivateImageCache,
  }), "no-store");
  assert.equal(policy({
    pathname: `/api/netshop/product-images/${hash}`,
    status: 200,
    responseCacheControl: "private, max-age=31536000",
  }), "no-store");
  assert.equal(policy({
    pathname: `/api/market/images/${hash}.png`,
    status: 200,
    responseCacheControl: immutablePrivateImageCache,
  }), "no-store");
});

test("dynamic policy preserves only successful immutable private hash images", () => {
  const hash = "0123456789abcdef".repeat(4);
  for (const pathname of [
    `/api/market/images/${hash}`,
    `/api/netshop/product-images/${hash}`,
  ]) {
    assert.equal(policy({
      pathname,
      status: 200,
      responseCacheControl: immutablePrivateImageCache,
    }), null);
  }
});

test("dynamic policy makes HTML and RSC responses non-cacheable", () => {
  assert.equal(policy({
    pathname: "/",
    requestAccept: "text/html,application/xhtml+xml",
  }), "no-store, must-revalidate");
  assert.equal(policy({
    pathname: "/?module=sales",
    responseContentType: "text/x-component",
  }), "no-store, must-revalidate");
  assert.equal(policy({
    pathname: "/",
    requestRsc: "1",
  }), "no-store, must-revalidate");
});

test("response gate overwrites unsafe API caching without losing response metadata", async () => {
  const response = new Response(JSON.stringify({ ok: false }), {
    status: 410,
    statusText: "Gone",
    headers: {
      "cache-control": "public, max-age=3600",
      "content-type": "application/json",
      etag: '"attempt-1"',
      vary: "cookie",
    },
  });
  const guarded = enforceDynamicCachePolicy(
    new Request("https://operations.example/api/jackyun/session/continue", { method: "POST" }),
    response,
  );

  assert.notEqual(guarded, response);
  assert.equal(guarded.status, 410);
  assert.equal(guarded.statusText, "Gone");
  assert.equal(guarded.headers.get("cache-control"), "no-store");
  assert.equal(guarded.headers.get("etag"), '"attempt-1"');
  assert.equal(guarded.headers.get("vary"), "cookie");
  assert.deepEqual(await guarded.json(), { ok: false });
});
