# 聚合层 D1 依赖清理与受控发布

## 范围与状态

当前源码的公开 API、AI 工具、全局搜索与 Worker 定时入口只通过 Django/PostgreSQL 读取和维护结构化业务事实。`app/`、`worker/` 的全部入口经 TypeScript 编译后的静态导入、重导出和动态导入递归检查，不得出现 D1 binding 访问、旧数据库入口或运行时建表/写 SQL。

2026-09-06 已按用户明确授权完成本机受控发布与生产回查。Worker、Django 应用、启动绑定、健康监控和备份恢复均已验收；具体采用记录见下节。该结论覆盖当前 Windows 本机，不表示发布了远程或云端环境。后续运行版本仍以受控 runtime 的 effective head 和 Django 应用清单为准。

## 本机正式采用记录

本节记录较早的聚合层业务采用。2026-09-06 后续已正式采用 D1 控制链脱钩，当前 effective release 为 `20260906T035823Z-fceee410b71f79b0`；正常启动、自动子进程恢复和后续发布验证不可变退役证明，不再读取历史 D1。下表的旧 release 和 Django 清单保留为当次历史证据，当前采用与备份恢复记录见 [全局 D1 控制链脱钩](GLOBAL_D1_CONTROL_RETIREMENT.md)。历史实体文件仍保留，不因控制链脱钩而删除。

脱敏机器可读证据：[`evidence/django-aggregate-release-20260906.json`](evidence/django-aggregate-release-20260906.json)。没有保存凭据、账号标识、模型配置或原始业务记录。

| 项目 | 已核验值 |
| --- | --- |
| Worker effective release | `20260905T180043Z-7364a22437c52ae1` |
| Worker source commit | `0dc7b2b75bceee64c74456287a590cb6dabdef87`，PR #6/#7 已合并 |
| Worker manifest SHA-256 | `589e304f0e60a8ee711840888b5090c8bcdb7580b2372275e2313e8e219a7f4e` |
| 已批准并消费的 plan SHA-256 | `f88f4156734d99794a912a3efa192511b3ad6fd9d01fb67b8960ea6c8ceb9943` |
| Django 最终 source commit | `db347c22680d09039322cbb4a3da43458f46b03c`，PR #8 已合并 |
| Django 最终部署清单 SHA-256 | `72396639f229985e1db63cd58546095fb87420c70329830c14afeec4b3e0acdf` |
| 组合状态 | `Running / Ready / exact_release`，12 个组件全部健康，23 个 Django reader/writer readiness 全部通过 |
| 启动与守护 | Worker `Verify` / `VerifyStartup` 通过；Django 登录快捷方式精确回读通过，supervisor 为 `running / healthy`，重启尝试为 0 |
| 旧版本拒绝 | 旧 release `20260905T161941Z-2f1ddff054f33ff5` 校验失败，原因为 `ManifestPath is not the authorized effective head release` |

Worker candidate `20260905T175520Z-9732204d73443f0e` 在 ERP 配置核对中被排除，从未激活。最终 Django 网店查询修复只通过受控 `DeployApp` 更新应用；Worker 的有效版本未改变。主源码、受控 Django 应用和不可变 Worker 的代码来源分别绑定，不通过手工覆盖 runtime 文件发布。

真实 principal 回查使用经授权的本机只读 API（当前没有可用的 operations MCP）：账户为既有无范围限制的管理员，先确认销售来源 `django_postgresql / sales_single_write`、revision `14:10`、数据截止 `2026-09-03`。网页、财务分析/目标选项/目标列表/导入历史、市场工作区、AI 模型列表、商品、库存与客服入口通过；14 分组联合查询通过。网店现有商品标识查询耗时约 224 ms，全部分组联合查询约 623 ms，未超时。未输出查询标识或原始结果。

首次宽泛订单关键词查询曾触发现有的 2 秒部分结果机制；后续查询约 86 ms，来源可用，无 D1 回退。该截止机制仍然保留，不能把首次部分结果当作完整搜索成功。生产负向回查包括未知 Host 返回 404、无签名财务内部 API 返回 401、无效搜索分组/目标视图/月份返回 400。受限 scope 的正反向契约在隔离 PostgreSQL 中验证；没有为了验收新增生产用户、触发模型付费调用、导入业务数据或发送外部通知。

| 备份与恢复 | 已核验值 |
| --- | --- |
| 发布前一致性备份 | `daily-20260905T174555Z-1bf6ec64a336`，Backup / Verify 通过 |
| 发布后一致性备份 | `daily-20260905T183255Z-7e27e4b41cc6`，Backup / Verify 通过 |
| 发布后 manifest SHA-256 | `83794c2267a744f1cf689ceef36567aa9603e325186a805946d618cd4a89f48c` |
| 隔离恢复 ID / 端口 | `5a922aaefa5b` / `55432` |
| 期望与恢复 content SHA-256 | 均为 `d61a14870416a36f88e5f9d50ff58bf32758d815c68fecac200a548d8df249b5` |
| 恢复边界 | `productionDatabaseTouched=false`、`serviceStateChanged=false`、`cleanupStatus=isolated_data_removed` |

备份间各业务 revision、authority、AI 与权限域证据均未变化；表计数差异仅为 `market_write_request_receipts` 从 15,381 增至 15,450，故两次备份的整体内容摘要不同。恢复必须与其绑定的同一份备份比较，不能要求不同时间的审计记录完全相同。正式备份、manifest、恢复审计和原有终态退役证据继续保留。

## 最终调用链

- 顶部搜索 `/api/search` 和中央 AI 工具 `search_system_data` 共用有界适配层，14 个分组全部读取现有 Django consumer。市场使用已有的 `/api/market/consumers/query`；它返回直接的 `items/total/truncated`，版本由 `X-Market-Data-Revision` 提供，不增加另一套 envelope。
- 网店 consumer 先以 PostgreSQL `DISTINCT ON` 选出各 source/dataset/platform/shop 的最新已完成批次，再以 `EXISTS` 关联事实，避免每条历史记录重复排序批次；批次 ID 和四个范围字段共同匹配，日期空值、排序 tie-break、推广排除、权限和分页总数保持原口径。此项没有新增索引、表、写权限或 revision。
- 导入批次保持销售 → 财务 → 网店 → 商品经营 → 库存 → 客服 → ERP → 市场的跨源分页顺序。每页只读取必要窗口；计数与数据页的来源 revision/total 不一致时，该分组失败关闭。
- 财务分析、目标、导入及 AI 财务工具删除 legacy/shadow 生产分支。财务模式默认且只允许 `django`，显式旧模式失败关闭。真实 principal、scope、HMAC、请求取消、金额分单位、写请求 replay 和上游错误状态保持原契约。
- 财务目标 Django 接口落实 `view=items/options/full`：列表不再查询管理选项，选项不扫描列表，缺省 `full` 保持兼容。AI 目标查询使用 `items`。
- 网店、财务 Excel 规范化进入独立 `normalized-import.ts`；旧 D1 导入实现仅保留为隔离迁移/测试入口。ERP 分片常量从 Django uploader 获取。
- 市场模型配置只读 AI Django consumer。标注 API、单步 runner 和 scheduled 不再接收 D1 句柄；Django 已领取的任务继续按既有图片 URL 获取、Images 优化、模型调用及 claim/complete fencing 执行。
- Worker liveness 仍独立于数据库。readiness 改为核验 23 个 Django reader/writer 的 `/health/ready`、服务身份和进程角色，最多 6 个并发、总预算 4 秒、每个响应最多 16 KiB。失败返回脱敏的 `django_unavailable` 和服务名，不触发业务写入或服务重启。
- `.openai/hosting.json` 和 Vite 生产配置移除 D1 binding；构建包不再复制 Drizzle 迁移。市场图片、网店图片与运营事务附件仍使用原 R2 binding，未改变其存储所有权。

## 验证

```powershell
npm run check:backend-boundary
npm run test:unit
npm run lint
npm run build
node --test tests/rendered-html.test.mjs
git diff --check
```

构建只能在独立 worktree 中进行，必须确认生产 Worker 不监听该 worktree 的 `dist`。构建后复核 `dist/.openai/hosting.json` 无 `d1`，`dist/.openai/drizzle` 不存在，产物无 `getD1Database`、`sqlite_master`、`ensureFinanceSchema` 等旧访问入口。

Django 验证在独立临时 PostgreSQL 17 cluster 执行，使用独立端口、测试角色和合成数据，禁用外部回调与自动化；禁止连接生产 cluster 创建测试库。执行 `migrate --noinput`、`makemigrations --check --dry-run` 及：

```text
python -B manage.py test netshop.tests market.tests.test_search_consumers market.tests.test_api finance.tests.test_target_views finance.tests.test_api --noinput
```

覆盖当前市场身份投影、字面量匹配、精确分页、金额分单位、签名/角色/scope 拒绝、版本交错、财务列表/选项隔离，以及既有财务导入幂等、目标版本和市场 API 契约。本次没有新增表、数据库迁移或权限授予。

2026-09-06 初始隔离验证结果（基于 `main` 的 `7dc79c67`）：生产依赖图检查 302 个模块，D1 违规为 0；单元测试 1,838 项通过、20 项既有跳过；构建及 20 项产物/入口测试通过，其中直接运行无 D1 binding 的编译 Worker，验证 liveness 正常、缺少 Django 配置时 readiness 返回 `django_unavailable`。独立 PostgreSQL cluster 的 26 项 Django 测试通过，迁移 dry-run 无变化。Lint 为 0 错误、9 项既有警告；全仓 TypeScript 检查仍有 160 项既有诊断，与同一 `main` 基线逐项比较无新增，不能表述为全仓类型检查通过。此段只记录正式授权发布前的隔离验证阶段，最终生产采用见上节。

正式发布前的配置核对发现 ERP readiness 必须复用现行 `ERP` 环境变量前缀，已修正并增加该生产配置形状的回归测试。修正后 23 项服务配置检查全部通过，单元测试为 1,839 项通过、20 项既有跳过；构建、20 项产物测试和 lint 复验通过，TypeScript 仍与基线一致。包含错误 ERP 变量名的候选不得激活。

上线只读回查发现既有网店 consumer 的逐行相关子查询超过搜索 2 秒截止时间。独立 PostgreSQL 上修复后 49 项 Django 测试通过；10 万条合成记录、40 个历史批次的对比中，精确查询从 186 ms 降为 25 ms，宽泛查询从 3,086 ms 降为 46 ms，原实现与新实现的条目、总数和分页完全一致。该数字为隔离性能样本，生产耗时以发布后真实 principal 回查为准。

## 正式发布门禁

1. 用户明确授权本机受控发布与必要服务操作后，重新确认最新 `main`、工作树差异、全部检查和无冲突合并结果；保留已有生产备份、迁移 attestation 与 D1 退役回执。先更新独立集成工作树，停止当前 Worker 之前必须保持主仓库中 guard 绑定的启动入口字节与现行 release 一致；不要提前用 Git 同步覆盖 `package.json`、总控或 launcher。停服后再更新其他源码，受保护入口由 successor apply 原子安装。若先行同步导致门禁拒绝，只有在确认差异全部来自本次同步并验证旧 manifest/guard SHA 后，才可恢复当前 release 的精确入口字节以执行受控 Stop；不能绕过 guard。
   同时只读核对现有 `.dev.vars` 的服务变量契约，不输出凭据。ERP 域沿用 `TERUISI_DJANGO_ERP_READER_BASE_URL` / `TERUISI_DJANGO_ERP_WRITER_BASE_URL`，服务名仍为 `erp_reference.reader/writer`；不得自行发明 `ERP_REFERENCE` 变量或靠新增生产别名掩盖适配错误。
2. 按现有 Django runtime 发布流程更新已验证的应用快照和清单，采用财务目标视图变更，回读各已启用域 readiness。不要直接覆盖运行目录或扩大数据库角色权限。
3. 按现有 Worker 控制器停止网页服务，再执行受控 successor `plan`，核对精确计划 SHA 后 `apply`。`plan` 会生成候选，不是无副作用 dry-run。不得手工启动 Wrangler 或绕过 immutable release 门禁。
4. 使用唯一总控启动；核验 Django 全栈、Worker 新 effective head、激活 fence、登录快捷方式重绑和组合状态。验证健康接口读取 Django，D1 故障或无 binding 不再阻断搜索、AI 查询和标注入口。
5. 使用真实授权账号只读回查搜索、市场、财务列表、导入历史及受限账号拒绝；生产定时任务只核验配置与已有状态，禁止为了验证主动执行模型调用、导入或外部通知。

本次不删除 D1 实体数据库、tombstone、永久 guard、历史迁移、恢复证据或 R2 对象。恢复只允许 Django/PostgreSQL 兼容代码、备份/WAL/PITR 或受控前向修复，禁止恢复 D1、旧模式或双写。
