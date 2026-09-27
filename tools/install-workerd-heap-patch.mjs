import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Backport only config forwarding; retain the currently validated runtime.
// Upstream: cloudflare/workers-sdk#14702. Do not patch a running release.
export const originalMiniflareSha256 = "9584409d464f6c21d720b20da3c5dc6e5bdab0393f9cb19b36cb960beaf0958b";
const anchor = '      autogates: process.env.MINIFLARE_WORKERD_AUTOGATES ? process.env.MINIFLARE_WORKERD_AUTOGATES.split(" ") : []';
const replacement = '      v8Flags: process.env.TERUISI_WORKERD_HEAP_MB === "3072" ? ["--max-old-space-size=3072"] : [],\n' + anchor;
const hash = (value) => createHash("sha256").update(value).digest("hex");

export function patchMiniflareHeap(source) {
  const restored = source.replace(replacement, anchor);
  if (hash(restored) !== originalMiniflareSha256) {
    throw new Error("Miniflare heap patch refuses an unknown dependency digest");
  }
  if (restored.split(anchor).length !== 2) throw new Error("Miniflare heap patch anchor is not unique");
  return restored.replace(anchor, replacement);
}

export async function installWorkerdHeapPatch(root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")) {
  const require = createRequire(path.join(root, "package.json"));
  const wranglerRequire = createRequire(require.resolve("wrangler/package.json"));
  const entry = wranglerRequire.resolve("miniflare");
  const source = await readFile(entry, "utf8");
  const patched = patchMiniflareHeap(source);
  if (patched !== source) await writeFile(entry, patched);
  return { status: patched === source ? "already_patched" : "patched", sha256: hash(patched) };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.log(JSON.stringify(await installWorkerdHeapPatch()));
}
