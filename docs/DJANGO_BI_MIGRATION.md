# BI 看板 Django 只读聚合契约

## 1. 结论与边界

BI 看板是跨领域只读投影，不是新的业务事实域。销售事实与批次继续由 `backend/sales/` 的 PostgreSQL 单写权威持有，库存与库龄继续由 `backend/inventory/` 的 PostgreSQL 单写权威持有，ERP 主数据由 `backend/erp_reference/` 的 PostgreSQL 单写权威持有。BI 只以最小权限读取这些权威表，不复制事实，不创建 writer、scope head、导入批次或独立业务 revision。

本次只重构现有 BI 首页已经展示的销售与库存指标。页面不再并行请求销售、库存接口，也不在 React 中重算库存健康分；所有同名指标由 Django 领域服务组合后一次返回。网店、财务等尚未出现在现有 BI 首页的指标不在本次范围内，不能因模块名称而伪称已纳入。

## 2. 运行拓扑

```text
浏览器
  -> GET /api/bi/overview（公开 Worker：真实鉴权、无范围账号、参数边界）
  -> 127.0.0.1:8081（bi_reader：HMAC、只读事务、组合查询）
  -> sales / ERP master / inventory PostgreSQL 表
  -> 单一 dashboard 响应 + X-Bi-Data-Revision
```

- Django app：`backend/bi/`
- 进程角色：`bi_reader`
- 数据库角色：`teruisi_bi_reader`
- 固定地址：`127.0.0.1:8081`
- Worker 配置：`TERUISI_DJANGO_BI_READER_BASE_URL=http://127.0.0.1:8081`
- 响应契约：`bi-dashboard-read-model-v1`
- 运行控制器：`tools/django-bi-service.ps1`

数据库角色只获得 BI 审计表及生成当前看板所需销售、ERP 主数据、库存和库龄表的 `SELECT`。它没有 writer、序列权限、DDL 权限或上游表 DML 权限。`sales_data_revisions` 的 RLS 只允许读取 `sales` 与 `erp` 两个 revision。

库存总览使用广东仓统一健康规则，因此 BI reader 还必须读取 `inventory_guangdong_monitor_items` 和 `inventory_guangdong_supplier_cycles`。角色配置仅增加这两张配置表的 `SELECT`，不开放监控审计表或写权限。BI readiness 同时验证配置表结构、全部 ORM 列的实际读取权限和写权限拒绝，避免端口与旧 schema 检查通过却在看板查询时失败。此修复需要通过受控部署更新 runtime 并执行 `ProvisionRole`；代码提交不代表生产权限已更新。

2026-09-11 已在本机完成该修复的受控生产采用。BI 真实角色只读回查、默认/近 7 天/自定义公开 API、非法参数拒绝、内部无签名拒绝及 Chrome 页面均已通过，整栈 Running / Ready / exact_release，守护进程 running / healthy。无新迁移，Worker release 保留；上线前备份成功独立恢复，上线后备份已重新校验并沿用本次恢复验证。完整记录见 [BI 权限修复生产证据](evidence/bi-guangdong-permissions-production-20260911.json)。

## 3. 一致性与失败关闭

一次 BI 请求在查询前后分别读取销售/ERP 与库存 revision。两次结果一致才返回；变化时重试一次，仍变化则返回 503，不拼接跨版本页面。组合 revision 为：

```text
<sales revision>:<erp revision>|<inventory revision>:<inventory digest prefix>
```

未知参数、重复参数、超过 367 个自然日的范围、非允许角色、受限数据 scope、HMAC 异常、非 JSON、响应超限、契约版本不符或 revision 头与正文不一致均失败关闭。Worker 不回查原销售/库存接口，Django 也不回查 D1/R2 业务事实。

## 4. 数据迁移语义

旧系统没有 BI 独立事实表，因此不存在事实搬运。迁移只创建 `bi_migration_runs` 审计表，并采用 `plan -> apply -> verify` 固定以下材料：

- 销售、ERP、库存 revision；
- 最近成功销售批次、库存批次与库龄批次；
- 销售业务行、当前库存行与当前库龄行计数；
- 源 authority 身份与规范 SHA-256；
- `legacyBiFactRows=0`、`factCopyRequired=false`。

`apply` 必须携带当前 `planId`，源材料变化即拒绝；`verify` 必须携带 `runId`，并重新计算全部材料。生产环境仅允许 `migration_writer` 执行此命令。

生产操作必须从受保护 runtime 控制器解密现有 migration owner 凭据，不能把数据库密码写入命令或环境文件：

```powershell
& "D:\teruisi-runtime\django-sales\app\tools\django-bi-service.ps1" -Action PlanMigration -Json
& "D:\teruisi-runtime\django-sales\app\tools\django-bi-service.ps1" -Action ApplyMigration -ApprovedPlanId <planId> -Json
& "D:\teruisi-runtime\django-sales\app\tools\django-bi-service.ps1" -Action VerifyMigration -ApprovedRunId <runId> -Json
```

三个动作都会重新采样源 revision；两次尝试仍无法形成一致材料即失败。直接运行 `manage.py migrate_bi_read_model` 只用于隔离开发或测试环境。

## 5. 隔离演练与生产启用

2026-09-05 已在 worktree 独立 SQLite 镜像完成全量 Django migration、合成销售/ERP/库存/库龄边界数据，以及 BI `plan -> apply -> verify`。结果为 `status=verified`，销售业务行 4、库存行 1、库龄行 1、旧 BI 事实行 0，且未复制任何业务事实。完整机器可读凭据见 [`evidence/bi-django-isolated-migration-20260905.json`](evidence/bi-django-isolated-migration-20260905.json)。

隔离凭据只证明镜像通过，不是生产 cutover 收据。生产启用必须在合并后的受控窗口中完成：停止 Worker、备份并复验 PostgreSQL、部署 runtime、执行迁移与 `plan/apply/verify`、配置最小权限角色、启用 8081、绑定 Worker successor、执行公开 API 正负向与页面 smoke，然后回读聚合状态和备份覆盖。

2026-09-05 已完成本机生产启用。生产 plan 为 `bi-plan-f486db46d27d1a203e33365118954e38`，verified run 为 `bi-apply-1079734fb42842eeb1cb13b830bbb8a6`，源摘要为 `f486db46d27d1a203e33365118954e384a29332f6e164b85aa22729e28cc3ae8`；采用水位为销售/ERP revision `14:10`、库存 revision `25:836ee07086e6`、销售业务行 577,957、当前库存行 22,628、当前库龄行 5,525、旧 BI 事实行 0。独立 reader、启动凭据和公开 API 均已回读 ready，unsigned 内部请求返回 401，非法公开 query 返回 400，合法公开 dashboard 返回 200 且响应 revision 与 `X-Bi-Data-Revision` 一致。

Worker 通过 append-only successor 计划 `0de08035d40d360af57f7c879e5ff8c42681303a69e178a8ef1b26def81996b5` 激活 release `20260904T223540Z-40a783da7d4d5867`。发布后备份 `daily-20260904T224400Z-3a04b5b245f5` 已包含 `bi_migration_runs`，manifest SHA-256 为 `539113700f102d56b2056c5476f15419ed7527434f3466599420a01357d639cb`；隔离恢复演练 `5bdcabec9e8a` 的 expected/restored content SHA-256 均为 `818055d8359d8f2ae222f53277345e801023f11472f29a356a20e6a4e2f05910`，且 `productionDatabaseTouched=false`、`serviceStateChanged=false`。完整机器可读凭据见 [`evidence/bi-django-cutover-20260905.json`](evidence/bi-django-cutover-20260905.json)。

## 6. 必测门禁

- Django API：角色/scope/HMAC/参数、组合 revision、源变化重试与失败关闭；
- Worker：精确路径与 query 签名、回环 URL、超时/体积、响应契约与 revision；
- 页面：只请求 BI API、刷新竞态、保留上次成功结果、服务端健康分；
- 运行时：8081 单 reader、DPAPI 凭据、只读角色、迁移收据、统一启停和聚合状态；
- 迁移：空旧 BI 事实、陈旧 plan 拒绝、apply 幂等、verify 重算；
- 备份：包含 `bi_migration_runs`，恢复演练创建 `teruisi_bi_reader`，并继续覆盖全部上游权威表。

广东仓权限回归使用 `pwsh -NoProfile -File tests/bi-permissions-postgres.ps1`。该测试只读取本机 PostgreSQL/Python 程序文件，在当前 worktree 的随机 `.runtime/bi-permissions-*` 目录创建独立 cluster，监听独立高端口，使用合成数据；不会使用生产连接或凭据。测试执行控制器中的原始角色配置代码，再用真实 `teruisi_bi_reader` 连接验证完整看板、缺任一配置表权限、列级写权限误授和只读事务。测试结束停止该 cluster，保留隔离日志。
