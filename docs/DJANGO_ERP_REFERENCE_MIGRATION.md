# ERP 主数据 Django/PostgreSQL 重构与迁移手册

## 1. 边界与结论

ERP 主数据域只包含“货品主数据”和“组合装及子件”。历史入口中的“库存库龄”已经属于 Django 库存域，继续走 `inventory_reader`/`inventory_writer`，不能在 ERP 域形成第二套库存事实。

现有 React/Next.js 数据导入页面和公开 API 保持不变。公开 Worker 负责真实 principal、权限、Excel 解析、HMAC、请求/响应上限和薄适配；`backend/erp_reference/` 负责 ERP 事实、批次、scope head、内容指纹、导入尝试、请求 receipt、原始分片、revision、迁移 run 和 write authority。正式切换后，PostgreSQL 是 ERP 主数据唯一权威，不再运行 D1→PostgreSQL bridge，也不允许 D1 fallback、双写或反向迁移。

ERP 域故障只能使 ERP 导入、ERP 查询和依赖 ERP consumer 的有界功能失败关闭，不得改变销售、财务、网店、市场、商品经营、库存、运营事务、客服或 BI 的写入所有权。

## 2. 垂直链路与最小权限

```text
现有 ERP 导入页面 / global search / AI consumer
  -> 公开 Worker：鉴权、解析、签名、边界
     -> erp_reference_reader 127.0.0.1:8091：历史、货品/组合 consumer
     -> erp_reference_writer 127.0.0.1:8092：直传、分片、幂等发布
        -> PostgreSQL erp_*：唯一权威事实、revision、迁移与审计
```

| 地址 | 进程 | PostgreSQL 身份 |
| --- | --- | --- |
| `127.0.0.1:8091` | ERP reader | `teruisi_erp_reference_reader` |
| `127.0.0.1:8092` | ERP writer | `teruisi_erp_reference_writer` |

reader 只能读取 ERP 有界查询所需表。writer 只能写 ERP 表；唯一跨域权限是按 ERP 映射更新既有 `sales_order_lines.resolved_category`，不得新增、删除或修改销售事实、金额、成本、销量、毛利或批次。旧 `teruisi_erp_reference_sync` 角色必须撤销连接与全部权限并设为 `NOLOGIN`。

## 3. 数据与运行契约

- `products` 以 `product_code` 唯一；`combos` 以 `parent_code + child_code` 唯一，子件数量使用千分单位正整数。
- 每次发布是完整 scope 替换。事实、批次、scope head、指纹、尝试、ERP revision 和销售派生分类在同一事务内提交；处理中 owner 使用 generation/token fencing。
- 相同规范化内容返回 `duplicate`；相同 request ID 只能重放同一 actor、method、path、query 和 body 的既有响应。
- reader/writer readiness 必须独立重算货品与组合 scope 的完整摘要，核对当前完成批次、scope head、ERP revision、PostgreSQL authority、进程 epoch/cutover ID、数据库身份和读写事务属性。完整 Django/BI 运行栈最多常驻约 98 条 Waitress 线程连接，因此生产启动必须回读 `max_connections>=120`；本机正式值为 `128`。
- `TERUISI_DJANGO_ERP_MODE=django`；reader/writer URL 固定为 `http://127.0.0.1:8091/8092`。超时、签名、JSON、revision、响应上限或数据库错误均失败关闭。

## 4. 开发和系统测试门禁

```powershell
& "D:\teruisi-runtime\django-sales\venv\Scripts\python.exe" backend/manage.py check
& "D:\teruisi-runtime\django-sales\venv\Scripts\python.exe" backend/manage.py makemigrations --check --dry-run
& "D:\teruisi-runtime\django-sales\venv\Scripts\python.exe" backend/manage.py test `
  sales finance netshop market products inventory workflow customer_service erp_reference bi
npm test
```

迁移前还必须在独立 PostgreSQL 数据目录、独立端口和 D1 副本上完成：Django schema migration、D1 authority 安装、只读一致快照、`plan → apply → verify`、authority `prepare → activate`、完整摘要重算和旧 D1 写拒绝。镜像演练不得连接生产写端或启动真实 Worker、自动化、机器人和外部回调。

## 5. 受控迁移与切权

生产只允许从受保护 runtime app 执行：

```powershell
$operator = "D:\teruisi-runtime\django-sales\app\tools\django-erp-reference-cutover.ps1"
$erpD1 = "<service.json 中精确 erpSourceD1>"

& $operator -Action PrepareRuntime
& $operator -Action InstallD1Authority -ErpReferenceD1 $erpD1
& $operator -Action Snapshot -ErpReferenceD1 $erpD1
& $operator -Action MigrateDryRun -ErpReferenceSource <受控快照>
& $operator -Action MigrateApply -ErpReferenceSource <同一快照> -ApprovedRunId <plan-id>
& $operator -Action MigrateVerify -ErpReferenceSource <同一快照> -ApprovedRunId <apply-id>

& $operator -Action AuthorityPrepare -ErpReferenceD1 $erpD1 `
  -ApprovedRunId <verify-run-id> -ErpReferenceCutoverId <cutover-id>
& $operator -Action AuthorityActivate -ErpReferenceD1 $erpD1 `
  -ApprovedRunId <verify-run-id> -ErpReferenceCutoverId <cutover-id>
```

`plan` 绑定源路径、源摘要和计数；`apply` 只接受精确计划，既有 bridge 货品投影必须与 D1 完全一致；`verify` 重新读取 D1 快照和 PostgreSQL 全量事实。处理中批次/owner、目标污染、货品投影差异、源文件变化、authority 不一致或审批 run 不匹配均失败关闭。

`prepare` 后、`activate` 前且 writer 未接收请求时，可以用同一 run/cutover 执行 `AuthorityAbort`。`activate` 同时把 D1 owner 切为 `postgresql` 并激活 PostgreSQL authority；成功后即跨过 PNR，不得恢复 legacy/shadow 或重新启用旧 bridge。

## 6. 正式系统测试与 D1/R2 终态退役

切权后先启动 8091/8092 和新 Worker release，再生成正式系统测试 receipt。检查必须覆盖：Django reader、writer 负向、公开导入历史、直传、分片、global search、AI consumer、旧 D1/R2 拒绝及其他域保全。

```powershell
& <runtime-app>\tools\erp-reference-production-smoke.ps1 `
  -ReleaseRoot <effective-worker-release> -D1Path $erpD1 `
  -R2Evidence <r2-evidence> -AuditDirectory <本轮audit目录> `
  -CutoverId <cutover-id> -MigrationRunId <verify-run-id> `
  -SourceDigest <source-digest> -TargetDigest <同一digest> `
  -WorkerBuildSha256 <worker-build-sha256>
```

ERP 历史分片曾复用 `inventory-upload/`；库存和客服均已终态退出该前缀，因此退役前必须在 Worker 停止时证明该前缀对象、字节、multipart upload 和 part 全为 0。全局 R2 bucket/binding 不删除，市场图片和运营事务附件继续使用现行命名空间。

```powershell
& $operator -Action R2Evidence -ErpReferenceD1 $erpD1
& $operator -Action RetirementPlan -ErpReferenceD1 $erpD1 `
  -ApprovedRunId <verify-run-id> -ErpReferenceCutoverId <cutover-id> `
  -SmokeReceipt <system-test-receipt> -R2Evidence <r2-evidence>
& $operator -Action RetirementApply -ErpReferenceD1 $erpD1 `
  -ApprovedRunId <verify-run-id> -ErpReferenceCutoverId <cutover-id> `
  -SmokeReceipt <system-test-receipt> -R2Evidence <r2-evidence> `
  -ApprovedRetirementPlanId <精确plan-id>
```

`0110` 删除 ERP D1 事实/控制对象并建立 7 个空 tombstone view，同时只清理共享导入/上传表的 ERP 行并安装 18 个永久 guard。非 ERP 行的计数和摘要必须保持不变。该 SQL 不能由普通 Drizzle journal、应用请求或 Django 启动自动执行。

PNR 后恢复只允许 PostgreSQL 备份/WAL/PITR、兼容代码或审批过的前向修复。源快照、迁移 run、authority 证据、系统 smoke、R2 证据和 retirement audit 必须长期保留。

## 7. 迁移记录

2026-09-05 已在独立目录和 `127.0.0.1:55439` 完成生产数据镜像演练：8,473 条货品、4,392 条组合件、83 个批次、58 个尝试、83 个规范化指纹和 2 个 scope head 的源/目标摘要均为 `33b4d6032868f5d25532cc9333f09482bc2a205ee92f2cec0524a8f583f4d7fb`；镜像 apply run 为 `erp-reference-5de6ff6a49434feaa013d92624afba2d`。该 run 仅为镜像证据，不得用作生产 authority 或生产完成声明。

## 8. 本机生产完成状态（2026-09-05）

- cutover ID：`erp-reference-pg-20260905T102200Z-8a8dbcb59fc8`；authority epoch：`69f7d42f-109f-43c7-9f84-c86629d8fa00`。
- 生产迁移 run：`erp-reference-eb5fa9fc5cd0467ba58c9dc0a9c11b01`。8,473 条货品、4,392 条组合件、83 个批次、58 个尝试、83 个规范化指纹和 2 个 scope head 已完成源/目标复验，双方摘要均为 `33b4d6032868f5d25532cc9333f09482bc2a205ee92f2cec0524a8f583f4d7fb`。
- reader/writer 固定运行于 `127.0.0.1:8091/8092`；生产 Worker effective head 为 `20260905T024235Z-8ab8213dadf5407c`。正式系统测试 receipt 覆盖 Django 读写负向、公开历史/直传/分片、global search、AI consumer、旧 D1/R2 拒绝和其他域保全，共 10 项全部通过。
- 全量系统测试将旧 `max_connections=100` 的普通角色连接槽耗尽后，终态修复把本机值提高到 `128`，并新增全栈与 ERP 独立启动的 `>=120` 失败关闭门禁。修复后 294 个并发健康请求全部成功，预热状态下 98 条应用连接仍保留 27 个普通角色槽位，聚合状态为 `Running / Ready` 且 10 个组件全部通过。
- operator-only `0110` 已把 7 个旧 ERP D1 对象变为空 tombstone view，18 个永久 guard 已生效；`inventory-upload/` 的对象、字节、multipart upload 和 part 均为 0。全局 D1/R2 binding 保留给仍使用它们的其他域。
- 切换后备份 `daily-20260905T030501Z-66b58028ef30` 已完成独立校验；其 content SHA-256 为 `1faa8b07e95c82e144144f117b936f794fba12deaf51bfc448e378719901c661`。恢复演练 `c45ed90af731` 在独立端口 `55444` 得到相同摘要，未触碰生产数据库，临时数据已移除。
- 生产已跨过 PNR。旧 D1、R2、bridge、legacy/shadow 和反向迁移不得恢复；故障恢复仅允许 PostgreSQL 备份/WAL/PITR、兼容代码或经审批的前向修复。

完整可机读证据见 [`docs/evidence/erp-reference-django-cutover-20260905.json`](evidence/erp-reference-django-cutover-20260905.json)。
