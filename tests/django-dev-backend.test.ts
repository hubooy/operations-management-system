import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

const script = path.resolve("tools/django-dev-backend.mjs");
const source = readFileSync(script, "utf8");
const DOMAINS = ["SALES", "FINANCE", "NETSHOP", "MARKET", "PRODUCTS", "INVENTORY", "WORKFLOW", "CUSTOMER_SERVICE"];

function runLauncher(args: string[], environment: Record<string, string>) {
  return spawnSync(process.execPath, [script, ...args], {
    encoding: "utf8",
    env: { ...process.env, ...environment },
  });
}

function withTemporaryRoot<T>(callback: (root: string) => T): T {
  const root = mkdtempSync(path.join(tmpdir(), "teruisi-dev-backend-"));
  try {
    return callback(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test("dev backend launcher only ever runs the development role and refuses production", () => {
  assert.match(source, /TERUISI_DJANGO_ENVIRONMENT=development/);
  assert.match(source, /TERUISI_DJANGO_PROCESS_ROLE=development/);
  assert.match(source, /不能声明 production/);
  assert.doesNotMatch(source, /TERUISI_DJANGO_[A-Z]+_AUTHORITY_EPOCH=/);
  assert.doesNotMatch(source, /TERUISI_DJANGO_[A-Z]+_CUTOVER_ID=/);
  assert.match(source, /"-m", "waitress"/);
  assert.match(source, /"teruisi_backend\.wsgi:application"/);
});

test("print-dev-vars emits every Django domain pair with distinct reader/writer origins", () => {
  withTemporaryRoot((root) => {
    const result = runLauncher(["print-dev-vars", "--reader-port", "18001", "--writer-port", "18002"], {
      TERUISI_DEV_BACKEND_RUNTIME_ROOT: root,
      TERUISI_DEV_BACKEND_DEV_VARS_PATH: path.join(root, "dev.vars"),
    });
    assert.equal(result.status, 0, result.stderr);
    const output = result.stdout;
    assert.match(output, /^# >>> teruisi-django-dev-backend >>>/m);
    assert.match(output, /^# <<< teruisi-django-dev-backend <<<$/m);
    const secret = /^TERUISI_DJANGO_INTERNAL_SECRET=(.+)$/m.exec(output)?.[1] ?? "";
    assert.ok(Buffer.byteLength(secret, "utf8") >= 32, "internal secret must satisfy the Worker's 32-byte minimum");
    for (const domain of DOMAINS) {
      assert.match(output, new RegExp(`^TERUISI_DJANGO_${domain}_READER_BASE_URL=http://127\\.0\\.0\\.1:18001$`, "m"));
    assert.match(output, new RegExp(`^TERUISI_DJANGO_${domain}_WRITER_BASE_URL=http://127\\.0\\.0\\.1:18002$`, "m"));
    }
    assert.match(output, /^TERUISI_DJANGO_BI_READER_BASE_URL=http:\/\/127\.0\.0\.1:18001$/m);
    assert.doesNotMatch(output, /TERUISI_DJANGO_BI_WRITER_BASE_URL/);
    assert.match(output, /^TERUISI_DJANGO_BI_READER_BASE_URL=http:\/\/127\.0\.0\.1:18001$/m);
    assert.doesNotMatch(output, /TERUISI_DJANGO_BI_WRITER_BASE_URL/);
    assert.match(output, /^TERUISI_DJANGO_FINANCE_MODE=django$/m);
    assert.match(output, /^TERUISI_DJANGO_WORKFLOW_MODE=django$/m);
    assert.match(output, /^TERUISI_DJANGO_CUSTOMER_SERVICE_MODE=django$/m);
    const generatedEnvironment = readFileSync(path.join(root, "backend.env"), "utf8");
    assert.match(generatedEnvironment, new RegExp(`TERUISI_DJANGO_INTERNAL_SECRET=${secret}`));
    assert.match(generatedEnvironment, /DJANGO_SECRET_KEY=[0-9a-f]{96}/);
  });
});

test("reader and writer ports must differ because the Worker rejects a shared origin", () => {
  withTemporaryRoot((root) => {
    const result = runLauncher(["print-dev-vars", "--reader-port", "18001", "--writer-port", "18001"], {
      TERUISI_DEV_BACKEND_RUNTIME_ROOT: root,
      TERUISI_DEV_BACKEND_DEV_VARS_PATH: path.join(root, "dev.vars"),
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /读端口与写端口必须不同/);
  });
});

test("sync-dev-vars only touches its managed block and is idempotent", () => {
  withTemporaryRoot((root) => {
    const devVars = path.join(root, "dev.vars");
    writeFileSync(devVars, "AI_SECRET_ENCRYPTION_KEY=keep-me\nTERUISI_LOCAL_DIRECT_ACCESS=true\n");
    const environment = {
      TERUISI_DEV_BACKEND_RUNTIME_ROOT: root,
      TERUISI_DEV_BACKEND_DEV_VARS_PATH: devVars,
    };
    const first = runLauncher(["sync-dev-vars"], environment);
    assert.equal(first.status, 0, first.stderr);
    assert.match(first.stdout, /已写入 \.dev\.vars 受管块/);
    const afterFirst = readFileSync(devVars, "utf8");
    assert.match(afterFirst, /^AI_SECRET_ENCRYPTION_KEY=keep-me$/m);
    assert.match(afterFirst, /^TERUISI_LOCAL_DIRECT_ACCESS=true$/m);
    assert.match(afterFirst, /^TERUISI_DJANGO_INVENTORY_READER_BASE_URL=http:\/\/127\.0\.0\.1:8001$/m);
    assert.equal((afterFirst.match(/teruisi-django-dev-backend >>>/g) ?? []).length, 1);

    const second = runLauncher(["sync-dev-vars"], environment);
    assert.equal(second.status, 0, second.stderr);
    assert.match(second.stdout, /已是最新/);
    assert.equal(readFileSync(devVars, "utf8"), afterFirst);

    const moved = runLauncher(["sync-dev-vars", "--reader-port", "18001", "--writer-port", "18002"], environment);
    assert.equal(moved.status, 0, moved.stderr);
    const afterMove = readFileSync(devVars, "utf8");
    assert.match(afterMove, /已更新/.test(moved.stdout) ? /18001/ : /18001/);
    assert.equal((afterMove.match(/teruisi-django-dev-backend >>>/g) ?? []).length, 1);
    assert.match(afterMove, /^AI_SECRET_ENCRYPTION_KEY=keep-me$/m);
    assert.doesNotMatch(afterMove, /127\.0\.0\.1:8001/);
  });
});
