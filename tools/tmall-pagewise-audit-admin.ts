import { abandonInvalidDownloadedTmallPagewiseAudit } from "./tmall-pagewise-product-master-export";

function argument(name: string) {
  const index = process.argv.indexOf(name);
  if (index < 0) return undefined;
  const value = process.argv[index + 1];
  if (!value || value.startsWith("--")) throw new Error(`${name} 缺少参数值`);
  return value;
}

async function main() {
  const action = argument("--action");
  if (action !== "abandon-invalid-downloaded") {
    throw new Error("仅支持 --action abandon-invalid-downloaded");
  }
  const storeKey = argument("--store-key");
  const reason = argument("--reason");
  if (!storeKey || !reason) throw new Error("必须提供 --store-key 与 --reason");
  if (!process.argv.includes("--confirm")) {
    throw new Error("必须在操作者明确确认后传入 --confirm");
  }
  const result = await abandonInvalidDownloadedTmallPagewiseAudit({
    storeKey,
    reason,
    operatorConfirmed: true,
  });
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`${JSON.stringify({ ok: false, error: message.replace(/[\r\n]+/g, " ").slice(0, 500) })}\n`);
  process.exitCode = 1;
});
