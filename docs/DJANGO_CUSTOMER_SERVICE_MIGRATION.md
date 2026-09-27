# 客服分析 Django/PostgreSQL 重构与迁移手册

## 1. 当前结论与边界

客服分析保留现有 React/Next.js 页面和公开 API 入口，不改写为 Django template。客服会话、配对导入、筛选、分析标注、删除审计、导入幂等、原始分片、revision 和有界搜索 consumer 已实现为独立 `backend/customer_service/` Django app；Worker 只负责真实 principal、无范围账号门禁、Excel/聊天解析、HMAC、请求/响应上限和薄适配。

本分支已完成隔离 SQLite→PostgreSQL 迁移、authority prepare/abort/activate 演练和 API/权限/幂等测试。生产 D1 authority、生产 PostgreSQL 数据、生产 Worker release 和在线服务未在本次开发中修改；因此本分支不能把演练结果称为正式 cutover。正式切权必须在批准的变更窗口内从受保护 runtime 执行。

客服域故障只能使客服 API 失败关闭，不得改变销售、财务、网店、市场、商品经营、库存、运营事务、ERP bridge 或 PostgreSQL 其他域的写入所有权。

## 2. 目标垂直链路

```text
现有客服 React 页面
  -> 公开 Worker：principal、scope、解析、HMAC、边界
     -> customer_service_reader 127.0.0.1:8071：查询、快照、consumer
     -> customer_service_writer 127.0.0.1:8072：导入、分片、标注、删除
        -> PostgreSQL customer_service_*：唯一权威事实、revision、幂等和审计
```

固定端点和角色：

| 地址 | 进程 | PostgreSQL 身份 |
| --- | --- | --- |
| `127.0.0.1:8071` | customer-service reader | `teruisi_customer_service_reader` |
| `127.0.0.1:8072` | customer-service writer | `teruisi_customer_service_writer` |

reader 只读客服域表；writer 只能写客服域表，不能写销售事实、ERP、财务、网店、市场、商品经营、库存或运营事务。writer readiness 必须同时核验 PostgreSQL authority 为 `postgres`、epoch/cutover ID 与进程环境一致、revision 已验证、reader/writer 身份正确和只读/写事务边界有效。

## 3. 已实现契约

- 客服事实、批次、scope head、内容指纹、导入尝试、请求 receipt、删除审计、raw upload、迁移 run、revision 和 write authority 均由 Django app 独立持有。
- 查询日期按 `Asia/Shanghai` 业务日期解释；日期区间为左闭右开。顾客原文和聊天正文默认不返回，显式请求时按消息数、消息内容和总字节数截断。
- 只有无数据范围的客服 principal 可以查询；导入历史、导入和写操作继续执行角色校验。scope、HMAC、JSON 字段集合、字符串/数值范围和枚举均在 Django 再校验。
- 相同客服店铺范围的规范化完整内容返回 `duplicate`；新内容以单事务发布，批次、事实、scope head、指纹、尝试和 revision 一起提交并回查。
- 每个写请求绑定 actor、method、path、query 摘要和 body 摘要；相同 request ID 只能重放同一响应，不能跨操作复用。
- `TERUISI_DJANGO_CUSTOMER_SERVICE_MODE` 必须显式为 `django`。reader/writer URL 必须分别为 `http://127.0.0.1:8071` 和 `http://127.0.0.1:8072`；超时、签名、响应类型、revision、响应上限或服务错误均不回查 D1。

## 4. 开发检查

隔离开发环境使用项目配置的 Python runtime；不在生产 runtime 安装依赖或执行写入：

```powershell
$env:PYTHONPATH = "backend"
$env:DJANGO_SETTINGS_MODULE = "teruisi_backend.settings"
$env:TERUISI_DJANGO_ENVIRONMENT = "test"
$env:DJANGO_DEBUG = "1"
& "D:\teruisi-runtime\django-sales\venv\Scripts\python.exe" backend/manage.py check
& "D:\teruisi-runtime\django-sales\venv\Scripts\python.exe" backend/manage.py makemigrations --check --dry-run
& "D:\teruisi-runtime\django-sales\venv\Scripts\python.exe" backend/manage.py test customer_service
node --import tsx --test tests/customer-service-analysis.test.ts tests/customer-service-import.test.ts
```

## 5. 源快照与历史迁移

迁移源必须是普通 `.sqlite`/`.sqlite3` 文件，不得是生产连接、目录、符号链接或活动 D1 数据目录中的任意猜测文件。先由 operator-only authority 安装脚本完成备份和写保护，再创建一致快照；所有输出放在受控 `audits\customer-service-cutover\` 目录：

```powershell
python tools/customer-service-d1-authority-install.py `
  --source <冻结的客服D1.sqlite> `
  --sql drizzle/0107_customer_service_write_authority.sql `
  --backup <受控D1备份.sqlite> `
  --receipt <authority-install.json>

python tools/customer-service-d1-snapshot.py `
  --source <已安装authority且仍为legacy的D1.sqlite> `
  --output <customer-service-source.sqlite> `
  --manifest <customer-service-source-manifest.json>
```

生产 runtime 不直接从源码执行上述 operator；受保护入口为 `django-customer-service-cutover.ps1`，并且每次都必须显式传入同一个、已核验的 `-CustomerServiceD1` 路径：

```powershell
$operator = "D:\teruisi-runtime\django-sales\app\tools\django-customer-service-cutover.ps1"
& $operator -Action InstallD1Authority -CustomerServiceD1 <客服D1绝对路径>
& $operator -Action Snapshot -CustomerServiceD1 <客服D1绝对路径>
& $operator -Action MigrateDryRun -CustomerServiceSource <受控快照路径> -CustomerServiceD1 <客服D1绝对路径>
& $operator -Action MigrateApply -CustomerServiceSource <同一快照路径> -ApprovedRunId <plan-id> -CustomerServiceD1 <客服D1绝对路径>
& $operator -Action MigrateVerify -CustomerServiceSource <同一快照路径> -ApprovedRunId <apply-id> -CustomerServiceD1 <客服D1绝对路径>
```

Django 迁移严格按 `plan → apply → verify`：

```powershell
cd <受保护runtime app>\backend
python manage.py migrate_customer_service_from_d1 `
  --source <customer-service-source.sqlite> --plan

python manage.py migrate_customer_service_from_d1 `
  --source <customer-service-source.sqlite> --apply `
  --approved-plan-id <customer-service-plan-id>

python manage.py migrate_customer_service_from_d1 `
  --source <customer-service-source.sqlite> --verify `
  --approved-run-id <customer-service-apply-run-id>
```

`plan` 只读源。`apply` 只接受同一源路径、源摘要、计数和未变化的计划，并在单一事务内落库；`verify` 独立重算源/目标完整摘要、计数、scope head、幂等审计和 revision。处理中批次、范围 owner、孤立事实、重复身份、目标非空、源文件变化、authority 状态错误或生产角色错误均失败关闭。

## 6. 受控正式切权顺序

正式操作必须先完成 PostgreSQL 备份、Worker 写入口冻结、Django reader/writer 权限核验和客服源快照；其他领域服务不因客服迁移而停止或重启。runtime 部署顺序为：

```powershell
& <源码worktree>\tools\django-local-service.ps1 -Action Stop
& <源码worktree>\tools\django-local-service.ps1 -Action Configure -ErpSourceD1 <精确ERP-D1路径>
& <源码worktree>\tools\django-local-service.ps1 -Action DeployApp
& D:\teruisi-runtime\django-sales\app\tools\django-local-service.ps1 -Action HardenAcl
& D:\teruisi-runtime\django-sales\app\tools\django-customer-service.ps1 -Action ConfigureCredentials
& D:\teruisi-runtime\django-sales\app\tools\django-customer-service.ps1 -Action ProvisionRoles
```

客服 reader 可先启动用于影子读取对比；authority 仍为 `d1` 时 writer 必须保持停止。完成同一快照的 apply/verify 后，由受保护 migration writer 执行：

```powershell
python manage.py customer_service_write_authority `
  --source <同一客服源快照> --prepare `
  --approved-run-id <verify-run-id> --cutover-id <唯一cutover-id>

# 只有 prepare 后、activate 前、writer 未接收请求且目标仍为 d1 时允许：
python manage.py customer_service_write_authority `
  --source <同一客服源快照> --abort-pending `
  --approved-run-id <verify-run-id> --cutover-id <同一cutover-id>

python manage.py customer_service_write_authority `
  --source <同一客服源快照> --activate `
  --approved-run-id <verify-run-id> --cutover-id <同一cutover-id>
```

authority 变更也必须通过受保护 operator 的 `AuthorityPrepare`、`AuthorityAbort`、`AuthorityActivate`；不得从源码目录直接解密凭据或调用 production database。`activate` 成功后立即启动 `django-customer-service.ps1 -Action Start`，回读 8071/8072 readiness、authority、revision、公开 API 正负向、导入 duplicate/replay、版本冲突、受限 scope、HMAC 篡改和旧 D1 拒绝，再以原子 Worker release 绑定 `django` 模式。切权后不支持恢复 `legacy`/`shadow`、D1 双写或反向迁移。

## 7. D1/R2 终态退役

跨过 PNR 并完成观察后，才可由受保护 operator 退役旧客服 D1/R2 路径。退役前必须具备：成功 PostgreSQL 备份、完整迁移 verify、正式系统 smoke receipt、D1 authority 已为 `postgresql`、旧客服分片对象为 0、processing/owner 为 0，以及保留其他域对象和共享导入表非客服行不变的证据。

旧客服配对上传曾复用 `inventory-upload/` R2 前缀；库存域已经终态退出该前缀，因此客服退役必须在 Worker 停止时证明整个前缀、multipart upload 和 multipart part 均为 0。该证据只退役客服/库存历史上传前缀，不删除全局 R2 bucket 或 binding；市场图片、运营事务附件、网店图片和 AI 图片继续使用各自现行 R2 命名空间。

正式回读和退役命令如下，所有 receipt/evidence 必须位于同一受保护 `audits\customer-service-cutover\` 根内：

```powershell
# 先在 Worker 停止时生成 R2 空前缀证据，再启动正式 Worker 与 8071/8072
& $operator -Action R2Evidence -CustomerServiceD1 <客服D1绝对路径>

# 正式链路运行时生成系统测试 receipt
& <runtime-app>\tools\customer-service-production-smoke.ps1 `
  -ReleaseRoot <effective-worker-release> -D1Path <客服D1绝对路径> `
  -R2Evidence <客服R2证据> -AuditDirectory <本轮audit目录> `
  -CutoverId <cutover-id> -MigrationRunId <verify-run-id> `
  -SourceDigest <source-digest> -TargetDigest <同一digest> `
  -WorkerBuildSha256 <worker-build-sha256>

# 再次受控停止 Worker 和客服 reader/writer 后，以同一新鲜证据执行 plan/apply
& $operator -Action RetirementPlan -CustomerServiceD1 <客服D1绝对路径> `
  -ApprovedRunId <verify-run-id> -CustomerServiceCutoverId <cutover-id> `
  -SmokeReceipt <system-test-receipt> -R2Evidence <r2-evidence>
& $operator -Action RetirementApply -CustomerServiceD1 <客服D1绝对路径> `
  -ApprovedRunId <verify-run-id> -CustomerServiceCutoverId <cutover-id> `
  -SmokeReceipt <system-test-receipt> -R2Evidence <r2-evidence> `
  -ApprovedRetirementPlanId <精确plan-id>
```

`0108` 会删除旧客服事实表和 authority 表并建立空 tombstone view，清除共享导入表的客服行、历史 `customer-service:%` 上传会话/结果，同时安装 18 个客服域永久 guard。共享导入表只拒绝 `domain='customer-service'`，共享上传表只拒绝 `fingerprint LIKE 'customer-service:%'`；其他域数据必须保持摘要不变。退役 SQL 不得由普通 Drizzle journal、Django 启动或应用请求自动执行。

PNR 后的恢复只允许 PostgreSQL 备份/WAL/PITR、兼容代码或审批过的前向修复；D1 退役对象和 evidence 必须继续保留，不能作为事实回滚来源。

## 8. 正式切换与终态证据

| 项目 | 结果 |
| --- | --- |
| Django app / migrations | `check`、migration drift 和客服 10 项 Django 测试通过 |
| 正式迁移 | `plan → apply → verify` 通过，29,018 条会话、7 个批次、1 个 scope head；源/目标摘要均为 `ed3c642fbbfa03f4ee956ffe8c0d0eed58aafde609c58a7413fd4cc9876b04fa` |
| authority | cutover `customer-service-pg-20260905T012130Z-5e02b476b398` 已激活，epoch `6fbe9992-5f8d-44a3-8569-2f90f49a40e5`，PNR 已跨过 |
| 正式系统测试 | Django reader/writer、公开会话、导入历史、分片负向、global search、AI consumer、D1/R2 拒绝和其他域保全全部通过 |
| Worker | effective head `20260904T175625Z-61c15a876f2c685c`，manifest SHA-256 `b7bd38aa9248ccddef008a4e353a819b3ce5c3116e57c79bb1e9aaa787988be7` |
| D1/R2 退役 | retirement plan `8fb53d3be716cbd82bff2cb7336b9d33420a69e739207468bdf15e617a788c07` 已完成；5 个空 tombstone view、18 个永久 guard，R2 `inventory-upload/` 对象/字节/multipart 均为 0 |
| 备份恢复 | 备份 `daily-20260904T180528Z-7f0f7c68b0ba` 已 Verify；隔离恢复 `e9047c2a1b6d` 内容摘要一致，生产库未触碰，临时数据已清理 |

正式迁移 run 为 `customer-service-5e02b476b3984cb590f46fd11081c6d9`。正式 smoke receipt SHA-256 为 `8c25983b973e53a5aa0f72d0035201fd7ff7afea88e4dd3bab52908fbd9dd163`，R2 空前缀证据 SHA-256 为 `1127331846322966dcf8753317fe093be521ce6d421e12cdf8ac55b02aca58c3`。退役时共享导入/上传表的非客服行摘要保持 `141b66b920f7f0ed5a7ec6890b23ffbec47650f984b57e1c73b8b4ff6116f23e`，未删除全局 D1 数据库或 R2 bucket/binding。

正式数据暴露了两项历史边界并已前向固化：历史批次的累计 `conversation_count` 可以大于当前仍指向该批次的会话数，因为后续重导会移动 `last_import_batch_id`；历史消息数组可超过现行新导入 200 条上限，迁移必须在 2,000 条、128 KiB 的独立安全边界内完整保留，不能静默截断。运行时新导入仍保持原 200 条契约。
