# 吉客云五表会话接口下载

本实现使用当前账号已登录网站的接口协议，浏览器只负责 DPAPI 登录、企业身份检查、签名等价检查及令牌发布。报表查询、权限检查、任务提交、轮询和 OSS 下载由同一个 HTTP 会话完成，不再加载五个报表页面、等待 MiniUI 控件或执行右键菜单。本实现不是独立的官方开放平台 AppKey 接入。

自动任务使用当前 Windows 用户本机 `%LOCALAPPDATA%\Chromium\Application\chrome.exe` 的独立 Chromium，以 `--headless=new` 无窗口运行；不回退到日常 Google Chrome 或可见窗口。沿用吉客云专用 profile 和 DPAPI 绑定，登录数据不与日常浏览器、京东或天猫 profile 混用。每轮只接受自己新启动的进程，并核验可执行文件、Windows 用户、profile、端口、进程 ID 和无窗口参数。安装缺失、端口已占用、验证或登录失败时停止任务，不接管既有浏览器；人工处理验证码仍须显式维护。下载结束后关闭本轮专用浏览器。

2026-09-09 隔离验收：本机 Chromium 151.0.7922.109 完成真实登录与签名等价检查，耗时 4.043 秒，主窗口句柄为 0，退出后专用端口关闭；已有浏览器占用端口时拒绝接管、未调用会话回调，原空白页和进程保持可用，错误 PID 也被拒绝。本次没有提交导出或导入。36 项定向测试、全量 2,020 通过（23 跳过，0 失败）、构建、20 项页面检查及 helper 打包通过；类型检查仍为既有 142 项错误，改动文件无新增错误。原始脱敏证据保存在 `D:\codex-artifacts\jackyun-hidden-chromium-20260909`。

同日已按用户要求正式采用：PR #45 合入源码 `ffd78340fe63f859d603b369e3330f9e1754fa7a`，受控 rotation 激活 release `20260908T164747Z-01eadafc917073fa`，manifest SHA-256 为 `21bea4fb88be03acb7c700e5f033ef2aca1dbaa352d2dce405aceaaeb03ce1ae`，启动入口已重绑。00:58:41 本机总控回读 `Running / Ready / exact_release`，12 组组件全部健康，helper 就绪且空闲。原 n8n 发布版本、12 个节点、连接、`active=true` 和 `Asia/Shanghai` 每天 00:10 均核对未变。首次 Start 在后端进程恢复期间未达到就绪，失败日志保留；后端启动完成后，通过标准总控再次启动成功，重试仅清除了该子进程继承的 `PSModulePath`，未修改全局环境，首次失败根因未单独复现。本次未触发新的五表业务运行。采用记录见 [独立无窗口 Chromium 发布证据](evidence/jackyun-hidden-chromium-20260909.json)。

## 入口与不变的业务口径

- 工作流定义：`automation/n8n/jackyun-five-dataset-api.workflow.json`；生成命令：`node tools/generate-jackyun-export-first-workflow.mjs --api-only`。
- 保留原 n8n ID `J8kY2mQ5vR7sT4pN`，每天本机时间 00:10 自动运行，同时保留手动入口；两者均先领取共享 helper。时区固定为与本机 China Standard Time 对应的 `Asia/Shanghai`。仓库 JSON 保持未激活，实际调度以本机 n8n 已发布版本为准，不能另外启用竞争链。
- 新计划入口为 `/jackyun/export-first/plan-api`，传输版本为 `session_api_v1`；B 节点调用 `export-all`，C/D/E 继续使用现有 `validate/import/verify`。
- 导出顺序：分仓库存、组合装及子件、发货时间销售明细、库龄、SKU 货品；五表全部落地、校验后，依次导入货品、分仓库存、库龄、销售、组合装。
- 2026-09-23 起，新 API 计划固定为截至昨天（含）的最近 45 天，月初、跨月同样滚动；历史 v1 计划仍按原月度范围核验。库存及库龄标记实际采集日。销售依旧使用本轮分仓库存匹配成本，成本冲突、未匹配或缺失均在正式导入前拦截；不因扩大日期范围改变原成本清洗。范围绑定、订单更新及正式采用见 [45 天滚动同步](JACKYUN_SALES_ROLLING_WINDOW.md)。

## 参数来源与权限校验

`config/jackyun-api-templates.json` 来自本账号真实网页查询和被拦截的最终导出请求。校准只保留业务参数、表头和摘要，精确中止被拦截的导出 POST；没有保存 Cookie、token、签名材料、原始客户数据或签名下载地址。

模板随 helper 代码打包为不可变资源；登录配置和 DPAPI 凭据继续由受保护的 mutable root 读取。每轮从权限接口读取五个模块权限和导出权限、字段权限指纹、全部授权仓库及当前企业的唯一自营货主。数量查询与导出条件逐字段比对后，调用平台原有 `validateExcelExport`，再提交异步导出。授权仓库不硬编码具体 ID，日期每轮计算；销售必须为 `timeType=4`。

权限、模板、服务端响应结构、字段或数据数量异常均停止。组合装导出保留两张工作表；当前模板含图片字段，超过平台 2,000 个母件的图片导出上限会明确停止，不能截断后当作全量成功，届时须重新校准导出字段。其他既有全量最低数量和 500,000 行上限继续保留。

## 原任务恢复与时间证据

每表先保存任务基线、查询总数、参数摘要和查询时刻，再原子保存提交意图，最后执行一次 `startExcelExport`。提交结果不确定时不重放 POST；在原运行身份下只查询原任务。任务必须同时匹配新任务 ID、模块、行数、时间窗口和唯一附件；基线任务、多个候选、任务失败、附件变更均拒绝下载。轮询默认有界为 5 分钟，超时保留原运行。

平台 `gmtCreate` 只有秒精度，且可能比本机时间早一秒。新版本保存提交前任务基线响应的 HTTP `Date`、本机请求开始和接收时刻，用已验证的平台时刻确定任务窗口下界；禁止从候选任务反推或放宽窗口。校准请求往返超过 5 秒、提交距接收超过 5 秒、时钟差超过 10 秒或缺少时间头均停止。任务 ID、原始平台创建时刻、标签和附件哈希仍完整保留。

任务窗口的上界也必须换算到同一份已验证的平台时钟。不能把平台 `Date` 作为下界、再直接把本机 `new Date()` 作为上界；平台时钟略快时，这会形成“上界早于下界”的假失败。换算只使用提交前已保存的 `serverDate - receivedAt` 偏差，原有 5 秒往返、5 秒提交间隔和 10 秒时钟差门禁不变。

若原 execution 已经完成唯一库存导出 POST、状态停在 `submitted`，可使用 `tools/jackyun-api-resume.ts` 做受控恢复。恢复只接受以下两种精确现场：

- 未绑定任务：错误必须精确为“导出任务绑定条件无效。”，`pendingTaskId`、`binding` 和本轮下载目录均不存在。
- 已绑定但文件未落地：错误必须精确为 `fetch failed`，`pendingTaskId` 与完整 `binding` 必须共同指向同一个库存任务；本轮下载目录只能包含唯一、真实且为空的 `inventory` 子目录，不得出现临时文件或其他模块目录。

两种现场都必须没有 handoff、验证或导入效果，并按同一流程恢复：

1. `plan <executionId>` 持有原全局锁，只读 n8n 失败证据、活动清单、原计划、API controller 和平台任务列表；必须找到不在原基线内、模块/行数/平台时刻完全一致、已完成且只有一个允许来源附件的库存任务。
2. `apply <proposal.json> <approvedSha256>` 再次读取同一平台任务并复验附件摘要，以 create-only 方式发布续跑许可；不下载、不导入，也不修改原计划、controller、active 或 n8n 历史。
3. 许可只允许同一上海日期内、30 分钟内由一个新的完整 n8n execution 从 `plan-api` 领取。B 节点复用已绑定的原库存任务，禁止再次发送库存导出 POST；后续四表仍按原顺序新导出，C/D/E 屏障与精确批次回查不变。

该恢复仅接受首个库存模块和 `submitted` 状态；是否允许存在 pending task/binding，严格取决于上面两种失败现场。任务缺失、仍在生成、失败、重复、附件改变、文件已开始落地、跨日、其他 execution 已领取或任一证据变化均停止。许可必须在创建后 30 分钟内，由一个新的完整 n8n execution 从 `plan-api` 单次领取；领取后复用原任务并禁止重发库存导出 POST。不能通过删除 `active.json`、重写状态或直接调用导入接口绕过。

API 登录安全验证和专用浏览器端口占用属于另一类“导出前零业务效果”闭合，不使用任务续跑许可。`tools/jackyun-preflight-recovery.ts` 只在原计划为 `session_api_v1`、`exports` 为空且不存在 `exportIntent`，n8n 精确停在 B 节点，并且浏览器事件、导入运行、验证结果和下载目录四条业务效果路径全部不存在时，允许 create-only 闭合。可接受的错误只有精确 `challenge_present` 和“专用浏览器端口已占用，自动任务不会接管已打开的浏览器”；原计划、原 active 指针和 n8n 失败历史均保留，只有后续新的完整 execution 可以推进 active。

`api-controller-state.json` 与历史浏览器 controller 分离。交接仍使用现有 importer 的 schema 2：`navigationIntentAt/tableStableAt` 是兼容字段，分别对应接口预检开始和接口数量查询完成；`evidence.controller=authenticated_http_api` 及显式 API 时刻字段标明来源，不能解释为实际发生了页面导航或表格渲染。

专用浏览器在 HTTP 阶段离线，只有持有原 profile/run lock 的所有者可以刷新和发布令牌。登录失败、刷新不确定或权限错误不通过反复重试掩盖。工作流业务节点不自动重试；跨 execution 恢复仍须审核原运行的许可，不能删除 active 文件或把旧计划改成 API 计划。

每小时安全重试模板采用后，可重试的定时失败会在 60 分钟后创建新的完整 n8n execution，并从共享 helper 领取门禁重新开始；成功即停止。验证码/安全验证、凭据或登录失效、企业/店铺身份不符、任务歧义、来源未就绪、owner 冲突、导出点击或提交结果未决仍立即停止自动重试并保留旧证据。它不重放业务节点，也不放宽本节的会话、计划和活动清单约束。统一结构与发布门禁见 [`N8N_HOURLY_SAFE_RETRY.md`](N8N_HOURLY_SAFE_RETRY.md)。

## 2026-09-08 隔离验收

真实导出及本地演练均在独立工作树、独立输出目录完成，未调用正式导入节点 D、未发布 helper、未更新生产 n8n、未重启服务。演练沿用原 importer 的只读历史基线核验；未写生产业务库。

| 运行 | 五表下载 | 导入前演练 | 结果 |
| --- | ---: | ---: | --- |
| `api-acceptance-1788843246393` | 52.889 秒 | 未执行 | 五表完整下载 |
| `n8n-export-first-1788843367368`（隔离调用同一节点实现） | 55.020 秒 | 15.690 秒 | 五表下载和演练通过 |
| `n8n-export-first-1788843480579`（隔离调用同一节点实现） | 50.251 秒 | 23.776 秒 | 五表下载和演练通过 |

这三轮是在修复时间匹配问题后的连续新导出；不含复用文件、前置校准时间或正式数据库导入时间。后两轮不是生产 n8n execution，其数字 ID 是隔离验收身份。两轮五表原始字节、交接、输入合同和 prepared 产物均另行通过 `verifyJackyunPreparedImports`。

源计数分别为库存 25,734、组合装母件 1,942、销售 6,556、库龄 5,684、货品 8,486。销售过滤后 6,117 行，成本冲突、未匹配、缺失成本均为 0。组合装母件数与拆分后关系行数不是同一口径。

首次诊断运行 `api-acceptance-1788842587286` 的库存任务已生成，但因时间匹配漏选等待超时；不计入连续成功结果，也不把这次失败改写成成功。原生产 897 运行仍保留，不能用这些隔离验收来声明它已完成。

脱敏计时及验证材料在 `D:\codex-artifacts\jackyun-api-only-20260908`；原始业务文件仅保存在本机隔离目录，不进入 Git。生产采用须先核验候选 helper 构建和现有共享链路回归，再审核 897 原运行闭合证据，协调共享 helper 空闲窗口，通过受控 release 替换 helper 与原 n8n 定义。正式导入后的批次回查仍为必需，不能把这些下载/演练耗时报告为正式全链路耗时。

回归记录：全量单元测试 2,001 通过、20 跳过、0 失败；生产构建及 20 项渲染契约通过；42 项受控 release/helper 测试通过；Django 生产边界检查无违规。Lint 无错误，保留 9 项既有警告。全量 TypeScript 仍有 142 项历史诊断，规范化后与既有基线逐项一致，没有新增诊断，不能称为全量类型检查通过。新增负向测试覆盖权限/租户变化、范围和日期、数量截断、时间校准、提交响应丢失后不重放、API 调度隔离以及销售成本失败阻止正式导入。

## 897 旧运行的精确闭合

发布准备复核原 n8n SQLite、计划和唯一 controller 文件：897 在 `warehouseCom` 控件等待处失败，controller 仅为 `navigated`，没有本轮查询、导出提交或业务文件。已部署 release `20260908T023322Z-d783739f19e43a9d` 的 controller 源码摘要为 `35d006f60461f9ce7f8b6fcd4d224f10fd973202a88dd2c5c5bd84e9543dadae`，该异常在仓库选择和最终导出 POST 之前抛出。

现有 `tools/jackyun-preflight-recovery.ts plan 897` / `apply 897 <proposal.json> <approvedSha256>` 增加仅适用于这一次的已审计分支。精确绑定原 n8n 数据摘要、时间、错误节点、请求路径、计划和控制状态字节，同时检查无在途吉客云 execution、无成功重试、helper 空闲和无额外文件。闭合采用 create-only 审计记录；不删除 active、计划或旧失败历史。之后仅允许新的完整 n8n 计划推进 active，897 不能重放。任一文件变化、出现下载/演练目录或身份变化均拒绝，不能作为任意失败运行的通用放行规则。

## 2026-09-08 本机正式采用

在用户明确要求“发布新版，替换旧版”后，已把本实现部署到受控 Worker/helper，并更新原 n8n 工作流；上面的隔离演练段落描述发布前记录，不代表当前仍是候选状态。

- 发布代码由 PR #31、#32 合入，部署源码为 `e193367eeb648f17e22e1b0c56858db752d9f72d`。Effective release 为 `20260908T060055Z-c06e40a5d153d39e`，manifest SHA-256 为 `ac0676a9ff83e432cfe9fda4d0240d61f6b8d0d8d96698451e923d5632d58bb0`。
- 受控 rotation plan SHA-256 为 `7f10eb6d18989a7297ee85e6f5c896ff6bd8e5f5517073308d64a29fc99fa5b2`；apply 已验证唯一 successor 并重绑、回读启动入口。旧 release `20260908T023322Z-d783739f19e43a9d` 保留审计，后续启动采用新 head。
- Django 重新 DeployApp/HardenAcl 后的部署清单摘要为 `0b4235f702266c1fdbd646cdb082b31361b3fe1ccf04c288a27f579d3e54c06d`，受信 verifier 与部署源码一致。本次不改变后端业务代码、数据库结构或领域写入权限。
- 原工作流 `J8kY2mQ5vR7sT4pN` / “吉客云导入系统”已使用 API 模板覆盖。11 个节点及连接、设置均与模板逐项一致，version ID 为 `1e9930b1-4e64-4ee3-919b-d8fe10f8f670`。保持同一所有者、手动运行和 `active=false`，没有新建竞争工作流；其余 16 条定义摘要未变。
- 897 已按上述精确证据闭合为 `closed_before_export`，receipt SHA-256 为 `95f9e8518afd53a2ffe828ba34ee704246bbcef92a573c11e881bea911fdb752`。该状态不表示其下载或导入成功；旧计划、active 和失败历史仍保留。
- 全量回归最终为 2,003 通过、20 跳过、0 失败；发布前高负载下出现过一次浏览器弹窗测试超时，原失败日志保留，定向三次复验及随后全量均通过。首次生产 plan 因新 helper root/template 缺少 verifier 精确登记而在激活前失败，PR #32 补齐两项登记后，64 项 release/rotation 测试通过，再次 plan/apply 成功。测试直接将真实 helper build receipt 交给生产 validator，并验证遗漏和额外可变配置被拒绝；没有绕过门禁。
- 发布前备份 `daily-20260908T053620Z-dd46ac9b49d7` 已完成 SHA 校验与独立端口 55432 恢复演练；manifest SHA-256 为 `5b731557a37a542a642ebab0a88d18a76dae0cacde6027376bbac8e6e3dd4b08`，源与恢复内容摘要均为 `9c980ab8ae53a0f0f2a4a4a483315b202c3072fb3fee14c3576f80d369514c8f`，演练未触碰生产库。
- 总控于上海时间 14:09:20 返回 `started / Running`。本机只读 `/api/sales/data-health` 核验销售 revision 仍为 `17:13`、覆盖截至 `2026-09-07`，helper 返回就绪且空闲。本次发布没有触发新的 n8n execution 或正式业务导入，不能报告为新版生产全链路已跑通。
- 守护进程于 14:12:35 核验为 `running / healthy / all_components_ready`。首次从 PowerShell 7 启动 Windows PowerShell 子进程时，继承的 `PSModulePath` 导致 `Microsoft.PowerShell.Security` 自动加载失败；仅在启动该子进程时去除继承值、随后恢复父进程环境后，受控原脚本正常常驻。没有修改服务代码或全局环境，失败日志仍保留。全部 12 组组件健康，Worker 身份为 `exact_release`。

脱敏采用记录及本地原始证据摘要见 [`evidence/jackyun-session-api-adoption-20260908.json`](evidence/jackyun-session-api-adoption-20260908.json)，发布本地证据在 `D:\codex-artifacts\jackyun-api-release-20260908`。后续首次正式运行必须仍由原 n8n 手动入口执行 A/B/C/D/E；成本检查失败时禁止进入 D，E 必须回查精确批次。更近一次隔离 A/B/C 和独立校验总耗时为 88.591 秒，材料在 `D:\codex-artifacts\jackyun-timing-test-20260908`；该耗时不包含正式导入，也不包含本次服务发布维护时间。

## 2026-09-09 每天本机 00:10 调度

用户明确要求按本机本地时间每天 00:10 执行。本机时区经 `Get-TimeZone` 核验为 `China Standard Time`（UTC+08:00），工作流沿用 `Asia/Shanghai`，新增唯一 Schedule Trigger，定时和手动入口均进入原共享 helper 领取流程。现有 A/B/C/D/E、成本处理、排队和失败停止规则未改变。

本机于 00:09 发布版本 `9dfa78ac-ec2d-45a9-bbab-02380f5e7c35`，原工作流 `J8kY2mQ5vR7sT4pN` 已启用：`active=true` 且 `activeVersionId=versionId`。12 个节点、连接和时区与仓库模板一致，已发布历史节点和连接也已逐项核验；其他 16 条工作流定义摘要不变。使用 n8n 正常页面发布，使在线调度器立即生效，没有通过 CLI publish 后遗漏重启，也没有重启 n8n/Worker/helper。

使用本机已安装 n8n 的 cron 库验证跨日边界：2026-09-09 00:09 的下一次为当天 00:10。本机必须保持开机，n8n 和配套服务运行；离线时不承诺自动补跑。如以后更改 Windows 时区，需要同步调整工作流时区。更改前定义备份及发布回查证据保存在 `D:\codex-artifacts\jackyun-schedule-20260909`。
实际触发已核验：n8n execution 912 于本机 2026-09-09 00:10:00.029 自动开始，模式为 trigger（不是手工/CLI 触发）。发布与首次触发证据见 [调度采用记录](evidence/jackyun-daily-0010-20260909.json)。

## 2026-09-18 零业务效果闭合与已绑定任务续跑

00:10 定时 execution `2879` 在 API 登录阶段因精确 `challenge_present` 失败。经复验空 API 计划和四条业务效果路径全部不存在后，以 `verified_api_login_challenge_without_business_effects` create-only 闭合；后续 execution `2902` 因人工登录浏览器仍占用专用端口，同样在零业务效果现场以 `verified_api_browser_occupied_without_business_effects` 闭合。两次闭合都保留原计划、active 指针和 n8n 历史，不能解释为原运行成功。

execution `2906` 随后通过 DPAPI 自动登录，唯一提交并绑定库存任务 `sys-113277891`（源查询 25,911 行），但在文件落地前因 `fetch failed` 停止。本轮下载目录只有空的 `inventory` 子目录，且不存在 handoff、验证或导入效果；因此发布 30 分钟、单 execution、create-only 续跑许可。execution `2910` 领取该许可后复用原库存任务，没有重发库存导出 POST，并继续完成其余四表、导入和精确批次回查。

`2910` 于上海时间 02:06:06 至 02:08:37 成功：货品 8,508 行、库存 22,807 行、库龄 5,684 行、销售 15,567 行、组合装 4,434 行；库存和库龄快照为 2026-09-18，销售覆盖 2026-09-01 至 2026-09-17。原始数据告警保留为库存 5、销售 51、组合装 2，没有隐藏或改写。过期等待重试 `2894`、`2903`、`2907` 已取消，最终无非终态 n8n execution。

源码为 `12211061b0d418adcc2db62ea8a9efdf45cc8bc3`，不可变 Worker/helper release 为 `20260917T175957Z-94a4e718fdfb027f`，manifest SHA-256 为 `044ce49e17130ed496645c942d4e36fad89199a1eceaa0e6f2164cd9a4bfbf4b`。最终 `Running / Ready / exact_release`、12 组件、启动绑定、helper 空闲和钉钉 connected 通过；没有 Django/PostgreSQL/n8n 重启，也没有数据库迁移。完整闭合摘要、续跑许可、五个批次和前后备份证据见 [2026-09-18 正式恢复记录](evidence/jackyun-zero-effect-recovery-production-20260918.json)。
