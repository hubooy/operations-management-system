#!/usr/bin/env node
// 本机开发用 Django 后端启动器（macOS / Linux / Windows 开发机通用）。
//
// 生产 Windows 主机的 Django/PostgreSQL 栈由 tools/django-local-service.ps1 及各域
// 受控脚本管理；本脚本只面向"没有 PostgreSQL、只想让前端页面能跑起来"的开发机：
//   - 在 .runtime/django-dev/ 下创建独立 venv 并安装 backend/requirements.txt；
//   - 生成开发用 backend.env（随机 DJANGO_SECRET_KEY 与 TERUISI_DJANGO_INTERNAL_SECRET）；
//   - 用 SQLite（backend 默认 .runtime/django/teruisi.sqlite3）执行 migrate；
//   - 以 development 进程角色启动两个 Waitress 进程：读端口与写端口（默认 8001/8002）。
//     development 角色同时挂载每个域的 reader/writer 路由，Worker 只要求同一域的
//     读写 base URL 属于不同 origin，所以两个进程即可覆盖销售、财务、网店、市场、
//     商品经营、库存、运营事务和客服八个读写域，以及只读 BI 聚合层；
//   - 把 Worker 侧需要的 TERUISI_DJANGO_* 变量写入 .dev.vars 的受管块，缺少这些变量时
//     页面会提示"Django xx 服务配置不完整"。
//
// 用法：node tools/django-dev-backend.mjs <start|stop|restart|status|migrate|sync-dev-vars|print-dev-vars|logs>
//   可选参数：--reader-port 8001 --writer-port 8002
//   环境变量：TERUISI_DEV_PYTHON 指定 Python 解释器；TERUISI_DEV_BACKEND_RUNTIME_ROOT 覆盖运行目录（测试用）。

import { spawn, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import net from "node:net";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDirectory, "..");
const backendRoot = path.join(repoRoot, "backend");
const runtimeRoot = process.env.TERUISI_DEV_BACKEND_RUNTIME_ROOT
  ? path.resolve(process.env.TERUISI_DEV_BACKEND_RUNTIME_ROOT)
  : path.join(repoRoot, ".runtime", "django-dev");
const venvRoot = path.join(runtimeRoot, "venv");
const logRoot = path.join(runtimeRoot, "logs");
const envFilePath = path.join(runtimeRoot, "backend.env");
const requirementsPath = path.join(backendRoot, "requirements.txt");
const requirementsStampPath = path.join(venvRoot, ".requirements.stamp");
// 测试可用 TERUISI_DEV_BACKEND_DEV_VARS_PATH 指向临时文件，避免改动真实 .dev.vars。
const devVarsPath = process.env.TERUISI_DEV_BACKEND_DEV_VARS_PATH
  ? path.resolve(process.env.TERUISI_DEV_BACKEND_DEV_VARS_PATH)
  : path.join(repoRoot, ".dev.vars");
const isWindows = process.platform === "win32";

const DEV_VARS_BEGIN = "# >>> teruisi-django-dev-backend >>> 由 tools/django-dev-backend.mjs 维护，请勿手工编辑此块";
const DEV_VARS_END = "# <<< teruisi-django-dev-backend <<<";
const DOMAINS = ["SALES", "FINANCE", "NETSHOP", "MARKET", "PRODUCTS", "INVENTORY", "WORKFLOW", "CUSTOMER_SERVICE", "ERP", "ACCESS_CONTROL"];
const READ_ONLY_DOMAINS = ["BI"];
const MODE_FLAGS = { FINANCE: "TERUISI_DJANGO_FINANCE_MODE", WORKFLOW: "TERUISI_DJANGO_WORKFLOW_MODE", CUSTOMER_SERVICE: "TERUISI_DJANGO_CUSTOMER_SERVICE_MODE", ERP: "TERUISI_DJANGO_ERP_MODE" };
const WRITER_MAX_BODY_BYTES = 67_108_864;
const READER_MAX_BODY_BYTES = 4_194_304;
const MAX_HEADER_BYTES = 32_768;
const LIVE_TIMEOUT_MS = 45_000;
const STOP_TIMEOUT_MS = 15_000;

function usage() {
  return [
    "用法：node tools/django-dev-backend.mjs <命令> [--reader-port 8001] [--writer-port 8002]",
    "命令：",
    "  start          创建 venv、安装依赖、生成 backend.env、迁移 SQLite、启动读/写两个开发进程并同步 .dev.vars",
    "  stop           停止本脚本启动的两个进程",
    "  restart        stop + start",
    "  status         显示进程、端口、存活探针和 .dev.vars 同步状态",
    "  migrate        只执行 manage.py migrate",
    "  sync-dev-vars  只把 Worker 侧需要的 TERUISI_DJANGO_* 变量写入 .dev.vars 受管块",
    "  print-dev-vars 打印将写入 .dev.vars 的受管块，不改文件",
    "  logs           打印最近的进程日志",
  ].join("\n");
}

function parseArguments(argv) {
  const options = { command: "", readerPort: 8001, writerPort: 8002 };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--reader-port" || argument === "--writer-port") {
      const raw = argv[index + 1];
      const port = Number.parseInt(raw ?? "", 10);
      if (!Number.isInteger(port) || port < 1024 || port > 65_535) {
        throw new Error(`${argument} 需要 1024-65535 的端口号`);
      }
      options[argument === "--reader-port" ? "readerPort" : "writerPort"] = port;
      index += 1;
    } else if (argument.startsWith("--")) {
      throw new Error(`未知参数：${argument}`);
    } else if (!options.command) {
      options.command = argument;
    } else {
      throw new Error(`多余参数：${argument}`);
    }
  }
  if (options.readerPort === options.writerPort) {
    throw new Error("读端口与写端口必须不同：Worker 要求同一域的读写 base URL 属于不同 origin");
  }
  return options;
}

function log(message) {
  process.stdout.write(`${message}\n`);
}

function ensureDirectory(directory) {
  mkdirSync(directory, { recursive: true });
}

function sha256Hex(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function venvPython() {
  return isWindows
    ? path.join(venvRoot, "Scripts", "python.exe")
    : path.join(venvRoot, "bin", "python");
}

function parsePythonVersion(output) {
  const match = /Python (\d+)\.(\d+)\.(\d+)/.exec(output ?? "");
  if (!match) return null;
  return { major: Number(match[1]), minor: Number(match[2]), patch: Number(match[3]) };
}

function findBasePython() {
  const candidates = [];
  if (process.env.TERUISI_DEV_PYTHON) candidates.push([process.env.TERUISI_DEV_PYTHON, []]);
  for (const name of ["python3.13", "python3.12", "python3.14", "python3", "python"]) candidates.push([name, []]);
  if (isWindows) candidates.push(["py", ["-3"]]);
  for (const [command, prefix] of candidates) {
    const probe = spawnSync(command, [...prefix, "--version"], { encoding: "utf8", windowsHide: true });
    if (probe.error || probe.status !== 0) continue;
    const version = parsePythonVersion(`${probe.stdout}${probe.stderr}`);
    if (!version) continue;
    if (version.major === 3 && version.minor >= 12) {
      return { command, prefix, version: `${version.major}.${version.minor}.${version.patch}` };
    }
  }
  throw new Error("未找到 Python 3.12+；请安装后重试，或用 TERUISI_DEV_PYTHON 指定解释器路径");
}

function runChecked(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    windowsHide: true,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${options.label ?? command} 失败（退出码 ${result.status}）`);
  }
}

async function ensureVenv() {
  ensureDirectory(runtimeRoot);
  const python = venvPython();
  if (!existsSync(python)) {
    const base = findBasePython();
    log(`创建 venv：${venvRoot}（Python ${base.version}）`);
    runChecked(base.command, [...base.prefix, "-m", "venv", venvRoot], { label: "创建 venv" });
  }
  const requirementsDigest = sha256Hex(readFileSync(requirementsPath));
  const stamp = existsSync(requirementsStampPath) ? readFileSync(requirementsStampPath, "utf8").trim() : "";
  if (stamp !== requirementsDigest) {
    log("安装 backend/requirements.txt ...");
    runChecked(python, ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", requirementsPath], {
      label: "安装 Python 依赖",
    });
    writeFileSync(requirementsStampPath, `${requirementsDigest}\n`);
  }
  return python;
}

function randomSecret(bytes = 48) {
  return randomBytes(bytes).toString("hex");
}

function parseEnvFile(content) {
  const values = {};
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) continue;
    values[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
  }
  return values;
}

function ensureEnvFile() {
  ensureDirectory(runtimeRoot);
  if (!existsSync(envFilePath)) {
    const content = [
      "# 本机开发用 Django 后端环境；由 tools/django-dev-backend.mjs 生成，可按需修改。",
      "# 不要把本文件或其中的密钥提交到 Git，也不要用于生产 Windows 主机。",
      "TERUISI_DJANGO_ENVIRONMENT=development",
      "TERUISI_DJANGO_PROCESS_ROLE=development",
      "TERUISI_DJANGO_EXPECT_READ_ONLY=false",
      "DJANGO_DEBUG=true",
      "DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost",
      "TERUISI_DJANGO_LOG_LEVEL=INFO",
      `DJANGO_SECRET_KEY=${randomSecret()}`,
      `TERUISI_DJANGO_INTERNAL_SECRET=${randomSecret()}`,
      "# 留空即使用 SQLite（backend 默认 .runtime/django/teruisi.sqlite3）；也可指向本机 PostgreSQL。",
      "TERUISI_DJANGO_DATABASE_URL=",
      "",
    ].join("\n");
    writeFileSync(envFilePath, content, { mode: 0o600 });
    log(`已生成开发环境文件：${envFilePath}`);
  }
  const values = parseEnvFile(readFileSync(envFilePath, "utf8"));
  const secret = values.TERUISI_DJANGO_INTERNAL_SECRET ?? "";
  if (Buffer.byteLength(secret, "utf8") < 32) {
    throw new Error(`${envFilePath} 中的 TERUISI_DJANGO_INTERNAL_SECRET 必须至少 32 字节`);
  }
  if ((values.TERUISI_DJANGO_ENVIRONMENT ?? "development") === "production") {
    throw new Error("本脚本只用于开发环境，backend.env 不能声明 production");
  }
  return values;
}

function processEnvironment(envValues, extra = {}) {
  const environment = { ...process.env };
  for (const key of Object.keys(environment)) {
    if (key.startsWith("TERUISI_DJANGO_") || key === "DJANGO_DEBUG" || key === "DJANGO_SECRET_KEY") {
      delete environment[key];
    }
  }
  for (const [key, value] of Object.entries(envValues)) {
    if (value !== "") environment[key] = value;
  }
  environment.PYTHONUNBUFFERED = "1";
  environment.PYTHONDONTWRITEBYTECODE = "1";
  return { ...environment, ...extra };
}

function migrate(python, envValues) {
  log("执行 manage.py migrate（SQLite 或 backend.env 指定的数据库）...");
  runChecked(python, ["manage.py", "migrate", "--no-input"], {
    cwd: backendRoot,
    env: processEnvironment(envValues),
    label: "Django migrate",
  });
}

function pidFilePath(role) {
  return path.join(runtimeRoot, `${role}.pid.json`);
}

function readPidFile(role) {
  const filePath = pidFilePath(role);
  if (!existsSync(filePath)) return null;
  try {
    const parsed = JSON.parse(readFileSync(filePath, "utf8"));
    if (!Number.isInteger(parsed.pid) || parsed.pid <= 0) return null;
    return parsed;
  } catch {
    return null;
  }
}

function isProcessAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error && error.code === "EPERM";
  }
}

function looksLikeOurProcess(pid) {
  if (isWindows) return true;
  const probe = spawnSync("ps", ["-o", "command=", "-p", String(pid)], { encoding: "utf8" });
  if (probe.status !== 0) return false;
  return /waitress|teruisi_backend\.wsgi/.test(probe.stdout);
}

function isPortListening(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port });
    const finish = (value) => {
      socket.removeAllListeners();
      socket.destroy();
      resolve(value);
    };
    socket.setTimeout(1000);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

async function probeLive(port) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2000);
    const response = await fetch(`http://127.0.0.1:${port}/health/live`, { signal: controller.signal });
    clearTimeout(timer);
    if (!response.ok) return false;
    const payload = await response.json();
    return Boolean(payload) && payload.status === "ok" && payload.service === "teruisi-django";
  } catch {
    return false;
  }
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function startRole(python, envValues, role, port) {
  const existing = readPidFile(role);
  if (existing && isProcessAlive(existing.pid)) {
    if (existing.port === port) {
      log(`${role}：已在运行（PID ${existing.pid}，端口 ${port}）`);
      return;
    }
    throw new Error(`${role} 已在 PID ${existing.pid} 上以端口 ${existing.port} 运行；请先 stop 再更换端口`);
  }
  if (await isPortListening(port)) {
    throw new Error(`端口 ${port} 已被其他进程占用，${role} 无法启动`);
  }
  ensureDirectory(logRoot);
  const stdoutPath = path.join(logRoot, `${role}.stdout.log`);
  const stderrPath = path.join(logRoot, `${role}.stderr.log`);
  const stdoutFd = openSync(stdoutPath, "a");
  const stderrFd = openSync(stderrPath, "a");
  const maxBody = role === "writer" ? WRITER_MAX_BODY_BYTES : READER_MAX_BODY_BYTES;
  const args = [
    "-m", "waitress",
    `--listen=127.0.0.1:${port}`,
    `--threads=${role === "writer" ? 4 : 6}`,
    "--connection-limit=60",
    "--channel-timeout=35",
    `--ident=teruisi-django-dev-${role}`,
    `--max-request-header-size=${MAX_HEADER_BYTES}`,
    `--max-request-body-size=${maxBody}`,
    "--no-expose-tracebacks",
    "teruisi_backend.wsgi:application",
  ];
  const child = spawn(python, args, {
    cwd: backendRoot,
    env: processEnvironment(envValues, { TERUISI_DJANGO_MAX_BODY_BYTES: String(maxBody) }),
    detached: !isWindows,
    stdio: ["ignore", stdoutFd, stderrFd],
    windowsHide: true,
  });
  closeSync(stdoutFd);
  closeSync(stderrFd);
  if (!child.pid) throw new Error(`${role} 进程未能启动`);
  child.unref();
  writeFileSync(pidFilePath(role), `${JSON.stringify({
    pid: child.pid,
    role,
    port,
    startedAt: new Date().toISOString(),
    command: [python, ...args].join(" "),
  }, null, 2)}\n`);
  const deadline = Date.now() + LIVE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!isProcessAlive(child.pid)) {
      rmSync(pidFilePath(role), { force: true });
      throw new Error(`${role} 进程已退出，请查看 ${stderrPath}`);
    }
    if (await probeLive(port)) {
      log(`${role}：已就绪 http://127.0.0.1:${port}（PID ${child.pid}）`);
      return;
    }
    await sleep(500);
  }
  throw new Error(`${role} 在 ${LIVE_TIMEOUT_MS / 1000} 秒内未通过 /health/live 探针，请查看 ${stderrPath}`);
}

async function stopRole(role) {
  const record = readPidFile(role);
  if (!record) {
    log(`${role}：未记录进程`);
    return;
  }
  if (!isProcessAlive(record.pid)) {
    rmSync(pidFilePath(role), { force: true });
    log(`${role}：进程 ${record.pid} 已不存在，清理记录`);
    return;
  }
  if (!looksLikeOurProcess(record.pid)) {
    rmSync(pidFilePath(role), { force: true });
    throw new Error(`${role}：PID ${record.pid} 不是本脚本启动的 Waitress 进程（可能已被复用），已清理记录但未终止该进程`);
  }
  process.kill(record.pid, isWindows ? undefined : "SIGTERM");
  const deadline = Date.now() + STOP_TIMEOUT_MS;
  while (Date.now() < deadline && isProcessAlive(record.pid)) {
    await sleep(250);
  }
  if (isProcessAlive(record.pid)) {
    if (!isWindows) process.kill(record.pid, "SIGKILL");
    await sleep(500);
  }
  if (isProcessAlive(record.pid)) {
    throw new Error(`${role}：无法终止 PID ${record.pid}`);
  }
  rmSync(pidFilePath(role), { force: true });
  log(`${role}：已停止（PID ${record.pid}）`);
}

function devVarsBlock(envValues, readerPort, writerPort) {
  const lines = [DEV_VARS_BEGIN, `TERUISI_DJANGO_INTERNAL_SECRET=${envValues.TERUISI_DJANGO_INTERNAL_SECRET}`];
  for (const domain of DOMAINS) {
    if (MODE_FLAGS[domain]) lines.push(`${MODE_FLAGS[domain]}=django`);
    lines.push(`TERUISI_DJANGO_${domain}_READER_BASE_URL=http://127.0.0.1:${readerPort}`);
    lines.push(`TERUISI_DJANGO_${domain}_WRITER_BASE_URL=http://127.0.0.1:${writerPort}`);
  }
  for (const domain of READ_ONLY_DOMAINS) {
    lines.push(`TERUISI_DJANGO_${domain}_READER_BASE_URL=http://127.0.0.1:${readerPort}`);
  }
  lines.push(DEV_VARS_END);
  return lines.join("\n");
}

function splitDevVars(content) {
  const begin = content.indexOf(DEV_VARS_BEGIN);
  const end = content.indexOf(DEV_VARS_END);
  if (begin === -1 && end === -1) return { before: content, block: null, after: "" };
  if (begin === -1 || end === -1 || end < begin) {
    throw new Error(".dev.vars 中的 teruisi-django-dev-backend 受管块标记不完整，请手工修复后重试");
  }
  const blockEnd = end + DEV_VARS_END.length;
  return {
    before: content.slice(0, begin),
    block: content.slice(begin, blockEnd),
    after: content.slice(blockEnd),
  };
}

function syncDevVars(envValues, readerPort, writerPort) {
  const block = devVarsBlock(envValues, readerPort, writerPort);
  const existing = existsSync(devVarsPath) ? readFileSync(devVarsPath, "utf8") : "";
  const parts = splitDevVars(existing);
  if (parts.block === block) {
    log(`.dev.vars 受管块已是最新：${devVarsPath}`);
    return false;
  }
  let before = parts.before;
  if (before.length > 0 && !before.endsWith("\n")) before += "\n";
  if (parts.block === null && before.length > 0 && !before.endsWith("\n\n")) before += "\n";
  let after = parts.after;
  if (!after.startsWith("\n")) after = `\n${after}`;
  writeFileSync(devVarsPath, `${before}${block}${after}`, { mode: 0o600 });
  log(`${parts.block === null ? "已写入" : "已更新"} .dev.vars 受管块：${devVarsPath}`);
  log("提示：Vite/Wrangler 只在启动时读取 .dev.vars，请重启 `npx vinext dev`。");
  return true;
}

function devVarsSynced(envValues, readerPort, writerPort) {
  if (!existsSync(devVarsPath)) return false;
  const parts = splitDevVars(readFileSync(devVarsPath, "utf8"));
  return parts.block === devVarsBlock(envValues, readerPort, writerPort);
}

async function showStatus(options) {
  const envValues = existsSync(envFilePath) ? parseEnvFile(readFileSync(envFilePath, "utf8")) : null;
  log(`运行目录：${runtimeRoot}`);
  log(`venv：${existsSync(venvPython()) ? "已创建" : "缺失"}`);
  log(`backend.env：${envValues ? "存在" : "缺失"}`);
  for (const [role, port] of [["reader", options.readerPort], ["writer", options.writerPort]]) {
    const record = readPidFile(role);
    const alive = record ? isProcessAlive(record.pid) : false;
    const effectivePort = record?.port ?? port;
    const listening = await isPortListening(effectivePort);
    const live = listening ? await probeLive(effectivePort) : false;
    log(`${role}：${alive ? `运行中 PID ${record.pid}` : "未运行"}，端口 ${effectivePort} ${listening ? "监听中" : "未监听"}，/health/live ${live ? "ok" : "不可用"}`);
  }
  if (envValues && Buffer.byteLength(envValues.TERUISI_DJANGO_INTERNAL_SECRET ?? "", "utf8") >= 32) {
    log(`.dev.vars 受管块：${devVarsSynced(envValues, options.readerPort, options.writerPort) ? "已同步" : "未同步（运行 sync-dev-vars）"}`);
  } else {
    log(".dev.vars 受管块：无法判断（backend.env 缺失或密钥无效）");
  }
}

function showLogs() {
  for (const role of ["reader", "writer"]) {
    for (const stream of ["stdout", "stderr"]) {
      const filePath = path.join(logRoot, `${role}.${stream}.log`);
      if (!existsSync(filePath)) continue;
      const size = statSync(filePath).size;
      const content = readFileSync(filePath, "utf8");
      const tail = content.length > 4000 ? content.slice(-4000) : content;
      log(`----- ${role} ${stream} (${size} bytes, 最近 ${tail.length} 字符) -----`);
      if (tail.trim()) log(tail.trimEnd());
    }
  }
}

async function start(options) {
  const python = await ensureVenv();
  const envValues = ensureEnvFile();
  migrate(python, envValues);
  await startRole(python, envValues, "reader", options.readerPort);
  await startRole(python, envValues, "writer", options.writerPort);
  syncDevVars(envValues, options.readerPort, options.writerPort);
  log("本机 Django 开发后端已就绪。开发角色不启用生产 authority 门禁，销售导入等仅在 sales_writer 角色开放的写路径在开发模式下不可用。");
}

async function stop() {
  await stopRole("writer");
  await stopRole("reader");
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  switch (options.command) {
    case "start":
      await start(options);
      break;
    case "stop":
      await stop();
      break;
    case "restart":
      await stop();
      await start(options);
      break;
    case "status":
      await showStatus(options);
      break;
    case "migrate": {
      const python = await ensureVenv();
      migrate(python, ensureEnvFile());
      break;
    }
    case "sync-dev-vars":
      syncDevVars(ensureEnvFile(), options.readerPort, options.writerPort);
      break;
    case "print-dev-vars":
      log(devVarsBlock(ensureEnvFile(), options.readerPort, options.writerPort));
      break;
    case "logs":
      showLogs();
      break;
    case "":
    case "help":
    case "--help":
      log(usage());
      break;
    default:
      throw new Error(`未知命令：${options.command}\n${usage()}`);
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
