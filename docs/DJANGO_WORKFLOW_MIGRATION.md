# 运营事务 Django/PostgreSQL 全板块迁移与切换手册

## 1. 当前权威状态

截至 2026-09-04，本机生产环境的两个写入权威范围都已正式切换到 PostgreSQL：

- 结构化“新品上架”已经完成正式切换。PostgreSQL 是新品项目、店铺规划、七阶段、活动、产品线、周报配置与投递账本的唯一权威；既有 cutover ID 为 `workflow-pg-20260902T110500Z-bdfebd254007`，authority epoch 为 `ce0ceb5d-7bc9-4ea9-8812-0609f7fdf1aa`。旧 D1 `launch` 事实和新品专属 R2 候选命名空间已经终态退役。
- 工作计划、评论、活动、提醒、业务关联、模板、附件元数据/清理队列以及巡店/评价记录已完成正式切换。operations cutover ID 为 `workflow-ops-pg-20260904T094000Z-7438caa33f18`，authority epoch 为 `5ab6bed6-df07-4585-84d9-f650e0855ef1`；PostgreSQL 是该范围唯一事实、状态、审计、revision 和读写权威。

运营事务全板块已跨过 PNR，不存在 D1、`legacy`、`shadow` 或双写回退路径。工作事项附件字节仍保存在现有 R2 命名空间，但文件名、MIME、大小、SHA-256、对象键与清理状态只以 PostgreSQL 为权威；当前切换水位没有活动附件。全局 D1/R2 binding 仍服务 ERP、市场图片、运营事务附件和其他现行范围，不得删除。

## 2. 最终架构与所有权边界

运营事务仍使用现有 React/Next.js 页面和同源公开 API。公开 Worker 只负责真实 principal、角色与 scope、JSON/附件边界、R2 字节读写、HMAC、超时/体积限制和稳定响应；业务事实、状态机、版本 CAS、幂等回放、审计和 revision 全部由 `backend/workflow/` 管理。

PostgreSQL 新增的全板块对象为：

- `workflow_operations_write_authority`、`workflow_operations_migration_runs`；
- `workflow_tasks`、`workflow_task_comments`、`workflow_task_activity_logs`、`workflow_task_reminders`；
- `workflow_task_templates`、`workflow_task_entity_links`；
- `workflow_task_attachments`、`workflow_attachment_cleanup_queue`；
- `workflow_operation_records`、`workflow_operation_activities`。

既有新品对象和 `workflow_write_authority` 保持不变。正式 writer 必须同时核验新品 authority 与全板块 operations authority，且两个 authority 的 epoch/cutover 都必须与进程环境精确一致。reader/writer 固定使用独立最小权限角色和 `127.0.0.1:8061/8062`；任一 authority、角色、签名、revision 或响应边界异常均失败关闭。

附件采用明确的拆分所有权：文件名、MIME、字节数、SHA-256、对象键和清理状态属于 PostgreSQL；附件字节仍由 Worker 在 `workflow-attachments/{taskId}/{attachmentId}` R2 对象中管理。公开 API 不返回对象键，只返回受控下载 URL。删除任务时 Django 原子登记清理队列，Worker 删除 R2 字节并回写结果；不能因为元数据迁移而清空或迁移其他 R2 命名空间。

## 3. 权限、scope 与业务契约

- 工作计划、协作、模板和附件当前没有安全的行级 scope 字段，只允许无数据范围限制的 `viewer/analyst/operator/admin` 读取，写入限 `operator/admin`；受限账号失败关闭。
- 巡店/评价记录按 `platform` 或 `channel` 与 principal scope 过滤；任一明确身份命中授权范围即可访问，两个字段都不命中时失败关闭，不能靠自由文本店铺名猜测权限。
- 受限账号的统一 workflow 搜索只返回其可见的巡店/评价记录，不泄露无 scope 模型的工作计划或新品项目。
- `record_type=launch` 永久拒绝进入通用运营记录；新品继续使用结构化项目接口。
- 全部更新/删除使用版本 CAS；相同 request-id 只允许重放完全相同的 principal、方法、路径、查询和正文。
- 日期和逾期口径保持 `Asia/Shanghai`；页面、公开 API、全局搜索、AI page-data 和库存执行事项桥接复用同一 Django consumer，不在 Worker 重算另一套业务事实。

公开入口包括：

- `GET/POST/PATCH/DELETE /api/workflow/tasks`；
- `GET /api/workflow/tasks/{taskId}/collaboration`，以及 comments/activity/reminders/links/attachments 子资源；
- `GET/POST/PATCH/DELETE /api/workflow/templates`；
- `GET/POST /api/workflow/operations-records` 及详情、活动子资源；
- 既有 `/api/workflow/launch-projects`、产品线、上新跟进和周报配置接口。

Worker 配置继续固定为：

```text
TERUISI_DJANGO_WORKFLOW_MODE=django
TERUISI_DJANGO_WORKFLOW_READER_BASE_URL=http://127.0.0.1:8061
TERUISI_DJANGO_WORKFLOW_WRITER_BASE_URL=http://127.0.0.1:8062
TERUISI_DJANGO_WORKFLOW_TIMEOUT_MS=<可选且有硬上限>
TERUISI_DJANGO_WORKFLOW_MAX_REQUEST_BYTES=<可选且有硬上限>
TERUISI_DJANGO_WORKFLOW_MAX_RESPONSE_BYTES=<可选且有硬上限>
```

共享 HMAC 密钥以及两个 authority 的 epoch/cutover 只来自受控 DPAPI/runtime 配置，不能写入 Git、业务库、命令历史或日志。

## 4. 数据迁移契约

迁移源必须是安装 `0105_workflow_operations_write_authority.sql` 后、Worker 与 workflow reader/writer 均停止时生成的受控一致性快照。该迁移安装 42 个 D1 写入 guard（authority 自身 3 个，加 13 个旧表各 3 个），冻结期间任何旧表写入都必须拒绝。

`migrate_workflow_operations_from_d1` 将旧任务状态合并进任务当前状态，将模板状态合并进模板当前版本，并迁移评论、活动、提醒、关联、附件元数据/清理队列和非 `launch` 运营记录。每次 plan/apply/verify 都绑定：

- 快照路径摘要和源业务内容 SHA-256；
- 十个目标集合的精确行数；
- 规范化后的源/目标内容摘要；
- 唯一 `workflow-ops-<32 hex>` run ID；
- 事务状态和完成时间。

apply 只能使用 plan 返回的精确 run ID，且在一个 PostgreSQL 事务中完成。任一行、外键、日期、JSON、枚举或摘要不一致都整体回滚；verify-only 必须从源快照独立重算并与历史 verified run 和当前目标同时一致。空值只按目标模型的明确兼容契约规范化，例如历史活动任务的 `deleted_by=NULL` 变为空字符串，不伪造删除人身份。

## 5. 受控生产切换顺序

以下步骤只允许在明确批准的生产变更窗口中，从受保护 runtime 中执行。`django-workflow-operations-cutover.ps1` 会拒绝源码目录、非受控快照、未停止的 3000/8061/8062 端口、错误 run ID 或不匹配的证据。

1. 从最新 `main` 建立并复审候选 Worker successor；完成 Django 应用部署、数据库迁移 dry-run、PostgreSQL 备份和独立恢复演练。候选不能提前激活。
2. 通过统一控制器停止 Worker 和 workflow reader/writer，确认端口与精确进程回执均已清空。
3. 在受保护 runtime 执行 `DeployApp`，再执行 operations cutover 的 `InstallD1Authority`。保存完整 D1 authority 前备份和安装 receipt。
4. 执行 `Snapshot`，保存 `workflow-source.sqlite` 和 `workflow-source-manifest.json`；核对 SHA-256、行数、D1 `quick_check` 和 `owner=legacy`。
5. 对该精确快照执行 `MigratePlan`，人工核对返回的 source digest/counts/run ID；随后以同一 run ID 执行 `MigrateApply` 和 `MigrateVerify`。
6. 执行 `AuthorityPrepare`；PNR 前如需退出，只允许在所有服务仍停止时执行 `AuthorityAbort`，保留 D1 为唯一写入源并处置 PostgreSQL 候选数据。
7. 复验源摘要不变后执行 `AuthorityActivate`。该步同时把 D1 和 PostgreSQL operations authority 绑定到同一 cutover/run；从此跨过 PNR，不得回到 D1、legacy/shadow 或双写。
8. 将两个 authority 的 epoch/cutover 写入受控 runtime 配置，启动 workflow reader/writer，激活候选 Worker successor，并由统一控制器回读完整系统状态。
9. 运行 `tools/workflow-operations-production-smoke.ps1`。它只做公开读取和必然失败的负向写入，核验任务/协作/模板/巡店评价、scope、库存桥接、全局搜索、AI consumer、全部附件字节 SHA-256、D1 拒写、新品子域和其他组件，生成最近 30 分钟内有效的 `workflow-operations-system-test-receipt-v1`。
10. 再次停止 Worker 和 workflow reader/writer，使用该精确 smoke receipt 执行 `RetirePlan`，人工核对 plan ID 后执行 `RetireApply`。`0106_workflow_operations_domain_retirement.sql` 将 14 个旧对象变为空 tombstone view，并安装 42 个永久 guard。
11. 启动完整系统，重复公开回读，生成 PostgreSQL 一致性备份并在独立端口恢复验证；最后更新正式运行文档和项目状态。

新的 Django 应用要求两个 authority 都处于 PostgreSQL 状态，所以应用部署、operations authority 激活和 Worker successor 激活必须在同一受控窗口协调完成。不得先把新 runtime 当作日常版本发布而让既有新品 writer 因第二 authority 尚未激活而失去服务。

## 6. PNR 与恢复

- `AuthorityPrepare` 前：D1 仍是唯一权威，可取消变更窗口。
- `AuthorityPrepare` 后、`AuthorityActivate` 前：旧写入已冻结；只有精确 `AuthorityAbort` 可以恢复 D1 写入，禁止同时开放 PostgreSQL writer。
- `AuthorityActivate` 后：PostgreSQL 是唯一事实源。恢复只允许兼容代码、前向修复或 PostgreSQL 备份/WAL/PITR；D1 快照仅作审计和恢复研究，不能重新承担读写。
- D1 终态退役后：必须保留快照、migration run、authority、smoke、retirement receipt、tombstone 和 guard 证据。全局 D1/R2 binding 继续服务 ERP、市场图片、运营事务附件字节和其他现行域，不得删除。

## 7. 2026-09-04 正式切换证据

本次正式切换使用本机已导入生产数据。`teruisi_operations` MCP 当时不可用，因此只在受控 operator 中读取本机 D1 源并写入 PostgreSQL；证据不保存任务正文、密钥或附件内容。

- D1 authority 安装前备份 SHA-256 为 `1c592cc1b086256debaa3db7f5b9023e5aa83886e9e3213f4821af67b6867578`，安装 receipt SHA-256 为 `48aa2bdfda246223f353ed25197dcfddebda66808875aeac4470d307638f59ad`；冻结阶段安装 42 个临时写入 guard。
- 受控源快照位于 runtime 审计目录 `20260904-173728-7b6ee110`。SQLite 文件 SHA-256 为 `85777c2ca67e4597347b9035ce52cdcb0eaf64c7e0b9800c8433c68b3887ed4e`，manifest SHA-256 为 `a2ac4bdbbb12a8c7d5741288356a85dce3f98f549722260249280329780d9e58`，规范化业务摘要为 `e323b069b5f1c1de2a0973856320949702a9c45c2a8a11c02028e7d8701a7e9f`。
- 正式 migration run 为 `workflow-ops-7438caa33f189924efd6bdbc04192660`。plan、apply 和 verify-only 的源/目标摘要完全一致；迁移 35 个任务、1 条评论、34 条活动，提醒、模板、关联、附件元数据、清理队列、巡店/评价记录及其活动均为 0。
- `AuthorityPrepare` 回读 D1 `owner=pending, epoch=2` 且 PostgreSQL 尚为 disabled；`AuthorityActivate` 后 operations epoch 为 `5ab6bed6-df07-4585-84d9-f650e0855ef1`。从该步起只能前向恢复。
- 正式 Worker effective successor 为 `20260904T102019Z-f0c5c52779240d1b`，manifest SHA-256 为 `55eb195cabec31da6279268d575309d5f50aacc3ee71cc974cd5f18e7937ceb1`，build fingerprint 为 `5018221d9d96d5e77776012206e06fc46b14fde96b397537908205157c03ea76`。旧集成树候选曾按 D1 guard 失败关闭，没有产生旧写入；推进固定集成树到最新 `main` 后才激活正式 successor。
- 正式系统 smoke receipt SHA-256 为 `026947990137156cd03ee0f82f24f3231161bffc67615432bc3af5aed27e124d`，details SHA-256 为 `9adaa481c5c651674ad9ec7e59cd1cb9d6f2474088eb8755ad7bfe2c54827ae2`。14 项门禁覆盖公开任务/协作/附件元数据、模板、巡店/评价、新品、writer 负向校验、scope、库存桥接、全局搜索、AI consumer、附件字节、D1 拒写和其他业务域健康。
- D1 终态退役 plan ID 为 `f182178fc0c398059f91fb7f959d33a7f71dd3353b33cba27705c117428bf311`，audit ID 为 `5435231003a22845a48f809a770f06d0c74ce92b479edc3efe6a7817bbe1dc00`，审计文件 SHA-256 为 `b0aeadfb787a7cd1857b33296665df07bb007c86fec79ee93afb7b9fb3f32f9b`。`0106` 已将 14 个旧对象变为空 tombstone view，并留下 42 个永久 guard。
- 退役后公开回读统一返回 200、`Cache-Control: no-store` 和 revision `40:87fc5c3dc46c`：任务 35、模板 0、巡店 0、评价 0、新品项目 12、产品线 10、周报 10；周报配置也正常读取。
- 切换后 PostgreSQL 一致性备份为 `daily-20260904T103402Z-a04a9f950319`，manifest/dump/content SHA-256 分别为 `8de59cd948345b181ebe209f6b1d092d00894d0aab564f9bdc67ad27e120c0e4`、`d98b01854877c9f7aa9d1430d0fc6870a0a3df500d4751f84161af8f79b4631f`、`05ba4cbadabf68e4d3a59bc71363ea065f5aff110befa78f380808d118da7ea4`。隔离恢复 `a04a9f950319` 在 `127.0.0.1:55432` 完成，恢复摘要相同，`productionDatabaseTouched=false`、`serviceStateChanged=false`，临时数据已删除。

## 8. 2026-09-04 隔离迁移演练

本分支使用生产 D1 的只读一致性副本进行演练；`teruisi_operations` MCP 当时不可用，因此按数据查询规范使用本机已导入权威 D1 的只读副本。未记录客户正文或附件内容，生产文件、authority、服务和端口均未修改。

- 源 `PRAGMA quick_check=ok`；工作计划 35、任务状态 35、任务活动 33、评论 1，其余提醒、模板、模板状态、关联、附件、清理队列、巡店/评价记录及活动均为 0。最新任务更新时间为 2026-09-02 08:16:22。
- 受控快照 SHA-256 为 `c6035acdacd97d97201239c9c7f1d346370a721c1fcf910c13192d1c025ccc0`；规范化业务内容摘要为 `6eb5caac38220e70a45f002c0ed698bb277774b2f93f41b275357c1168942108`。
- 批准并独立复验的演练 run 为 `workflow-ops-f5d9c708573c1d5a48ea02328718c27e`；源/目标行数和摘要完全一致。
- 第一次 apply 因历史活动任务 `deleted_by=NULL` 与 PostgreSQL 非空约束冲突而失败；确认任务和 migration run 都为 0，证明事务整体回滚。修复为不伪造身份的空字符串兼容后，重新 plan/apply/verify 全部通过。
- 在隔离 PostgreSQL 17.11（`127.0.0.1:55431`）和隔离 D1 副本完成 prepare → abort → prepare → activate；epoch 为 `f2551d46-2dc2-446f-b437-52fed05e01a5`。激活后 D1 写入探针按预期返回 `workflow_operations_authority_not_legacy`。
- 隔离 Django API 验证了 writer 的 400/409 负向契约、相同 request-id 精确回放、任务创建、scope 过滤和 revision；演练结束后隔离 PostgreSQL 已停止，约 30GB 临时 D1/PostgreSQL 副本已在保留摘要证据后删除。

这些结果证明迁移器能够处理当时的生产形状；正式 cutover 最终使用第 7 节记录的独立生产快照、run、真实 release smoke 与 retirement receipt，没有复用隔离证据。

## 9. 开发验证

在隔离 worktree 和隔离数据库中运行：

```powershell
python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py test workflow --verbosity 2
node --import tsx --test tests/django-workflow-cutover.test.ts tests/django-workflow-service.test.ts tests/workflow-collaboration.test.ts tests/workflow-operations-records.test.ts tests/global-search.test.ts
npm run build
```

构建前先确认正式 3000 端口状态；不得停止生产 Worker 只为运行开发构建。测试服务只能使用隔离端口和独立 PostgreSQL 数据目录，不得复用正式 reader/writer、写入生产 D1/R2、触发机器人/定时任务或修改 production authority。

## 10. 既有新品正式切换证据

既有新品切换的批准 run 为 `workflow-bdfebd254007be2416297de2b17bc82e`，冻结业务摘要为 `bdfebd254007be2416297de2b17bc82e6390735b288b375cf80fd92f809c749f`。当时迁移 12 个项目、12 个店铺规划、84 个阶段和 38 条活动，历史缺失字段以显式缺口保留，没有伪造阶段事实。

旧 `launch` authority 已成为带 `workflow-launch-domain-retired-v1` 标记的空 tombstone view，3 个永久 guard 完整；新品 R2 候选前缀对象、字节、multipart upload 和 part 均为 0。正式切换后的恢复边界保持不变，本次全板块迁移不得改变、覆盖或重建这些既有终态证据。
