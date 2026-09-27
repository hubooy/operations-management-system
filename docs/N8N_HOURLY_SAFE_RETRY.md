# n8n 数据下载导入工作流每小时安全重试

通用旧任务恢复和凭据一致性复查候选见 [吉客云通用安全恢复](JACKYUN_SAFE_PREFLIGHT_RECOVERY.md)。该候选尚未采用，复用本工作流的精确标记分支，不扩大验证码、身份异常或提交结果不明的自动重试范围。

## 目标与边界

已登记的数据下载、导入工作流在定时执行或上一轮自动重试失败后，等待 60 分钟，再创建一条新的完整 n8n execution。新 execution 必须重新从“领取共享 helper”开始，重新取得 execution ID 和协调所有权，再按原 A/B/C/P/M 或五表流程执行。成功后不再产生下一轮；新的完整 execution 再次发生可重试失败时，重新等待 60 分钟。

这不是失败节点重放，也不绕过 n8n 直接调用 helper。导入幂等、恢复清单、活动清单、精确批次回查、跨店隔离及业务点击未决保护继续由各流程原有契约负责。

## 工作流结构

- 共享错误工作流：`automation/n8n/data-import-hourly-safe-retry.workflow.json`，固定 ID `TeruisiHourlyRetry2026`。
- 目标工作流通过 `settings.errorWorkflow` 指向共享错误工作流，并增加一个仅本机可达的专用 Webhook；Webhook 只连接到“领取共享 helper”。
- 错误工作流只接收 `trigger` 和 `webhook` 模式的失败。手动调试、CLI 或编辑器测试执行不会形成自动重试链。
- 可重试失败先等待 1 小时，再通过 `127.0.0.1:5678` 调用目标专用 Webhook。回调暂时失败时仍在同一错误 execution 中每小时重试回调，不会静默结束。
- `tools/start-n8n-service.ps1` 固定 n8n 监听 `127.0.0.1`，并拒绝把已监听在非回环地址的服务视为健康，避免专用 Webhook 暴露到局域网或公网。启动器只恢复现有“新品销售周报”所需的 `Execute Command` 节点，继续通过 `NODES_EXCLUDE` 禁用不需要的 `Local File Trigger`，不使用 n8n v2 的全量放开配置。

当前正式目标是吉客云五表 1 条、京东多店铺 1 条、京东市场榜单 1 条、京准通推广 2 条和天猫 6 店，共 11 条。京东多店铺与市场榜单各保留一个未激活的兼容模板，因此生成器登记 13 个目标 ID；兼容模板不得与现行静默版同时激活。

## 必须停止自动重试的失败

以下情况需要人工核对，错误分类器会直接结束，不安排下一轮：

- 验证码、滑块、短信验证、安全验证、京东业务码 601 或其他风控；
- 吉客云的机器错误码 `challenge_present` 与 `waiting_login` 同样必须停止，不依赖错误中是否含有中文“验证码/登录失败”；精确零业务闭合标记不能覆盖同时出现的安全验证或待登录错误。
- 凭据缺失、损坏、拒绝，登录态失效或登录控件歧义；
- 店铺身份不符、跨店、owner 冲突或恢复清单身份不一致；
- 多个候选任务、任务归属不唯一或来源日期尚未开放；
- 已经点击/提交但远端结果未知，包括 `export_submitting`、`report_submitting`、`page_export_submitting` 和 `task_click_invoked`；
- 需要操作者确认的内容完整性错误，例如分页货品唯一数少于出售中总数。

不得为了追求“直到成功”而缩小以上失败关闭范围。人工处理并闭合旧证据后，只能从原工作流创建新的完整 execution。

吉客云另有精确例外：仅当新 helper 在 B 节点登录前读取 DPAPI 凭据连续失败，并在原全局锁内确认 v2 原计划无导出意图、空 exports，浏览器事件、下载、验证和导入四类效果路径全不存在，才 create-only 保存自动闭合回执并返回 `JACKYUN_PREFLIGHT_RETRY_READY`。错误工作流只对吉客云 B 节点的精确标记安排 60 分钟后从原 Webhook 创建完整 execution；新执行仍须重新通过原计划、active、回执和日期门禁。任何闭合失败、迟到效果、文件缺失、路径异常或其他凭据/登录错误继续停止自动重试。旧 4299 不会因新规则自动闭合，必须单独按原运行证据处理。

该例外已于 2026-09-26 在线发布在共享错误工作流版本 `489d1a1f-4f50-4c27-aca7-fd496abc5191`，只改分类代码；其他节点和连线与前版一致。4299 已按精确原证据单独闭合，随后原吉客云手动完整执行 4397 成功；这次真实成功未触发新的重试分支，重试安全性由失败、重复、迟到效果和人工终态的隔离测试验收。见 [生产证据](evidence/jackyun-dpapi-retry-production-20260926.json)。

需要人工处理的异常通知按 [工作流钉钉通知规则](WORKFLOW_DINGTALK_NOTIFICATIONS.md) 投递用户本人单聊，不发群；通知路由不能改变失败关闭或人工确认门禁。

## 连续三次失败后的 AI 升级

Codex heartbeat `automation-5`（“数据工作流统一监控”）在每小时 05、15、25、35、45、55 分使用一个 Agent 统一只读检查 11 条现行正式工作流。它只检查已经到计划时间至少 5 分钟的流程，并按上海自然日持续追踪到下载、导入和资源收尾全部闭环；原每小时 heartbeat `ai` 保持暂停。对每个 workflow ID，它仍只统计 `mode=trigger/webhook` 且已经终止为 `error/crashed` 的不同 execution；最近一次 `success` 会把连续失败数视为 0，`running/waiting` 不计入失败，也不会触发竞争处置。最新三个生产 execution 连续失败且中间没有成功时，统一 Agent 才执行根因分析；日常不创建或委派子 Agent。

AI 以 workflow ID 和三个有序 execution ID 组成 incident key，同一组失败证据只处置一次。分析先使用紧凑批量快照，只对异常工作流加载对应专项手册和脱敏的 execution、helper owner、浏览器身份结论、活动/恢复清单阶段、文件签收、导入批次、告警和日期覆盖证据。

统一监控不得在本任务中修改源码或配置、建立 worktree、更新生产 n8n、部署 Worker/helper/Django、停止或重启服务、迁移或清理生产数据。确认代码或配置缺陷时只保存最小脱敏证据并提出单独修复任务；平台/网络瞬态且无代码缺陷时继续使用现有 n8n 每小时完整 execution 重试，不随意放宽校验或延长超时。

验证码、风控、凭据/登录、账号或店铺身份、跨店、来源未开放、任务歧义、点击或提交结果未决、活动清单归属不明、owner 冲突和需人工确认的内容完整性错误，即使达到三次也只能由 AI 诊断并给出唯一人工动作，不能自动修改或重放业务。未达到三次且没有必须转人工的安全故障时，统一 heartbeat 保持静默。每 10 分钟检查只跟踪已有 execution、重试链和业务终态，实时的 60 分钟重试仍完全由 n8n 共享错误工作流负责；人工介入提示继续以工作流既有失败关闭与通知能力为准。

## 生成与验证

```powershell
npm run tmall:n8n:generate
node tools/generate-jackyun-export-first-workflow.mjs --api-only
npm run n8n:retry:generate
node --import tsx --test tests/n8n-hourly-retry-workflow.test.ts
```

`npm run n8n:retry:generate` 可重复执行，节点、连接、Webhook 路径和 ID 均保持确定且不产生重复入口。工作流 JSON 不含账号、密码、Cookie、Token、Session 或凭据。

## 受控发布顺序

2026-09-11 已在本机生产受控采用：共享错误工作流 `TeruisiHourlyRetry2026` 与 11 条现行正式流程均已发布，所有者一致，11 个专用 POST Webhook 已注册；n8n 2.32.7 重启后仅监听 `127.0.0.1:5678`，`/healthz` 返回 200。重启期间还发现 n8n v2 默认禁用 `Execute Command`，导致既有 `NewProductWeeklyDingTalk2026` 无法激活；启动器现只恢复该工作流所需节点，并继续禁用 `Local File Trigger`，随后全部 active 工作流成功激活且计划任务持续运行。两个京东兼容模板保持未激活且定义未变。本次从 live 已发布定义只追加重试节点、连线、`errorWorkflow` 设置和策略元数据，没有带入仓库中其他尚未采用的候选业务改动，也没有由部署创建真实下载、导入或平台点击。为避免制造业务副作用，未主动注入生产失败；首次自然可重试失败仍须验证错误 execution 进入 60 分钟等待态并生成新的完整 Webhook execution。脱敏证据见 [`evidence/n8n-hourly-safe-retry-production-20260911.json`](evidence/n8n-hourly-safe-retry-production-20260911.json)。

2026-09-12 的首次自然故障已证实完整重试链：京准通商用设备工作流 13:00 的 trigger execution `1268` 因报表列表迁移引导遮挡而失败；共享错误 execution `1269` 等待至 14:00，随后从同一工作流的协调领取入口创建新的完整 webhook execution `1287`，而非单独重放 B。`1287` 在同一站点弹窗处再次失败，新的错误 execution `1288` 等待至 15:01 后创建完整 webhook execution `1303`；`1303` 于 15:01:58 成功，精确导入 2026-09-11 商用设备店 9,496 行，批次 completed、告警 0、重复 0，落库覆盖零缺口。此证据仅适用于已分类为可安全重试的失败；验证码、身份不符、业务点击结果未决等停止条件仍须失败关闭。

后续生产更新仍需单独授权，并按以下顺序进行：

1. 只读备份 n8n 数据库和现有 11 条正式工作流的当前/已发布版本、active 状态、owner、settings、nodes 与 connections。
2. 先以固定 ID 更新并发布共享错误工作流，确认 Error Trigger、60 分钟 Wait、同 owner 调用策略及目标白名单正确。
3. 逐条更新原工作流 ID，不新建同名副本；保留各自 owner、调度和既有 active 状态。只激活 11 条现行目标，两个京东兼容模板保持未激活。
4. 在维护窗口重启 n8n，使 `N8N_LISTEN_ADDRESS=127.0.0.1` 生效；回读端口只监听回环地址、`/healthz` 正常、各正式工作流 `activeVersionId=versionId` 且专用 Webhook 已注册。
5. 使用隔离的可逆失败验证一次“失败 → 等待态”，不得制造平台业务点击或生产导入；随后验证错误分类器会拒绝验证码、登录失效、身份不符、任务歧义和提交结果未决样例。
6. 保存脱敏采用证据。若共享错误工作流未先发布、owner 不一致、非回环监听、目标版本漂移、出现第二条 active 业务流水线或任一安全分类失效，停止发布并恢复代码/配置，不触发业务运行。
