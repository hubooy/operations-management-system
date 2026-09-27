# 全局 D1 退役评估与 Django/PostgreSQL 统一边界

## 结论

后续正式状态：2026-09-06 已完成控制链脱钩的镜像验证、受控本机采用与正式回查，effective release 为 `20260906T035823Z-fceee410b71f79b0`。见 [全局 D1 控制链脱钩](GLOBAL_D1_CONTROL_RETIREMENT.md)。本评估下述控制依赖描述的是采用前的历史部署，不再代表当前运行版本；原始核查结论保留用于解释变更原因。

2026-09-06 对当前 Windows 本机的源码、正式 Worker 产物、启动控制器和在线健康状态进行了只读复核。**业务读写已统一使用 Django/PostgreSQL；启动、自动重启和发布控制层尚未完全脱离历史 D1 文件。** 当前不能把业务切换完成解释为 D1 实体文件可以删除，或整机已经不存在任何 D1 访问。

不需要重新迁移已经完成切换的业务事实。下一阶段应替换控制层的历史退役证明依赖，继续保留原有不可变证据和永久拒绝旧代码的能力。React/Next.js 前端、薄 Worker 与仍有效的 R2 图片/附件字节不属于 D1 业务数据库迁移缺口。

本评估覆盖本机，不覆盖远程 Cloudflare 账号中其他部署、数据库、定时任务或调用者。没有访问云端资源清单，也没有据此判断任何远程 D1 数据库可以删除。

## 本轮复核证据

源码基线为 `d18b118cc29391f3abdf35ff0c3e26458c806a60`，与核查时 `origin/main` 一致，主工作区无已有差异。本轮未查询业务明细，以下为架构、配置与运行状态证据；各域历史迁移行数不作为当前业务数据水位。

| 检查面 | 本轮结果 | 证据与限制 |
| --- | --- | --- |
| API、页面、AI 工具、搜索、scheduled 的传递依赖 | 302 个模块，D1 违规 0 | `node tools/check-django-production-boundary.mjs`；范围是 `app/`、`worker/` 的传递执行依赖，不能代表所有运维脚本 |
| 当前正式 Worker | `20260905T180043Z-7364a22437c52ae1` | 总控返回 `Running / Ready / exact_release`，检查时间 `2026-09-06T09:15:25.6149553+08:00` |
| Worker manifest | SHA-256 `589e304f0e60a8ee711840888b5090c8bcdb7580b2372275e2313e8e219a7f4e` | 从正式 release 的 `deployment-manifest.json` 重新计算，与既有采用证据一致 |
| 正式 release 完整校验 | `Verify` 返回 `verified / exact_release` | source fingerprint 为 `9041e29952b0406b2631da18d4fefedabaf909a248ecd5d718e561da8ada1df7`，build fingerprint 为 `233703842272962ac57c6917b1d61aeda942408df3b7f80f36978058afd6d121` |
| 正式绑定与构建元数据 | D1 binding 0；Drizzle 迁移目录不存在；R2 binding 1 | 回读正式 `dist/server/wrangler.json` 与 `dist/.openai/hosting.json`，后者只有 `project_id`、`r2` |
| 正式 Worker / helper 产物扫描 | 未命中所检索的旧数据库访问入口 | Worker 搜索六个旧数据库 getter 与 `sqlite_master`；helper 搜索 `node:sqlite`、`sqlite_master`、`drizzle-orm/d1`、`getD1Database`、`.DB`；文本扫描不是完整动态执行证明 |
| 在线 liveness / readiness | 均 HTTP 200 | 复用控制器的 `x-teruisi-local-health: 1` 请求头；readiness 返回 `backend=django-postgresql`、`unavailableServices=[]` |
| 启动绑定 | `VerifyStartup` 返回 `verified` | release 与 manifest SHA 同上，`startupVerified=true` |
| 历史切换、权限与恢复 | 已有正式采用证据 | 见 [聚合层采用记录](DJANGO_AGGREGATE_CUTOVER.md) 与其机器可读证据；本轮未重做生产导入、迁移或恢复演练 |

健康接口无上述请求头时返回 404；这是现行接口边界，不能解释为服务故障。当前 23 个 Django 健康端点对应 11 个业务域的 reader/writer 与 1 个 BI reader。健康通过证明当前就绪，不能替代新的控制层脱钩演练。

## 已统一的业务范围

| 业务域 | Django 权威实现 | reader / writer 端口 |
| --- | --- | --- |
| 销售 | `backend/sales/` | 8001 / 8002 |
| 财务 | `backend/finance/` | 8011 / 8012 |
| 网店 | `backend/netshop/` | 8021 / 8022 |
| 市场 | `backend/market/` | 8031 / 8032 |
| 商品经营 | `backend/products/` | 8041 / 8042 |
| 库存与库龄 | `backend/inventory/` | 8051 / 8052 |
| 运营事务与新品 | `backend/workflow/` | 8061 / 8062 |
| 客服 | `backend/customer_service/` | 8071 / 8072 |
| ERP 主数据 | `backend/erp_reference/` | 8091 / 8092 |
| 权限控制 | `backend/access_control/` | 8101 / 8102 |
| AI 助理及 AI 图片字节 | `backend/ai_assistant/` | 8111 / 8112 |
| BI 只读聚合 | `backend/bi/` | 8081，无 writer |

全局搜索和中央 AI 工具通过既有 Django consumer 聚合，没有第二事实库；Worker 保留鉴权、签名、解析、有界转发及必要的 R2/Images 字节能力。各域继续独立维护权限、业务 revision、幂等和单写所有权。统一 PostgreSQL 不意味着合并所有 reader/writer 权限或重新集中为一个无限制数据库账号。

## 阻止控制层完全退役的具体依赖

### 1. Worker 启动和自动重启读取历史 D1

[`tools/worker-authority-guard.mjs`](../tools/worker-authority-guard.mjs) 的 `currentTombstoneState()` 在解析 effective successor、authority 和 guard receipt 后，仍调用 `inspectD1RetirementState(sourceD1Path)`。它以只读 SQLite 连接核验销售 tombstone views、共享表永久 guard 及 completed receipt；`assertReleaseWorkerLaunchAllowed()` 要求这些检查完成。

[`tools/worker-local-runtime-supervisor.mjs`](../tools/worker-local-runtime-supervisor.mjs) 在首次启动及子进程重启前调用上述门禁。因此 D1 文件缺失、损坏或无法读取会阻断 Worker 启动/恢复。这是从现行源码及正式部署脚本确认的控制依赖；本轮没有通过移动或破坏生产 D1 来演示失败。

不能简单删除调用、把 `completed` 写死为 true，或捕获文件缺失后放行。必须先把同等退役证明绑定到新发布链，再解除对在线 D1 文件的读取。

### 2. Django 生命周期仍检查历史文件存在

[`tools/django-local-service.ps1`](../tools/django-local-service.ps1) 的 `Get-ServiceConfig()` 调用 `Resolve-ErpSourceD1()`，要求 `service.json` 中的 `erpSourceD1` 是实际存在的 `.sqlite` 文件。多个 Start/配置/状态与受控操作复用该配置读取，正式 runtime 脚本中仍有同样调用。

ERP 业务读写已由 PostgreSQL 接管；这里是历史迁移源的存在性依赖。修复时应分离日常生命周期配置和历史迁移操作的源文件验证，同时保留当前 PostgreSQL authority、attestation、readiness、进程身份及 ACL 检查。

### 3. Worker successor 构建仍要求 D1 文件存在

[`tools/worker-local-release.mjs`](../tools/worker-local-release.mjs) 的 `buildWorkerReleaseInternal()` 对初次发布和 `rotation-candidate` 都执行 `assertRegularFile(sourceD1Path)`。[`tools/worker-local-release-rotation.mjs`](../tools/worker-local-release-rotation.mjs) 从 effective head 继承该路径，因此仅改启动门禁仍不能完成后续发布脱钩。

旧 manifest、authority、guard receipt 和 successor lineage 中的 `sourceD1PathSha256` 是不可变来源绑定；**保留路径摘要不等于读取 D1**。不应为了消除名称而重写历史 manifest。需要消除的是日常路径的文件访问，并为新的证明协议提供明确版本与严格验证。

### 4. 默认运维命令与检查覆盖需要收口

`package.json` 仍提供 `netshop:promotion:backfill`，指向直接操作 SQLite 的 [`tools/netshop-promotion-aggregate-backfill.ts`](../tools/netshop-promotion-aggregate-backfill.ts)。它要求项目内显式数据库路径，并在 `netshop_rows` 不是真实表时拒绝执行；现行 tombstone 会阻止它用于已退役网店库。它不是本轮发现的生产双写，但命令名称未说明已退役，仍可能误导操作者使用旧副本。

应将这类入口明确归入隔离历史研究/测试，日常命令失败关闭并说明 Django 权威入口。`check:backend-boundary` 还应覆盖真实 helper、日常 package scripts 与发布产物，单列受控历史迁移工具；不能对全部 `tools/` 一律放行，也不能把任何含 `D1` 字样的证据文件都视作业务依赖。

`db:generate` 当前已经失败关闭。README 中旧 AI Drizzle 发布指引及 AGENTS 中“尚未迁移域可以使用 D1”的通用规则与当前状态不一致，本轮文档修正取消这些现行指引，保留按日期记载的历史切换证据。

## 脱钩实施方案与验收门禁

以下为评估时制定的实施与最终验收方案。后续完成的实现、镜像及正式采用记录见上述控制链文档；原始方案保留供追溯，不能据此直接删改历史 D1 或启动证明。

1. **建立替代退役证明。** 在明确的采用操作中，对各域现有 terminal receipt、永久保护、PostgreSQL authority/attestation 和备份恢复证据逐项验证；保留历史 D1 的受控归档、摘要、来源身份与保留清单。产生版本化、不可变且绑定精确候选 manifest/前任 head 的终态证明。不得仅依赖文件存在、任意本地 JSON、自报的 `completed` 或销售一个域的完成状态来宣称全局完成。
2. **替换 Worker 运行门禁。** 新 release 验证该证明和连续 successor lineage；缺失、篡改、跨主机/跨 release、过期 CAS、孤立 sidecar、旧代码与任何 legacy 启动都失败关闭。正式采用后，新 release 的首次启动、热重启和自动子进程恢复均不再打开 D1。原有 Bootstrap 与历史路径摘要原样保留。
3. **分离 Django 与发布配置。** 日常 Django 生命周期只消费 PostgreSQL 运行契约及终态证明；历史 D1 文件检查仅保留在显式离线迁移/审计入口。Worker successor 的 plan/build/verify/apply 同样不得读取在线 D1，仍完整执行进程、文件、manifest、fencing 和启动快捷方式门禁。
4. **封闭旧运维入口并扩展审查。** 清理默认脚本中的旧业务维护命令，保留有明确范围的离线工具。增加“业务图无 D1”“日常控制图无 D1”“归档证据完整”三类分别可执行的检查，并核查构建包与安装后的实际调用路径。
5. **在镜像中演练缺失 D1。** 使用独立端口、凭据、存储和合成数据，关闭外部通知与自动化。镜像内撤去 D1 工作副本后完成 Django Start/Status、Worker 冷启动/热重启/自动恢复、successor plan/apply/启动绑定回读、23 服务健康及 API 只读验收；再覆盖证明缺失、篡改、旧 release、并发发布和响应丢失等拒绝路径。保留现有最小权限与跨域失败隔离验证。
6. **受控采用与前向恢复。** 全部验证、迁移 dry-run（如有）、必要复审和与最新 main 合并检查通过后，按精确候选及变更范围进入本机受控发布。发布前后使用绑定 snapshot 的 PostgreSQL 备份和独立恢复证据；恢复仅通过新架构的兼容修复、备份/WAL/PITR 或受控前向修复，禁止恢复 D1 业务权威。

完成以上运行、发布、拒绝与真实部署回读后，才能把状态升级为“本机日常业务与控制链完全脱离 D1”。D1 历史归档、tombstone、永久 guard 和恢复证据仍按保留政策保存；物理销毁是独立的数据保留决策，不是本方案的默认步骤。

R2 继续保存市场/网店图片及运营事务附件字节。将它们另行搬入 PostgreSQL 涉及容量、流式传输、备份与恢复成本，属于另一项存储变更，不影响本次 D1 退役结论。开发/测试使用的临时 SQLite 也不构成生产 D1 回退；生产 Django 配置显式要求 PostgreSQL。

## 本轮交付边界

本轮完成只读评估、部署配置与健康复核、缺口定位、实施和验收方案，以及当前说明文档纠偏。没有实现或采用新的终态证明协议，没有修改运行源码、数据库、权限或自动化，也没有停止/重启服务、生产部署、迁移、删除 D1/R2 或发送外部消息。原有生产切换和备份恢复证据继续保留。
