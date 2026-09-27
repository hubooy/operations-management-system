import path from "node:path";
import { pathToFileURL } from "node:url";

import { getTmallStore } from "../lib/netshop/tmall-store-registry";
import {
  hasPendingTmallProductMasterAudit,
  migrateTmallProductMasterCadenceInterval,
} from "./tmall-product-master-cadence";

function option(argv: string[], name: string) {
  const index = argv.indexOf(name);
  if (index < 0 || index + 1 >= argv.length || argv[index + 1]!.startsWith("--")) {
    throw new Error(`缺少参数 ${name}`);
  }
  return argv[index + 1]!;
}

export async function migrateConfiguredCadence(argv: string[]) {
  const allowed = new Set([
    "--action",
    "--store-key",
    "--expected-previous-interval-days",
    "--expected-last-success-date",
    "--confirm",
  ]);
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    if (!key || !allowed.has(key) || index + 1 >= argv.length) throw new Error(`参数无效：${key ?? ""}`);
  }
  if (option(argv, "--action") !== "migrate-interval") throw new Error("只支持 migrate-interval");
  const storeKey = option(argv, "--store-key");
  const previousIntervalDays = Number(option(argv, "--expected-previous-interval-days"));
  const expectedLastSuccessDate = option(argv, "--expected-last-success-date");
  const store = await getTmallStore(storeKey);
  const nextIntervalDays = store.productMasterCadence?.intervalDays;
  const confirmation = `${storeKey}:${previousIntervalDays}->${nextIntervalDays}:${expectedLastSuccessDate}`;
  if (option(argv, "--confirm") !== confirmation) throw new Error("天猫货品节奏迁移确认值不匹配");
  if (await hasPendingTmallProductMasterAudit(storeKey)) {
    throw new Error("天猫货品仍有未决活动清单，拒绝迁移节奏");
  }
  return migrateTmallProductMasterCadenceInterval({
    store,
    expectedPreviousIntervalDays: previousIntervalDays,
    expectedLastSuccessDate,
  });
}

async function main() {
  const result = await migrateConfiguredCadence(process.argv.slice(2));
  process.stdout.write(`${JSON.stringify({ ok: true, state: result })}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  void main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
