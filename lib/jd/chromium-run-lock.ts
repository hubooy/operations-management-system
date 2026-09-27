import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { withJackyunRunLock } from "../jackyun/run-lock";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

export function defaultJdChromiumRunLockDirectory(root = projectRoot) {
  return path.join(root, ".runtime", "jd-chromium-global.lock");
}

export async function withJdChromiumRunLock<T>(
  purpose: string,
  operation: () => Promise<T>,
  lockDirectory = defaultJdChromiumRunLockDirectory(),
) {
  return withJackyunRunLock({
    runId: `jd-chromium-${process.pid}-${randomUUID()}`,
    purpose,
    lockDirectory,
  }, operation);
}
