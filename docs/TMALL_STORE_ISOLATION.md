# 丽力每日接口分批与天猫店铺独立执行

## 状态和授权范围（2026-09-12）

**已完成受控生产发布，业务终态验收另列。** 用户已明确指定：丽力参考亿玖/亿用现有浏览器登录态 MTOP 分批导出；归档作废 9 月 11 日 22:44:54 的原任务；丽力每日更新；天猫店铺独立执行，失败店铺约一小时后安全重试，不阻塞其他天猫店铺。旧生产发布记录保留为历史证据。

- 丽力固定 workflow ID `TmallLiliDaily2026`，不增加第二条调度，上海时间 11:10 运行。
- 仅 M 改为 `/product-master-direct-v1`、独立协议 `lili-direct-m-v1`。P 保持原实现，不能接受亿玖/亿用的协议头，不能冒充官方 TOP 授权 API。
- M 每 20 个商品一批，**同店批次仍串行**。保留分页总数、唯一商品 ID、提交基线/时间窗、精确记录、文件 SHA、合并完整性、单次导入与批次回查；有歧义或提交结果未知必须停止，不能盲目重提。
- 丽力配置从三日改为一日，持久 cadence 必须受控 CAS 迁移，保留最后成功日期/快照；失败不推进日期。其他店铺节奏不变。

## 独立执行边界

`5791` 是持久协调入口；每个已启用天猫店铺有独立 Node Worker 线程、临时 loopback 端口及随机内部令牌。每个线程运行原单次 helper 状态机，隔离其模块级浏览器变量。仅使用每店注册的浏览器根目录、端口、下载目录、清单和凭据；不同 Profile 名称也不得跨店共享同一 Chromium userDataDir。

同一店铺一次只接受一个 execution。同一 execution 不能跨店或跨 workflow 使用；必须先领取，再从 A 开始。完成/失败后关闭本店浏览器，再确认执行线程已退出，才允许新 execution 领取。线程异常退出或无法证明收尾完成时只隔离该店，其他天猫店铺继续可运行。不得通过删除隔离状态、重启正在工作的其他店铺或复用旧 execution 强行恢复。

京东、吉客云仍保留原共享互斥边界，本次不授权它们与天猫重叠。服务健康返回 `isolationProtocol=tmall-store-isolation-v1` 和脱敏 `storeExecutions`；任一活跃/隔离单元存在时 `busy=true`，维护不得只检查某一个店铺。完成 execution 防重缓存上限为 4096，达到上限失败关闭，只能在全部空闲时受控重启。天猫每线程堆上限 512 MiB；该限额和六个线程并存的真实业务资源负载仍需上线验收。

## 重试

保留既有 `TeruisiHourlyRetry2026`。每条自动执行失败生成自己的错误 execution，等待 60 分钟后只请求该 workflow 的本机 Webhook，创建新的完整 execution。等待不占本店已正常释放的线程，不要求其他天猫店铺结束；同店仍有活跃任务时不能重入。

“约一小时”是 n8n 等待设置，不是系统宕机或调度延迟下的硬实时保证。验证码、登录/身份错误、未知提交结果、任务歧义、内容/覆盖校验失败以及线程收尾未知仍转人工，不承诺所有错误无限自动重试。手动/CLI execution 继续不自动重试。2026-09-12 已取得自然失败→等待→新完整执行的生产端到端证据，详见本文“今晚缺口优先补齐”。

## 受控采用顺序

1. 在独立 worktree 完成聚焦/全量测试、lint、构建、真实编译 helper 合成镜像测试与代码复审。合并到最新 main 不等于生产发布。
2. 获得当前 Worker/helper 维护和完整 n8n 验证授权，确认所有工作流空闲、无正在消费/恢复的 execution；不取消其他任务，不重启数据库。2026-09-12 因浏览器控制不可用，操作者另行明确授权空闲时受控重启一次 n8n，以采用官方 CLI 发布；这不是未来任意重启的长期授权。
3. 保存 live 已发布定义、cadence 和旧清单的精确摘要及回滚证据，遵守现有 Worker 受控发布、备份/恢复、不可变 successor 和快捷方式重绑门禁。
4. 在同一维护窗口核对并归档**唯一指定清单**：`tmall-lili`、runId `f48b8cc7-9120-4f5a-b3bd-14b3de09eec9`、`exportSubmittedAt=2026-09-11T14:44:54.797Z`。使用受控 abandon 入口保存完整证据，不删除文件或平台记录；身份、阶段、内容变化必须停止核对。禁止作废其他 109/20 行记录。
5. 新 helper/配置采用后，以 live 丽力定义调用 `adaptLiliDirectMaster`，只调整 M 路由/协议/名称/对应连线和说明。**不能整份导入仓库模板带入尚未采用的逐日回填循环**，不改其他节点、cron、retry、所有者或新建 workflow。按 live 旧成功日期 CAS 迁移 interval 3→1；任何条件不满足，不启用新的 M。
6. 回读 activeVersionId=versionId、六店各一条调度、各自重试入口和 helper 协议。仅从 n8n 创建丽力与另一店的新完整 execution，证明同时运行、同店互斥及失败隔离；核验每店 A/B/C/P/M 文件、精确批次、覆盖和浏览器关闭。失败不能记作已修复。
7. 补充实际采用证据后才更新 README/手册/Skill 的生产现状；清理仅限已完整合入且干净、不在使用的分支/worktree。

## 当前验证

- 全量同一单元测试集合以 `--test-concurrency=4` 运行：2132 项，2111 通过、21 跳过、0 失败。首次全量发现展示目录的丽力 cadence 未同步，已重新生成目录并通过全量复验。日志为隔离 worktree 的 `outputs/tmall-store-isolation-unit.log`。
- 全量 lint：0 错误、11 项既有警告；改动文件 lint 与 `git diff --check` 通过。隔离前端生产构建和真实 helper 构建通过。
- TypeScript 全量 `--noEmit --incremental false` 仍报 145 项既有诊断；与相同 `262aad7c`、相同依赖的干净集成树对比一致（唯一文本差异为既有测试诊断行号移动），没有新增诊断。不宣称全量类型检查已通过。

- 真实编译 helper 的合成镜像：六个店铺同时领取通过；同店重复、跨店复用 execution、禁用店铺、越过 A 进入 M 均被拒绝。镜像浏览器路径固定不存在，无登录、平台导出、数据库连接或业务导入。
- 真实 Worker 生命周期夹具：正常收尾释放本店，崩溃只隔离本店，其余店铺与正常店铺的新 execution 均可运行。
- 上述隔离测试不证明平台在六店同时导出时无风控，也不证明丽力真实商品已导入。生产采用及真实验收必须按下节判定。

## 2026-09-12 本机采用

- 源码 `94244216` 已快进合入 main 和固定干净集成树。effective Worker/helper release 为 `20260911T165424Z-941a792a6c05e33e`，manifest SHA `92b21b497aad296b9d6f5a1bd9d3d28350ce23de0c1e566f64435f8f5f396e84`；精确 plan SHA `addc7b3d1b2a3fbd7fd9f670e438e082047dcfb5d15f99877026a2e8b75814de`，successor/启动重绑通过。启动初次聚合检查曾报告 core 未就绪，未重试启动或重启后端；随后只读复验为 `Running / Ready / exact_release`、所有组件 true。
- PostgreSQL 发布前备份 `daily-20260911T163933Z-a2ca789a69b2` 已校验，隔离恢复 `a13c9e427b08` 内容摘要一致，生产库未触碰；n8n SQLite、所有 live 定义、所有者、Webhook、精确旧清单及 cadence 均另存私有运行证据。
- n8n 原进程在 0 个 new/running/waiting execution、helper 空闲且精确身份复验后停止；通过官方 CLI 只导入和发布丽力 live 最小改动，按现有服务启动脚本重启一次。丽力版本 `b5ef2f61-190f-4ddd-a680-3455975c7840` 的 current/activeVersionId/历史一致；其他工作流、所有者及 11 个 Webhook 完全未变。
- 精确旧清单 `f48b8cc7-9120-4f5a-b3bd-14b3de09eec9` 已原样加归档说明保存为 abandoned，未删除文件或平台记录，也未处理其他 109/20 行记录。cadence 以旧摘要和最后成功日 CAS 迁为 1，保留最后成功/快照 `2026-09-07`，迁移后 nextDueDate 为 `2026-09-08`（到期状态，不虚构新成功）。
- 新完整 n8n execution 丽力 **1058** 与亿玖 **1059** 在上海 01:02:40 同时启动；01:02:55 两店均已独立进入 import 阶段，01:10:17 前均成功终止，真实并行和完整导入证据成立。小时重试自然失败链尚未因此获得端到端证明。
- 丽力 M：2026-09-12 快照，109 商品、6 批（20×5+9）、合并 2452 行、1940 个非空唯一 SKU ID，精确 completed 批次后缀 `650f1d233e6a23d2e698237a4a89e0483d4bd77b9000c86977ac12fd9b3fb9ac`；合并 SHA `8644a31cbc721e3f59be131bd0a9364ab8990b489c7e6ab7fabab838d82003b0`。3 类源数据告警：512 行缺 SKU ID（按商品/属性/行号隔离键）、846 行缺商家 SKU 编码、762 行重复商家编码；不能称作零告警或 2452 个唯一 SKU。
- 亿玖 M：同日快照，128 商品、7 批、2624 行、3 类源数据告警，精确 completed 批次及全部文件 SHA 回查通过。两店 cadence 均为 lastSuccessDate/lastSnapshotDate `2026-09-12`、nextDueDate `2026-09-13`，两店调试端口关闭，helper 无 owner。
- 丽力本轮商品日/推广日为 9 月 7 日，分别 81/50 行、0 告警；亿玖为 9 月 9 日，分别 51/43 行、0 告警。四个精确批次均通过本机公开只读 API 的 completed/来源/平台/店铺/行数独立回查，C/P 同日覆盖复核通过。数据来源为授权本机 Django/PostgreSQL 只读替代来源；本轮不等于历史补齐：规划截止上海 9 月 11 日，丽力仍余 9 月 8–11 日，亿玖仍余 9 月 10–11 日。
- 发布后备份 `daily-20260911T171150Z-d797f82589ee` 与 SHA 复验通过，manifest SHA `bd1c0ef18d1544ebbf0c83f6618d52c6f08a373f9b43f082479b04f2e6decc4a`；本次未重复恢复发布后备份。
- 维护窗口从 PowerShell 7 启动 n8n 曾使 Windows PowerShell 5 子进程无法加载 `ConvertTo-SecureString`，并非凭据损坏。获得一次性授权后，在 0 个活跃/等待 execution、helper 空闲且完成 SQLite 在线备份时，改由已安装的无控制台计划任务受控重启；未改凭据，未重发周报，未重启 PostgreSQL、Django、Worker 或 helper。重启后新品周报 execution 1065 起持续自然成功，模块环境问题已关闭；详见 `docs/N8N_NO_CONSOLE_STARTUP.md`。

## 2026-09-12 今晚缺口优先补齐

- 01:31 以授权本机 Django/PostgreSQL 只读公开 API（`teruisi_operations` MCP 本轮不可用）查询注册起始日至上海 9 月 11 日的实际覆盖。六店联合缺口共 40 个店铺日：亿玖 2、丽力 4、拓丰 9、亿用 10、炊之王 8、马思图 7；商品日与推广日分别规划，任一侧已覆盖时不重复补该侧。
- 01:35 只通过六条 live workflow 的各自本机 POST Webhook 同时创建完整 execution 1066–1071。每店成功并回查覆盖确实减少后，才为该店创建下一次完整 execution；同店串行，六店互不等待。未绕过 n8n 调用天猫业务 helper，也未使用仓库中尚未采用的多日循环候选定义。
- 第一阶段已把亿玖、丽力、拓丰、炊之王分别补到只剩 9 月 11 日；这四店的该日 B 均返回 `SOURCE_NOT_READY (NO_DATA_ROWS, MISSING_EXPECTED_DATES)`，未误记完成。亿用 execution 1069 已成功补入 8 月 31 日的商品日和推广日后，在 M 读取导出记录时失败；活动清单证明 10 个批次仍为 planned 且没有 `exportSubmittedAt`，可由完整流程安全重试。马思图 execution 1071 已补入 9 月 1 日商品日，C 覆盖回查遇到 HTTP 503，推广日仍缺。
- 马思图 retry execution 1072 等待至上海 02:36:06；亿用 retry execution 1075 等待至 02:36:55。两条 Wait 不持有 helper 或本店浏览器，也未阻塞其他四店。永久策略仍只自动重试可安全自动重放的定时/Webhook失败；验证码、登录/身份、点击/提交结果未决、来源未出数和内容完整性错误停止自动重试。
- 亿用 1110 在 60 分钟后由 n8n 自动创建完整 execution 并成功，随后 1117–1127 逐日补到 9 月 10 日。马思图先后由 1109、1143、1160、1176 自动恢复；每次 A/B/C/P 成功发布的日事实均保留，M 对原 6 条分页导出任务只等待/续接、没有重复提交。1176 等到原任务齐备并完整成功后，1177、1178、1180 连续补完 9 月 9–11 日。该链证明等待不占其他店铺、失败店铺约一小时后从 A 重新开始完整流程、已发布日事实不因末段 M 失败回滚。
- 9 月 11 日最初尚未出数，亿玖/丽力/拓丰/炊之王在首次失败约一小时后各做一次完整流程复查仍返回 `SOURCE_NOT_READY`，没有高频重试。06:16 马思图 1180 首先证明该日源数据已可用；随后只通过其余五店 live Webhook 同时创建 1181–1185，五店均成功。06:19 的最终权威覆盖回查显示六店商品日和推广日从各自注册起始日至 9 月 11 日均无缺口，初始 40 个店铺日已全部补齐。
- 最终 n8n 无 new/running/waiting execution，六条 live workflow 均 active 且 current=published，共享小时重试版本未变；helper `ready/busy=false/storeExecutions=[]`，六个店铺调试端口均无监听，n8n healthz 为 200。最终私有只读证据为 `D:\teruisi-runtime\tmall-store-isolation-20260912\tmall-gap-audit-final-0619.json` 与同目录 `tmall-n8n-runtime-final-0619.json`；仓库脱敏证据见 `docs/evidence/tmall-gap-fill-production-20260912.json`。
- 补缺完成后 PostgreSQL 正式备份 `daily-20260911T222115Z-97d86afcb804` 已通过清单、dump 和内容 SHA 复验，manifest SHA 为 `2d56d3952c3a67ff394d58e2e75b0da8b0e037cfc57a8a5907c5883420e8b7c1`；`serviceStateChanged=false`，未为备份重启任何服务。本次未重复执行隔离恢复演练。
