# 全局 D1 控制链脱钩

## 状态与边界

2026-09-06：**本机正式采用完成。** 独立工作树验证、23 服务 PostgreSQL 镜像、真实子进程守护恢复、配置兼容、发布链和构建检查通过后，按用户授权完成合并、受控 Django 部署、Worker 证明采用及正式启动回查。当前 effective release 为 `20260906T035823Z-fceee410b71f79b0`；业务与日常控制链均不再读取历史 D1。历史 D1、tombstone、永久 guard、原始 bootstrap 和审计证据继续保留，本次没有移动、删除或销毁它们。

业务权威继续是 Django/PostgreSQL，现有 React 前端和薄 Worker 保留。R2 市场/网店图片与运营事务附件字节不在本次范围内。本变更不新增数据库迁移、业务写权限、数据 revision、双写或回退路径。

采用前的只读核查记录见 [全局退役评估](GLOBAL_D1_RETIREMENT_ASSESSMENT.md)。本结论只覆盖当前 Windows 本机；没有清查或发布远程 Cloudflare 部署，也没有实现新机器绕过历史 bootstrap 的首次安装路径。

## 本机正式采用记录

脱敏机器可读证据见 [`evidence/global-d1-control-adoption-20260906.json`](evidence/global-d1-control-adoption-20260906.json)。

| 项目 | 已核验值 |
| --- | --- |
| 发布源码 | `175c2687328bef491aeca2c32188ae387932ed1b`；PR #10/#11 已合并 |
| Worker effective release | `20260906T035823Z-fceee410b71f79b0` |
| Worker manifest SHA-256 | `ab44b00f096534c97651108c4bb75ea6d9343eb1d6d66f3f96a3b043dd3ea492` |
| 已消费 plan SHA-256 | `fb69313b8ebb86de684d3340e464b69e6bd84c032660e4a7b44415fc9fbc5745` |
| successor SHA-256 | `93cf0f2ddeaba1fb63da5783d0c218f241808360a04fb3e0e66d5cb0c3c8f0a4` |
| 全局 proof SHA-256 | `24503a096bb6649b4d4f0988ccfaab9924100232129daa13f70edf78e1f22c52` |
| 历史终态 schema SHA-256 | `7b6a241ad508a86b630aa6f82b01b2f43339b92b2f9e7c529685531da47e8de5`；12 个退役单元 |
| Django 部署清单 SHA-256 | `28587a32ef44290b974f3dc4af5cec01d4583c8da2cb3b02d91943e1bdab4114` |
| 正式状态 | `Running / Ready / exact_release`，12 个组件、23 个 Django reader/writer/BI 全部通过 |
| 启动与监控 | Worker `VerifyStartup` 通过；Django 守护为 `running / healthy`，重启尝试为 0 |
| 旧版本拒绝 | 前任 `20260905T180043Z-7364a22437c52ae1` 被只读 Verify 拒绝：`ManifestPath is not the authorized effective head release` |
| 生产绑定 | D1 为 0；原 R2 binding `SALES_IMPORT_FILES` 保留 |

首次 `apply` 在修改受保护入口前重新采集 D1/PostgreSQL 证据，通过精确计划 SHA 与前任 CAS，写入连续 successor、消费记录和启动绑定；没有覆盖旧 manifest/authority。唯一总控于北京时间 12:09 回查网页启动成功，后续 Status 确认所有组件健康。

正式 HTTP 验收共 19 项通过，涵盖真实 principal、销售新鲜度、liveness/readiness、页面、财务目标列表/选项/导入历史、市场工作区、AI 模型配置读取、网店、商品、库存、客服和商品分组搜索。错误目标视图/搜索分组为 400，未签名财务内部请求为 401，未知 Host 为 404。Host 检查使用 `node:http` 明确发送原始 Host；首次 `fetch` 检查因客户端规范化该头未形成预期请求，修正验收脚本后通过，生产代码未因此改动。搜索使用无匹配预期的测试词，不能据此声称所有关键词或全部分组均已做性能验收。

运营 MCP 不可用，以上使用经授权的本机只读 API 替代；先确认既有无范围限制管理员及销售 `django_postgresql / sales_single_write`，revision 为 `14:10`，数据截止 `2026-09-03`。仅保存响应状态、大小、摘要和必要的新鲜度元数据，没有保存业务明细、账号、模型配置或凭据，没有导入业务数据、执行模型调用或发送外部通知。

维护期间发现既有 5 个中文 PowerShell operator 为 UTF-8 无 BOM，Windows PowerShell 5 解析失败。通过已安装控制器在 PowerShell 7 中完成受控停止，再在隔离分支为这些文件补充 BOM；PR #11 的 Windows PowerShell 5 Parser 回归测试通过后合并并统一 DeployApp。随后 Windows PowerShell 5 完整 Start 通过。启动隐藏的 PowerShell 5 守护时，需要避免继承 PowerShell 7 的模块路径；本次从 PowerShell 5 父进程使用匹配的 `PSModulePath` 启动并回读健康状态。未手工终止业务进程或修改已部署应用文件。

| 发布后备份与恢复 | 已核验值 |
| --- | --- |
| 一致性备份 | `daily-20260906T041413Z-b507264145c7`，Backup / Verify 通过 |
| manifest SHA-256 | `4d594ab01480efb2c7f3d4c9e9a179fd09a5a6ee08f204b50594dc5e2d5d67ac` |
| dump SHA-256 | `4a3264ab2e7126cf8b1ad72ea914d0b6907b82e58d984a516aefd00c042b3e64` |
| 隔离恢复 ID / 端口 | `260906d1a001` / `55432` |
| 期望与恢复 content SHA-256 | 均为 `39b96e002df005e072684276529ffad085e5a1f8e6bf885377e5c6a778647af4` |
| 恢复边界 | `productionDatabaseTouched=false`、`serviceStateChanged=false`、`cleanupStatus=isolated_data_removed` |

发布前后各域 revision、authority 及 AI/权限证据一致；表计数变化为在线市场图片缓存从 43,856 增至 43,867、市场写请求回执从 17,344 增至 17,415。不同时间的整体内容摘要因此不同，两次恢复均与各自绑定的 exported snapshot 备份一致。正式备份、独立恢复结果、原始退役证明和发布链均继续保留。

验收后，逐项核验 4 个本次镜像 run 的 stopped receipt、独立端口无监听、无所属进程、精确目录和无重解析路径，再清理其临时 PostgreSQL 数据目录；镜像结果、日志与清理审计保留。此清理没有触及正式备份、生产数据库或历史 D1。

## 替代证明

首次采用使用 `worker-local-release-rotation.mjs plan --adopt-d1-control-retirement`。这是受控构建和计划写入操作，要求 Worker 已停止，不能把它当成在线 dry-run。

计划阶段只读核验 12 个退役单元的历史终态，覆盖销售、财务、网店、市场、商品经营、库存、运营事务新品、运营事务全板块、客服、ERP、权限和 AI。核验使用各域 operator-only SQL 的精确对象定义、空 tombstone、永久 guard 和 completed receipt；额外逐一校验共享 receipt 表的 insert、transition、delete 三道永久 guard，拒绝缺失、削弱及额外触发器；财务额外核验 PostgreSQL owner。后续迁移将原共享表替换成终态 view 的情况，以最终 view 契约核验，不要求已经合法消失的旧表 trigger 复活。历史 migration SHA 只允许文件的精确 LF/CRLF 字节变体，不接受任意 SQL 等价改写。

对历史库的只读核验得到的最终 view/guard 数为：销售 9/9、财务 0/42、网店 15/9、市场 49/9、商品经营 3/18、库存 7/21、新品 2/0、运营事务 14/42、客服 5/18、ERP 7/18、权限 2/6、AI 40/120。库存和新品数量包含后续共享表终态替换，不能直接与最初单域迁移的数量相减判断异常。

同时核验固定回环地址上的 23 个 Django reader/writer/BI readiness 身份，并对部署清单、服务配置及各域 enabled 文件做前后摘要比较。证明只保存摘要、终态数量和 cutover ID，不保存凭据或业务明细。既有销售 attestation/forward-recovery 摘要与各域 receipt 中的保留证据摘要一起绑定；本操作不创建新的完整 D1 归档或 PostgreSQL 备份，不取代发布前备份与独立恢复门禁。

候选 release 的 `audit/global-d1-retirement.json` 由 manifest 精确绑定文件 SHA；内部同时绑定源码、构建、release ID、bootstrap authority、采用前任 manifest、历史路径身份及终态证据。初次 `apply` 在修改受保护入口之前，再从候选的不可变 source snapshot 和当前 D1/PostgreSQL 重新采集并比较证据。证据变化即拒绝采用，需要重做计划。

后续版本继承同一份原始证明，不再打开历史 D1。完整 successor 链拒绝删除证明、替换证明、跨链复用、过期 CAS、旧 release、分叉、篡改及孤立 sidecar。启动与自动子进程恢复只验证不可变证明和 effective head；已经安装受控 runtime 的机器永久拒绝旧源码启动器，即使历史 D1 或 authority 文件丢失也不能放行旧入口。

`worker-local-release.mjs` 保持独立可信验证器，不依赖旁边的源代码文件；Django 部署复制该单文件后的校验契约仍成立。D1 采集器只在明确的首次采用操作中动态加载。最初安装阶段的 D1 检查和历史审计工具仍是隔离材料，不是正式采用后正常发布或运行的依赖。

## 配置和自动化

- Django `service.json` v5 中的 `erpSourceD1` 只作为规范绝对路径元数据读取，不检查文件存在；v6 显式声明 `backend=django-postgresql`，允许没有该路径。显式历史退役操作仍严格检查源文件。固定端口、进程身份、ACL、authority、readiness 与启停协议不变。
- 天猫 helper/下载校验直接引用 `normalized-import`；市场图片修复引用独立 `image-repair-contract`，保留原解析和 URL 规则。旧 D1 实现不会随这些纯函数进入日常执行依赖图。
- `netshop:promotion:backfill` 及旧脚本的 CLI 入口永久拒绝执行；历史导出函数仅供隔离研究与夹具使用。普通 Drizzle 生成继续失败关闭，新结构使用 Django migrations。
- `check:backend-boundary` 自动扫描 `app/`、`worker/`、真实 helper 及 package 中的日常 Node 入口和传递动态导入；本次覆盖 366 个模块，违规 0。PowerShell 生命周期和首次采用的隔离审计协议由专门测试覆盖，不把它们冒充为此业务依赖图的一部分。

## 验证结果

新增测试覆盖完整退役单元、错误/重排的域、缺失 guard、额外 view 写入 trigger、未完成/错误摘要 receipt、历史 CRLF、文件篡改、跨版本/路径绑定、硬链接、非 canonical JSON、23 个 readiness 角色及部署文件中途变化。

发布夹具在首次采用后撤去其 SQLite 文件，再采用第二个 successor，两次启动门禁均通过；旧版本与篡改证明被拒绝。首次证据复验失败时受保护入口和发布链保持不变。PowerShell 5 配置夹具验证 v5 缺失 D1、v6 无 D1、错误 backend/端口及历史 operator 的拒绝行为。

仓库单元测试、lint、隔离 Vinext 构建与 rendered Worker 测试通过。构建 Worker 在无 D1 binding、无 Django 服务配置的临时端口上能启动，readiness 返回缺失 23 个服务且不回退。helper 使用临时端口完成打包后健康检查。

本分支验证结果：全量单元测试共 1,871 项，1,851 通过、20 跳过、0 失败（`--test-concurrency=2`）；隔离构建成功，20 项 rendered Worker 测试全部通过；全量 lint 为 0 错误。默认高并发复跑曾出现两项既有短时序测试失败，降低并发后通过，未修改对应业务实现或放宽断言。

最终复审后，30 项证明/发布协议及真实子进程恢复测试全部通过。全库 TypeScript 检查仍有既有诊断，不能报告为通过：使用同一依赖环境、以 Git HEAD 源码作内存覆盖的对照检查，原基线为 160 项，本分支为 141 项，新增诊断 0 项，消除 19 项；本次新代码未增加诊断。

本轮按用户已授权的镜像联调、复审与备份恢复、合并、受控本机发布、正式验收顺序执行。额外的 PowerShell 编码回归单独通过 1 项测试；没有把它计为重新执行了整套单元测试。正式采用及当前状态以上方记录为准。

### 23 服务镜像与守护恢复

脱敏证据见 [`evidence/global-d1-control-mirror-20260906.json`](evidence/global-d1-control-mirror-20260906.json)。从已验证一致性备份恢复到独立 PostgreSQL 17 cluster，数据库端口为 `55444`，Django 使用 `18001/18002` 至 `18111/18112`，BI 为 `18081`。测试凭据随机生成，目录 ACL 仅允许本机操作者、SYSTEM 和 Administrators；禁止生产数据库连接回退、外部网络、定时任务、模型调用和通知。

日常归档按既有规范不包含 ACL。镜像使用显式只读目录导出的 SHA 绑定权限快照，精确重放 23 个角色的 834 项 relation 权限和 4 项列权限；不扩大生产授权。销售 writer 既有 TEMPORARY 权限保留，其他角色不增加数据库 DDL 权限。网店到销售的跨服务 consumer 同样重定位至镜像 reader；网络隔离曾阻止漏配地址访问生产，修正后全链通过。

23 个 Django reader/writer/BI 使用真实 PostgreSQL、生产角色及权限/authority/readiness 校验，冷启动与全部服务重启均通过。运行构建后的 Worker（D1 binding 为 0，R2 使用独立命名空间），页面、销售新鲜度、财务列表/选项/历史、市场、AI 模型、网店、商品、库存、客服和搜索只读接口全部通过；错误目标视图返回 400，无签名内部请求返回 401。测试前后整体业务摘要均为 `d61a14870416a36f88e5f9d50ff58bf32758d815c68fecac200a548d8df249b5`，与绑定归档一致，集群已停止。

`worker-supervisor-process-integration.test.ts` 使用真实 Node 子进程和生产 supervisor 的 Worker/helper 重启循环，测试适配只重定位固定 runtime/端口；子进程使用 HTTP 生命周期夹具，业务接口由上述构建 Worker 镜像另行覆盖。进程凭据绑定实际 CIM PID、创建时间、父进程及命令行。移除测试 D1 后，两类子进程退出均受控恢复；篡改退役证明后再次退出，恢复被拒绝。没有禁用 effective-head、process receipt、不可变证明或 helper artifact 检查。这不能替代正式采用后的完整 controller、登录快捷方式和生产身份回读。

镜像工具固定使用独立路径、端口及测试凭据。`--resume-private-cluster` 仅可复用同一 run 的已停止镜像，要求备份/ACL SHA、版本、路径、配置及无重解析/硬链接全部匹配；仅在离线单用户模式轮换本次镜像临时密码，不开放网络或改写认证规则。每次复用都重新验证全库摘要，不接受有业务修改的快照。历史 D1 不被移动或删除。

发布前新备份 `daily-20260906T033825Z-b37cade931d1` 的 Backup/Verify 通过；manifest SHA 为 `158ec9f328064166a6a0b57aae0476e253c18cfd612ed0a2e77144ab24e67cfc`。独立恢复 `386a2f901c57` 使用端口 `55432`，期望与恢复摘要均为 `8aa537624ba17b1b2d5a2ad2f10dcffbfcfe570ad8a34faf82aa14efbb83dd30`，正式数据库和在线服务状态均未改变，演练数据已由受控 operator 清理。

## 正式采用顺序

1. 按 [PostgreSQL 运维规范](DJANGO_POSTGRES_OPERATIONS.md) 核验备份与独立恢复证据；保留历史 D1、全部迁移 receipt、tombstone/guard 和既有采用链。核验镜像与生产契约及最新 main 集成结果。
2. 获得本机维护窗口授权后，按 [聚合切换发布门禁](DJANGO_AGGREGATE_CUTOVER.md#正式发布门禁) 通过现有受控 operator 更新 Django runtime 控制器和应用，完成 23 服务身份与 readiness 回读。必须先完成这一步，再采集 Worker 采用证明；手工复制 runtime 文件或重写旧 manifest 不被允许。
3. 在受控 Worker 停止状态，由固定干净集成工作树执行首次 `plan --adopt-d1-control-retirement --json`。检查完整候选、计划 SHA、前任 head、退役证明与当前 PostgreSQL 部署绑定，再使用精确 SHA 执行 `apply --approved-plan-sha256 <SHA> --json`。
4. 回读唯一连续 effective head，立即通过既有 `InstallStartup/VerifyStartup` 绑定并验证登录快捷方式；经唯一启动引擎启动，核验 23 服务、Worker/helper 健康及有权限的只读 API。保留真实采用证据后才能升级本文件的状态。
5. 后续 `plan --json` 自动继承证明，不再使用首次采用开关。任何旧 release/legacy/D1 业务恢复都禁止；恢复继续使用兼容代码、PostgreSQL 备份/WAL/PITR 或受控前向修复。

正式采用不包含物理销毁历史 D1。归档移动与销毁需要单独的数据保留决策，不能通过删除生产文件来代替隔离无 D1 验证。
