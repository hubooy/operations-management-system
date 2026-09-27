import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { attachHourlyRetryTarget } from "./n8n-hourly-retry-policy.mjs";

const root = new URL("../", import.meta.url);
const directHttp = process.argv.includes("--direct-http");
const apiOnly = process.argv.includes("--api-only");
if (directHttp && apiOnly || process.argv.slice(2).some(arg => !["--direct-http", "--api-only"].includes(arg))) throw new Error("Unknown workflow option");
const original = JSON.parse(await readFile(new URL("automation/n8n/jd-multi-store-daily.workflow.json", root), "utf8"));
const id = name => createHash("sha256").update(`jackyun-export-first:${name}`).digest("hex").slice(0, 32);
const cloneNode = name => {
  const node = structuredClone(original.nodes.find(item => item.name === name));
  node.id = id(name);
  return node;
};
const manual = cloneNode("手动运行"); manual.position = [-760, 0];
const claim = cloneNode("领取共享 helper"); claim.position = [-540, 0];
claim.parameters.headerParameters.parameters.find(item => item.name === "X-TERUISI-WORKFLOW-KEY").value = "jackyun";
const claimed = cloneNode("helper 领取成功？"); claimed.position = [-300, 0];
const wait = cloneNode("等待前序流程释放 helper"); wait.position = [-540, 220];
const steps = [
  [apiOnly ? "plan-api" : directHttp ? "plan-direct-http" : "plan-web-session", "A·固定采集日和销售日期", [-60, 0], 120000],
  ["export-all", apiOnly ? "B·接口校验与五表下载" : directHttp ? "B·网页校验后 HTTP 导出五表" : "B·共用登录态：顺序导出五表", [280, 0], 1800000],
  ["validate", "C·五表完整校验和导入演练", [620, 0], 1800000],
  ["import", "D·统一导入运营管理系统", [960, 0], 5400000],
  ["verify", "E·独立核验五类精确批次", [1300, 0], 300000],
];
const nodes = [manual, claim, claimed, wait, ...steps.map(([action, name, position, timeout]) => ({
  id: id(action), name, position, type: "n8n-nodes-base.httpRequest", typeVersion: 4.2,
  parameters: { method: "POST", url: `http://127.0.0.1:5791/jackyun/export-first/${action}`,
    sendHeaders: true, headerParameters: { parameters: [{ name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" }] },
    options: { timeout } },
}))];
const edge = name => ({ node: name, type: "main", index: 0 });
const connections = {
  [manual.name]: { main: [[edge(claim.name)]] },
  [claim.name]: { main: [[edge(claimed.name)]] },
  [claimed.name]: { main: [[edge(steps[0][1])], [edge(wait.name)]] },
  [wait.name]: { main: [[edge(claim.name)]] },
};
for (let index = 0; index < steps.length - 1; index++) connections[steps[index][1]] = { main: [[edge(steps[index + 1][1])]] };
for (const [name, content, position, width] of [
  ["五表操作说明", "## 先导出五张表，再导入\n导出顺序：分仓库存 → 组合装及子件 → 销售单明细账 → 库龄分析 → 货品 SKU。五表共用一次专用浏览器登录态；每表按已核验的条件查询，调用网页自身的“导出所有页”能力，按任务接口等待并下载原始 Excel。保留平台原有校验，组合装导出母件及子件；不保存 Cookie 到 n8n。销售按发货时间，本月 1 日至昨天；每月 1 日按已有业务规则处理上月整月。销售使用截图中的普通“导出”，组合装使用“导出组合装及子件”。\n库存与库龄记录实际采集日；当前查询不会标成昨天的历史快照。分仓库存继续使用公司全仓范围，导入时按现有规则过滤刷刷仓等无效行。", [280, -300], 1280],
  ["导入与运行说明", "## 导入门槛\n5 张本轮文件全部落地后，核验表头、页面总数、SHA-256、组合装母子关系和销售日期，并进行无业务写入的导入演练。全部通过才依次导入：货品 → 分仓库存 → 库龄 → 销售 → 组合装；最后独立回查 PostgreSQL 权威 API 的批次与事实归属。\n同一 execution 全程绑定；提交前保存任务基线和 intent；提交结果未决时保留原运行，只查询原任务，禁止重复生成。仅共享 helper 的领取允许自动重试，业务节点失败后停止。默认手动、未激活，无定时器。账号密码及浏览器会话均不保存在 n8n。需要配套 helper 版本支持 /jackyun/export-first/ 路由。", [0, 280], 1200],
]) nodes.push({ id: id(name), name, type: "n8n-nodes-base.stickyNote", typeVersion: 1, position,
  parameters: { content, width, height: 245, color: 4 } });
const workflow = { id: "J8kY2mQ5vR7sT4pN", name: "吉客云导入系统", nodes, connections, pinData: {}, active: false,
  settings: { executionOrder: "v1", timezone: "Asia/Shanghai" }, tags: [] };
if (directHttp) {
  const note = nodes.find(node => node.name === "五表操作说明");
  note.parameters.content += "\nHTTP 版本保留网页初始化、动态仓库/字段、权限和组合装确认；拦截最终提交，以本机 HTTP 创建任务和查询结果。HTTP 阶段专用浏览器离线，由唯一会话所有者按需续期；遇到提交不确定性不重试。此版本仍需要专用浏览器，不是零浏览器实现。";
}
if (apiOnly) {
  const schedule = { id: id("daily-local-0010"), name: "每天本机时间 00:10", type: "n8n-nodes-base.scheduleTrigger", typeVersion: 1.3,
    position: [-760, -160], parameters: { rule: { interval: [{ field: "cronExpression", expression: "10 0 * * *" }] } } };
  nodes.splice(1, 0, schedule);
  connections[schedule.name] = { main: [[edge(claim.name)]] };
  nodes.find(node => node.name === "五表操作说明").parameters.content = "## 五表接口下载\n浏览器仅负责登录和会话初始化；报表阶段由同一个 HTTP 会话完成权限核验、获取全部授权仓库与自营货主、按本轮日期查询数量、服务端导出校验、提交任务、轮询和下载。没有报表页面导航、MiniUI 控件、右键或菜单点击。\n销售按发货时间，固定为截至昨天（含）的最近 45 天，跨月和月初仍为 45 天；每次成功完整导入按该范围更新明细，同内容不重复累计，范围外历史保留。组合装保留母件与子件，图片导出数量上限仍严格校验。库存和库龄记录实际采集日。";
  const note = nodes.find(node => node.name === "导入与运行说明");
  note.parameters.content = note.parameters.content.replace("页面总数", "接口查询总数");
  note.parameters.content = note.parameters.content.replace("默认手动、未激活，无定时器。", "每天本机时间 00:10 定时运行，同时保留手动入口。已核验本机 China Standard Time，对应工作流时区 Asia/Shanghai（UTC+08:00）；本机需保持开机且 n8n/helper 服务运行。仓库模板未激活，实际调度以 n8n 已发布版本为准。");
}
attachHourlyRetryTarget(workflow);
await writeFile(new URL(apiOnly ? "automation/n8n/jackyun-five-dataset-api.workflow.json" : directHttp ? "automation/n8n/jackyun-five-dataset-http.workflow.json" : "automation/n8n/jackyun-five-dataset-daily.workflow.json", root), `${JSON.stringify(workflow, null, 2)}\n`, "utf8");
