# Django/PostgreSQL 销售域单写切换手册

本文定义销售域从旧存储切换到 Django/PostgreSQL 的终态契约、执行顺序、验收门禁和恢复边界，并记录当前本机已经完成的不可逆切换证据。

> 当前状态：2026-08-29/30，当前 Windows 主机的销售域已完成 Django/PostgreSQL 单写切换和 D1 销售对象退役，cutover ID 为 `sales-pg-20260829T204417Z-d9896e904d8092cb`。PostgreSQL 已是销售读写唯一权威，D1 不再是销售写入、读取、容灾或回滚来源。2026-08-31 又完成财务正式切换和销售五个 Tab 的整体验收；本机最新运行证据见第 13.4 节。第 5、7、11、12 节保留为本次过程说明及新环境重建模板，不表示当前本机仍等待执行。该结论不扩大到远程生产、高可用、ERP 权威来源或其他业务域。

## 1. 最终所有权

| 数据或能力 | 最终权威与单写所有者 | 说明 |
| --- | --- | --- |
| 销售事实、批次、revision | Django/PostgreSQL | 读写均只进入 PostgreSQL |
| 导入范围、幂等指纹、尝试审计、锁与 fencing | Django/PostgreSQL | 必须随事实原子发布并保留完整历史 |
| 原文件签收、分片字节、分片会话、暂存与校验元数据 | Django/PostgreSQL | PostgreSQL 是唯一存储与生命周期所有者，不存在销售 R2 回退 |
| 销售总览、品类分析、明细和消费者查询 | Django reader/PostgreSQL | 不从 D1 读取销售结果 |
| ERP 货品主数据 | D1 | 仍是 ERP 权威来源 |
| PostgreSQL ERP 参照副本 | 独立 ERP-only outbox bridge | 消费 ERP 事件并维护独立 checkpoint；仅以 ERP 映射回填现有 `sales_order_lines.resolved_category` 派生分类 |
| 其他尚未迁移业务域 | 原有系统 | 不因销售切换而扩大迁移范围 |

Cloudflare Worker 只承担公开鉴权、principal/scope 校验、HMAC principal 信封、Excel 解析、分片请求边界、请求超时与体积边界和边缘协议适配。分片字节经受控回环写入 PostgreSQL，Worker 不保存销售对象。它不得：

- 在 D1 创建、更新或查询销售事实、批次、幂等、revision 或审计状态；
- 在 Django reader/writer 异常时把销售请求改道 D1；
- 接受浏览器自报角色、scope 或内部签名头；
- 读写销售 R2 对象，或将分片签收成功当成销售导入成功。

销售写入所有权任一时刻只能有一个。禁止长期双写，也禁止用“先写 D1、异步补 PostgreSQL”作为切换后的正常路径。

## 2. 必须保持的业务契约

迁移不能改变既有业务口径：

- 业务时区为 `Asia/Shanghai`，日期范围使用左闭右开区间；
- 金额以人民币分存储和传输，净销售额包含负值退款；
- 大毛利率为 `(分摊后金额合计 - 货品成本合计) / 分摊后金额合计`；
- `刷刷仓` 继续从销售、库存和成本分析中排除；
- 店铺身份至少使用 `platform + shop_name`，存在 channel 时同时核验；
- 幂等身份基于业务域、精确业务范围及解析/清洗/过滤后的完整规范化内容；文件名、行序和工作簿元数据不能单独决定重复；
- 事实、批次、范围状态、revision、幂等审计和 owner 提交栅栏必须在同一 PostgreSQL 事务内发布；
- 失败、接管、迟到 owner、空集合和内容变化仍须遵守仓库 `AGENTS.md` 的导入安全规则。

## 3. 目标拓扑

```text
浏览器
  │
  ▼
Cloudflare Worker
  ├─ 公开鉴权 / principal HMAC / Excel 解析 / 分片请求边界
  ├──────────────► Django reader 127.0.0.1:8001 ─┐
  └──────────────► Django writer 127.0.0.1:8002 ─┤
                                                  ▼
                                      PostgreSQL 127.0.0.1:5432
                                                  ▲
ERP 导入 ─► D1 ERP 主数据 + ERP-only outbox ─► ERP bridge
```

图中不存在销售读写到 D1 的通路。ERP bridge 是独立进程、独立凭据和独立 checkpoint；除按 ERP 映射更新现有 `sales_order_lines.resolved_category` 派生分类外，不得混入销售事实写入或销售事件消费。该例外不能新增/删除销售事实，也不能修改金额、成本、销量、`gross_profit`、其他销售字段或批次。

## 4. 本机服务与最小权限

| 服务 | 地址 | 运行角色 | 最小权限 |
| --- | --- | --- | --- |
| PostgreSQL 17 | `127.0.0.1:5432` | — | 仅回环监听 |
| Django reader | `127.0.0.1:8001` | `teruisi_sales_reader` | 只读销售查询对象和 ERP 参照副本 |
| Django writer | `127.0.0.1:8002` | `teruisi_sales_writer` | 写销售域对象；不可写 ERP 表、ERP revision 或 checkpoint |
| ERP bridge | 后台进程 | `teruisi_erp_reference_sync` | 写 ERP 参照表、ERP revision/checkpoint；销售域只可更新现有 `sales_order_lines.resolved_category` 派生分类 |

迁移 owner 只在 schema migration、显式授权、REVOKE 和 RLS policy 管理期间使用，不得作为 reader、writer 或 bridge 的运行身份。三种运行角色必须使用独立 DPAPI 密文；配置、日志、进程参数和审计不得出现明文密码或完整连接串。

授权必须从 `PUBLIC` 和历史角色的隐式权限开始收紧，再显式授予所需 schema/table/sequence 权限。销售 revision 与 ERP revision/checkpoint 应通过 RLS policy 约束各自可见、可写的数据集，不能只依赖 Django 代码。

## 5. 切换门禁与顺序

### 5.1 切换前冻结

进入变更窗口后先停止销售导入和所有可能创建销售写入的自动化，等待当前导入、上传、暂存、租约和后台任务进入可证明的终态。不得在存在 `processing`、未决点击、未发布暂存或所有权不明任务时切换。

随后取得并校验：

1. D1 切换前备份，包含销售事实、批次、幂等、尝试审计、revision 和写入所有权状态；
2. PostgreSQL 切换前备份或可恢复基线；
3. 两份备份的 SHA-256、字节数、创建时间、来源版本和只读恢复验证；
4. 当前应用 commit、迁移版本、配置版本和批准的 cutover ID。

备份是审计与灾难恢复材料，不等于允许把销售服务重新指向 D1。

### 5.2 历史数据与审计迁移

受控的一次性迁移必须覆盖完整销售域，而不是只复制查询所需事实：

- 销售事实和业务键；
- 导入批次、覆盖范围和动态 revision；
- 原文件 SHA-256、规范化业务内容指纹与解析格式版本；
- 每一次成功、失败、拒绝、重复、接管和恢复尝试的审计；
- 上传/分片/暂存的终态元数据及其来源关系；
- 范围状态令牌、幂等头、锁与 owner fencing 证据；
- 与销售分析相关且需留存的成本来源、排除规则和映射异常。

迁移必须用同一解析与规范化路径核对源/目标业务摘要，并至少比较：动态行数、业务键集合、分域摘要、金额/销量聚合、退款、最早/最晚业务日期、批次数、尝试审计数、范围状态和 revision。任何差异都必须在切换前解释并形成脱敏证据，不能把历史验收数字硬编码成通过条件。

### 5.3 单写所有权切换

跨 D1 与 PostgreSQL 的所有权切换不是单个数据库事务，必须使用批准的 cutover ID 和失败关闭状态机：

1. 两端 schema、代码版本、数据摘要和审计迁移全部通过；
2. PostgreSQL 记录已准备但尚未激活的 cutover；
3. D1 销售写入所有权进入待切换状态，旧写入路径立即拒绝新请求；
4. 完成冻结窗口内的最终差异核验；
5. D1 将销售写入所有权写入不可回退的 PostgreSQL 终态；
6. PostgreSQL 用相同 cutover ID 激活销售写入；
7. 启动 reader/writer，Worker 仅路由到显式 reader/writer 地址；
8. 验收通过后，按审批计划退役 D1 销售 schema、binding 和代码路径。

步骤 3 中“批准的 R2 cleanup + D1 `pending`”事务一旦提交即跨过不可回退点（PNR）。在此之前、D1 仍为 owner 且 cleanup 未提交时，才可按批准流程结束维护窗口并调查；跨过 PNR 后禁止把 `pending` 改回 `d1`，也禁止换用另一个 cutover ID，只能以原 cutover ID 前向恢复：完成最终核验、将 D1 推进到 `postgresql` 终态，再激活 PostgreSQL。终态后的故障按第 10 节从 PostgreSQL 和兼容应用版本恢复。

D1 销售对象退役是不可逆的独立变更：必须在完整系统验收、备份恢复演练和观察期通过后执行，并生成删除对象清单、DDL 结果、代码扫描结果和审批记录。不能把“路由已切换”当成“D1 已退役”。

## 6. ERP-only outbox bridge

ERP 主数据继续由 D1 维护。ERP bridge 必须顺序校验 source epoch、outbox sequence、来源批次、规范摘要和 ERP revision，并在单一 PostgreSQL 事务内原子发布 ERP 参照表、目标 revision 与 checkpoint；需要把 ERP 分类映射应用到销售明细时，只能更新已有行的 `sales_order_lines.resolved_category` 派生列。

首次 checkpoint 只能在以下条件全部满足时建立：

- D1 ERP 基线与 PostgreSQL ERP 基线已按行数、业务键和摘要核验一致；
- D1 outbox head、source epoch、revision 与计划写入的 checkpoint 一致；
- 没有缺口、重复、乱序或来源文件变化；
- 使用独立 `teruisi_erp_reference_sync` 角色；
- 操作者明确执行初始化动作并保存证据。

`InitializeErpReference` 只绑定已经验证相同的基线，不负责把缺失基线复制进 PostgreSQL。

正常 watch 在没有新事件时仍刷新心跳。受控状态检查必须同时满足 `caught_up=true`、checkpoint 等于当前 head、revision 一致、心跳在阈值内，并且启动后观察到一条新的 caught-up 心跳。例如：

```powershell
cd backend
python manage.py sync_erp_reference --source "<ERP D1 路径>" --status --max-age-seconds 60
```

缺口、乱序、摘要不符、revision 不符、心跳陈旧、source epoch 变化、未登记 ERP 进程或 bridge 越权均须失败关闭。reader readiness 也必须因 ERP checkpoint 不新鲜而失败；bridge 不得跳过事件、回写 D1 或承载销售事实同步。它唯一允许的销售表更新是按 ERP 映射回填现有 `sales_order_lines.resolved_category`，不得新增/删除事实或改动金额、成本、销量、`gross_profit`、其他销售字段和批次。

## 7. 新环境重建与受控升级骨架

当前本机已经完成第 13 节记录的销售域终态切换。本节保留给新环境重建、灾难恢复或后续受控升级；它不是重复执行本次不可逆切换的指令。所有占位值仍须在现场通过安全渠道提供。

```powershell
$repo = "<运营管理系统仓库根目录>"
$sourceTool = Join-Path $repo "tools\django-local-service.ps1"
$runtimeTool = "D:\teruisi-runtime\django-sales\app\tools\django-local-service.ps1"
$erpSourceD1 = "<经核验的 ERP D1 路径>"

& $sourceTool Stop
& $sourceTool Configure -ErpSourceD1 $erpSourceD1
& $sourceTool DeployApp
& $sourceTool HardenAcl
& $runtimeTool ProvisionErpRole
```

`Configure`、`DeployApp`、`RollbackApp` 均要求 PostgreSQL、reader、writer 和 ERP bridge 全部停止。脚本必须额外通过 CIM 检测命令行匹配的未登记 ERP 同步进程；发现后失败关闭，不能只相信 pid 文件。

在 ERP 基线确证相同后：

```powershell
& $runtimeTool InitializeErpReference
```

完成第 5 节的数据与单写所有权门禁后才可启动：

```powershell
& $runtimeTool Start
& $runtimeTool Status
```

启动顺序固定为：PostgreSQL → migration/grant/RLS 校验 → 销售写入所有权校验 → ERP 一次追平 → ERP watch 与启动后新心跳 → reader → writer。任何步骤失败都必须停止已启动的受控进程并清理本轮孤儿，不能留下部分可服务状态。

## 8. 备份与证据

每次切换应使用唯一 cutover ID，并在受控备份目录保存至少以下材料：

- D1 切换前只读副本及其 SHA-256；
- PostgreSQL base backup、逻辑备份或可验证的 PITR 起点；
- D1 与 PostgreSQL schema 清单、迁移版本和授权/RLS 快照；
- 历史事实、批次、幂等与尝试审计的动态计数和摘要；
- ERP source epoch、head、checkpoint、revision、摘要和新鲜心跳；
- 切换状态机每一步的 cutover ID、时间、操作者和结果；
- API、导入、性能、权限、恢复和 D1 退役测试结果。

证据不得包含密码、token、完整连接串、DPAPI 明文、原始客户文件或不必要的业务明细。任何恢复演练都应在隔离位置只读验证，不能覆盖运行数据。

## 9. 系统验收矩阵

### 9.1 API 与业务口径

- 销售总览、品类分析、明细和消费者查询的状态码、JSON、排序、分页和响应上限符合契约；
- 27 天、366 天、月末、跨年、退款、空结果、未知店铺、`刷刷仓` 排除和分母为零场景通过；
- 金额、销量、订单毛利与大毛利率逐项比对；
- principal 的 viewer/analyst/operator/admin 权限、店铺 scope、过期签名、篡改签名和重放全部失败关闭。

### 9.2 写入、幂等与并发

- Excel 解析由公开 Worker 的薄适配完成，原始分片字节、上传/暂存、提交、校验和落库回查全部走 Django writer/PostgreSQL；销售生产路径不得读写 R2；
- 精确重复返回既有结果但新增本次重复尝试审计；
- 同范围内容变化进行原子替换，被移除旧行不残留；
- 冲突重复、零行、签名失败、解析失败和范围错误不创建事实或抢占范围状态；
- owner fencing、30 分钟无进展接管、迟到 owner、响应丢失重试和并发导入通过；
- reader 只读、writer 不能写 ERP、ERP bridge 仅能写 ERP 对象及现有 `sales_order_lines.resolved_category`、并拒绝其他销售字段/批次/事实新增删除的数据库级测试通过。

### 9.3 ERP 与健康检查

- ERP bridge 从基线到 head 顺序追平，`caught_up=true`；
- 无事件时心跳持续更新，停止 bridge 后 readiness 在阈值内转为失败；
- outbox 缺口、重复、乱序、摘要/revision/source epoch 不符均失败关闭且不产生部分发布；
- `Status` 同时显示 PostgreSQL、reader、writer、ERP 进程、checkpoint 和心跳状态；
- 启动失败会清理本轮孤儿，未登记 ERP 进程会阻止配置、部署、回滚和启动。

### 9.4 性能与恢复

- 27 天和 366 天查询在批准的 p95/p99、超时和并发预算内；
- writer 大文件、分片恢复和并发写入不阻塞 reader 超出预算；
- PostgreSQL 备份恢复/PITR、DPAPI 凭据恢复、ERP bridge 续接和代码包回滚完成演练；
- Worker 连接超时、非 JSON、响应超限、reader/writer 不可用时明确报错，不访问 D1 销售数据。

### 9.5 D1 销售退役

- 当前运行仓库与部署包不得存在可执行的销售 D1 binding、事实 SQL、读写路由或兼容回退分支；允许保留历史迁移工具、`0092` 墓碑/退役收据/永久 guard、审计证据和隔离测试夹具，但它们不得被生产运行路径调用或恢复销售 D1 权威；
- D1 schema 检查确认批准清单中的销售表、索引、触发器和 outbox 已退役，而 ERP 表与 ERP-only outbox 完整；
- 公开销售读写只依赖显式 Django reader/writer 地址；
- `0092_sales_domain_retirement.sql` 继续是受控操作者专用迁移且不进入 Drizzle journal；在建立并审计退役后的新 schema baseline 前，普通 `npm run db:generate` 必须失败关闭；
- 退役后的整套系统测试、备份恢复证据和观察期指标通过。

## 10. 回滚与恢复边界

只有在 D1 仍为 `d1` owner、批准的 R2 cleanup 未提交且状态机证明两端均无新写入时，才可按批准流程结束维护窗口并调查。`cleanup + D1 pending` 一旦提交即为 PNR：不得取消、不得将 D1 改回 `d1`、不得更换 cutover ID；故障必须以同一 cutover ID 前向续接到 D1 `postgresql` 终态及 PostgreSQL `active`。不得在状态不明时猜测写入所有者。

切换终态后：

- 不支持将销售读写返回 D1；
- `RollbackApp` 只交换 current/previous 应用包，要求整套服务停止并验证 manifest；
- `RollbackApp` 不改变 PostgreSQL schema、销售数据、cutover ID、写入所有权或 D1 退役状态；
- 只有 previous 代码与现有 schema/数据契约兼容时才允许代码回滚；
- 数据故障使用 PostgreSQL 备份/WAL/PITR 或经审批的前向修复；
- ERP 故障继续以 D1 为权威，修复 bridge/checkpoint 后顺序续接，不影响销售写入所有权。

代码回滚命令：

```powershell
& "D:\teruisi-runtime\django-sales\app\tools\django-local-service.ps1" Stop
& "D:\teruisi-runtime\django-sales\app\tools\django-local-service.ps1" RollbackApp
```

回滚后仍需重新执行启动、状态、权限、ERP 新鲜度和关键 API 验收。

## 11. 新环境执行与验收清单（模板）

以下复选框用于下一台主机、灾难重建或新的独立切换，不是当前本机的未完成清单。当前本机已完成事实以第 13 节的不可变证据为准；任何新执行仍必须由实际操作者填写，文件提交、测试通过或代码合并不能自动勾选。

### 11.1 变更前

- [ ] 变更单/cutover ID：`________________`
- [ ] 批准人、操作者、开始时间：`________________`
- [ ] 当前 commit、部署包 manifest、数据库迁移版本已记录
- [ ] reader/writer/ERP bridge/PostgreSQL 与未登记 ERP 进程均已停止并核验
- [ ] 销售导入、上传、暂存、租约和自动化均无未决工作
- [ ] D1 备份已完成，SHA-256/字节数/只读恢复结果：`________________`
- [ ] PostgreSQL 备份或 PITR 起点已完成并验证：`________________`

### 11.2 数据与权限

- [ ] 销售事实动态行数、业务摘要与日期范围：`________________`
- [ ] 批次、幂等指纹、尝试审计、范围状态动态计数与摘要：`________________`
- [ ] 退款、金额、销量、毛利、店铺/channel 和 `刷刷仓` 排除比对通过
- [ ] 历史失败/拒绝/重复/接管审计完整迁移
- [ ] 三个运行角色已独立创建，DPAPI vault 和 ACL 已核验
- [ ] `REVOKE`、显式 grants、sequence 权限和 RLS policy 已由数据库级测试验证

### 11.3 ERP

- [ ] D1/PG ERP 基线行数、业务键与摘要一致：`________________`
- [ ] source epoch、head、revision 与首次 checkpoint：`________________`
- [ ] `ProvisionErpRole` 与 `InitializeErpReference` 已成功留证
- [ ] ERP bridge 已追平，启动后新心跳时间和 `caught_up` 证据：`________________`
- [ ] 缺口/乱序/摘要/revision/source epoch/陈旧心跳负向测试通过

### 11.4 单写切换与启动

- [ ] PostgreSQL cutover 已准备但未提前激活
- [ ] D1 旧销售写入已冻结，最终差异为零
- [ ] 已记录 `cleanup + D1 pending` 的 PNR；失败恢复只允许相同 cutover ID 前向续接，DB 与 operator 均拒绝 `pending→d1`
- [ ] D1 写入所有权已用相同 cutover ID 进入 PostgreSQL 终态
- [ ] PostgreSQL 销售写入已激活且无双写窗口
- [ ] 本机 `Start` 成功，启动顺序和失败清孤儿证据已核验
- [ ] `Status` 显示 PG/reader/writer/ERP/checkpoint/心跳全部健康
- [ ] Worker 只配置显式 reader `8001` 与 writer `8002`，无销售 D1 路径

### 11.5 系统验收与退役

- [ ] 读 API、消费者查询、真实 principal/scope 和业务口径测试通过
- [ ] 导入、分片、幂等、审计、并发、owner fencing 与落库回查通过
- [ ] 27 天/366 天性能和并发门禁通过，结果：`________________`
- [ ] PostgreSQL 恢复、ERP 续接和代码包回滚演练通过
- [ ] D1 销售 schema/binding/代码退役已单独审批
- [ ] D1 退役对象清单、DDL、代码扫描和退役后系统测试已留证
- [ ] 观察期结束时间、指标与最终批准人：`________________`

## 12. 新环境验收记录模板

```text
cutover ID:
执行时间（Asia/Shanghai）:
应用 commit / manifest:
D1 备份及 SHA-256:
PostgreSQL 备份 / PITR 起点:
销售事实行数 / 日期范围 / 摘要:
批次 / 幂等 / 尝试审计计数与摘要:
销售 revision:
ERP 行数 / revision / source epoch / head / checkpoint:
reader / writer / ERP bridge 状态:
权限与 RLS 结果:
API / 导入 / 性能 / 并发 / 恢复结果:
D1 销售退役清单与验证:
遗留风险:
验收结论:
批准人:
```

## 13. 当前本机终态切换记录

### 13.1 范围与数据水位

- cutover ID：`sales-pg-20260829T204417Z-d9896e904d8092cb`；
- 受控 D1 退役迁移：`0092_sales_domain_retirement.sql`，SHA-256 `f981a62efd0515a7f64dd9f174151b8cfeb0c4b071d8236c481b5459761a3b8f`；
- 正式切换快照：销售事实 `572,015` 条、销售批次 `88` 个、ERP 参照 `8,443` 条、动态 revision `8:5`；
- 销售事实业务日期覆盖：`2025-01-01` 至 `2026-08-27`；
- 终态：销售事实、批次、导入幂等与尝试审计、原始分片字节、上传/暂存状态、revision、查询和分析均以 PostgreSQL 为唯一权威；D1 销售事实、批次、上传、缓存、投影 outbox 和 authority 对象已退役，销售 R2 路径也退出生产代码，只保留防复活所需的只读 tombstone、retirement receipt、永久 guard、历史工具与测试夹具；
- ERP 主数据仍以 D1 为权威并经 ERP-only bridge 进入 PostgreSQL；其他业务域继续遵守各自现行边界。

上述行数、日期和 revision 是本次切换时的验收快照，不是代码常量，也不能代替以后查询时的动态新鲜度检查。本记录只证明当前 Windows 主机的销售域终态，不代表远程生产、高可用、整个 D1/R2 或其他业务域已经迁移。

### 13.2 备份、恢复与不可变证据

正式备份保存在 `D:\teruisi-runtime\django-sales\backups\sales-cutover-98c2727f4bb3fcc01a43e668`，保留以下 SHA-256：

| 证据 | 标识或 SHA-256 |
| --- | --- |
| 备份 manifest | `b665eb7109b66127dbcd1507fe569910f80ab6a86ac085fed70b086cd6392901` |
| R2 manifest | `4417e585fe8875ffb4e76622e419d01585a113c87bf4c45b9633515e2740d339` |
| D1 一致性备份 | `af89bdca0f38347b7ae4bd551cb64945403b59dd7c0b3396a88a5588ee56b92b` |
| PostgreSQL 逻辑备份 | `925bb98bba520ead274616b9046b236c5fc3dfceeecaaafc8c29eee772cf7d52` |
| 成功恢复演练 | run ID `5f2d0669317c`；结果 SHA-256 `f3b8f1e2efa59f50394e9f3efa1dc53b4adaad6428e104c6f3d84a1466ffb935` |
| cutover attestation 文件 | `14a5f315b9b3ae3cb1e6b2e8bff37970f9abfcc659e705e5f4e55a227c9b5b31` |
| cutover attestation payload | `60dafe8b443dcc3ec6ad7db610c6010b26cd8633a2d3e1184b677219b4b9f0ae` |
| forward-recovery 证据 | `11d2e0d9e2c201887b95bae80fe6af6cc5724e44853a9bcbd13b04a29dba1244` |
| D1 retirement 原始审计 | `5ccc4b15e901bf668303630b18e50659fed064a3e7404af8479b72c2c7afad4a` |

成功正式备份、恢复演练、attestation、forward-recovery 和 retirement 证据不得因磁盘清理而删除。清理策略只可删除失败或未被成功清单引用的旧备份，并且必须通过受控工具核验精确目标。

本次磁盘治理已通过受控工具删除失败恢复演练的大体积 payload，只保留小型审计元数据；正式成功备份和约 17 GiB 的成功恢复演练材料继续保留。该清理没有删除任何被成功 manifest、attestation、forward-recovery 或 retirement 证据引用的材料。

数据库权限审计已确认 reader 为只读且不能 DML/DDL 或访问 authority，writer 只能写销售域且不能写 authority/attestation/ERP/checkpoint；ERP bridge 只能写 ERP 参照、ERP revision/checkpoint 及现有 `sales_order_lines.resolved_category`，不能新增或删除销售事实，也不能修改金额、成本、销量、`gross_profit`、其他销售字段或批次。三种运行身份均无额外特权角色或成员关系。切换快照中的 processing、upload、staged、scope、attempt 和 write receipt 未决计数均为 `0`。

### 13.3 最终运行发布与复核补录

2026-08-30 已完成本机最终部署与复核：

- Django 应用部署 fingerprint 为 `774936c5efe8365a370dc6b29a6110a3e97d3868a1a04e1ec879ff16a84f30c7`，`deployment.json` 原始 SHA-256 为 `4c828b2c740bcc41fe0f66920246a154d53a62c0335ded9574e1f702cb121cfd`，共 `1,652` 个应用文件。PostgreSQL、Django reader/writer、ERP bridge、runtime ACL 和登录启动项均通过受控 `Status`；reader/writer readiness 为 200。
- 截至该轮复核的 Worker effective head 为 `20260830T020314Z-16b6c1b89ed012a9`，manifest SHA-256 `05574809aa2435c4b80032846e75d08c5d668ffdb370bb2d7bb538fdc223606b`，guard receipt `b7868197ac69a7599aed7930b58d4affaa4b799f3f2a946d0e2fee7184232a20`。正式 plan 为 `7e3f6d2cd0b220489bd0bbd7c235986687f8d886cc1f71ef699b83898e81c7ea`，successor `228e2c11dedf7c629a85fc0d82c03cd152ff43c87f4e450c625eb3cd6a81390e`，consumption `284aa6f909ab3ab113145acc3fb98e7a9bdb118afcaf89b312e781809c476417`，startup binding `98b76c730ad355d718f1a0863c6d86c1d09692006c3ddcb832b826b1a2b4fda5`。当时 effective-head 链共有 `3` 个 successor，chain state `ce75e7a153e467d74f096cc938b18bd96ab2b619979653caead25f1427450316`；后续 head 见第 13.4 节。
- Worker 已在 PS5 与 pwsh 下回读为 `exact_release`，只监听 `127.0.0.1:3000`；helper 只监听 `127.0.0.1:5791` 且为 `ready/idle`。一次完整 Stop → 未放宽 full Verify → Start 回归通过并保持在线。首次和第二次启动后 `node_modules` 都保持 `29,932` 文件、SHA-256 `be8a030751e67e048becd5a5e52b09d132b574091632456e5498665832356b2c`，release 内无 `.mf`；Miniflare 缓存只写入 runtime 的 `cache\miniflare\cf.json`。
- 启动事故根因及修复包括：pwsh 把 ISO JSON 字符串自动转为 `DateTime` 后触发递归规范化；supervisor prelaunch 递归启动 PowerShell `Status`，使其子探针被进程树策略误判；controller 写 receipt 与 supervisor 约 2 秒等待存在竞态；Miniflare 默认向 immutable `node_modules/.mf/cf.json` 写缓存。修复后使用保留 ISO 字符串的解析、Node 直接验证 create-only canonical receipt、15 秒有界握手与外层 CIM 二次核验，并把缓存固定到 release 外且校验重解析点/硬链接。旧缓存事故已隔离到 create-only incidents 目录，审计 SHA-256 为 `234d982349b585647d1ff3e27879007242aaf575bc479650fbafcdbfb8def1b6`，没有删除证据或放宽 verifier。
- 最终前端/Worker 全量回归：feature build 后 `1,328/1,328`，combined build 后 `1,618` 通过、`4` 项预期跳过；两个 rendered HTML 套件均 `19/19`。Django `143/143`，`manage.py check` 和 `makemigrations --check --dry-run` 均通过。Worker release/rotation/PS5/pwsh 组合测试在 feature 与 combined 各 `56/56`，独立复审最终为 P0/P1/P2 全 `0`。
- 公开 API 冒烟确认 `/api/auth/me`、销售总览、品类分析和品类明细均为 200、`no-store`、无 Set-Cookie 或内部 principal header；销售响应的 data/source revision 均为 `8:5`。缺 category 返回 `400 invalid_request`，无签名直连 Django 返回 `401 authentication_required`，非回环 Host/DNS 重绑定返回 404。
- 性能门禁使用真实 Django reader、四个视图（full/dashboard/category/category-detail）和精确品类。366 天冷启动、并发 `1`、`1` 轮共 4 请求，整体 p95/p99/max 均为 `9,213.08 ms`，低于 10 秒冷启动上限；27 天热态、并发 `8`、`20` 轮共 640 请求，p95 `55.46 ms`、p99 `1,112.17 ms`、max `1,132.98 ms`；366 天热态同样 640 请求，p95 `168.05 ms`、p99 `189.37 ms`、max `216.92 ms`。三组 revision 均为 `8:5`，无阈值违规。
- 最终现场仍为 572,015 条销售事实、88 个销售批次、8,443 条 ERP 参照和 revision `8:5`；所有 processing/upload/staged/scope/attempt/write receipt 未决数为 0。以上只是验收时动态水位，不得固化为业务常量。
- 本次只恢复本机 Worker/helper 与销售域运行时，没有触发任何天猫 A/B/C/P/M 业务节点；n8n `5678` 始终保持原进程在线。该完成状态仅覆盖当前 Windows 主机和销售域，不代表远程生产、高可用、任务队列或其他业务域已经迁移。

### 13.4 2026-08-31 销售所有 Tab 与最新发布复核

- 销售域 R2 清理审计 `sales-legacy-r2-cleanup-v1` 保持 `completed`：99 个销售上传会话元数据已纳入清理，92 个销售对象均逐一验证不存在，manifest ID 为 `3f2397a023e868c5398a61905b89217050acc34af8bc12e99acbbae2579d1a36`。该记录只证明销售域 R2 退出；市场图片、库存、工作流和其他未迁移范围继续使用全局 R2 binding。
- 财务后端已按独立 authority 正式切换到 PostgreSQL/Django，`TERUISI_DJANGO_FINANCE_MODE=django`，因此“销售分析”中的销售总览、渠道分析、品类分析、财务分析和目标设置五个 Tab 均已走 Django/PostgreSQL 权威。财务的独立 cutover、D1 guard 和 PNR 见 `docs/DJANGO_FINANCE_MIGRATION.md`；它不改变销售 cutover ID 或 ERP bridge 边界。
- 市场分析在销售消费者切换后的在线抽检暴露出严格缓存契约漏接 `salesRevision`，导致新鲜响应被错误拒绝为 503。修复后响应、前端类型和缓存校验统一携带该修订，定向 28 项测试通过，市场小分页接口恢复 200；没有修改市场事实或缓存 authority。
- 最新 Worker effective head 为 `20260830T201224Z-0efa51b94c6abe47`，manifest SHA-256 `b47d9ac948d7fd9929596aadd9ab9e9467cf51105c9a049bde7365309c434509`，guard receipt `24af76ebf964fb833bd1ccfb92c1a45e34ebf905522a7a599dd9164d62a7978b`。批准 plan 为 `277045fe563e05f514c3f83a89eca3112b18b2071ccb96f8e77d74de3aa35c7d`，successor `7c874420e3436338e3f0214094eda4a1d63cf457c898ef982dad85e3477a18ca`，consumption `2c7dde0b93155f2e2ee31e29d514e5900a017a9b1f4d02cc5f3f54d61b553140`，startup binding `26d2891f5b08a0694adaccace8584db01fceaaac1f090665705aeb7a272f68d8`。effective-head 链共有 `9` 个 successor，chain state 为 `c6a0ca8cab496003b7771ad7b869936bcc8c48e03db4f6a9ab3db5e838b48881`；完整 Verify 在进程在线时返回 `exact_release`。
- 发布后销售 summary（同时支撑总览与渠道）、品类分析、品类明细、财务分析、目标 items/options、销售与财务导入历史、销售数据健康均为 200；销售数据健康返回 `sales_single_write`。鉴权、库存、网店、市场、客服、工作流和商品汇总代表接口也均为 200，helper 为 `ready/idle`，四个 Django reader/writer readiness 均为 ready。
- 最终源码验证为 Worker/领域测试共 `1,680` 项，其中 `1,676` 通过、`4` 项已退役 D1 销售路径预期跳过；rendered HTML `19/19`，Django `sales`、`erp_reference`、`finance` 共 `161/161`，`manage.py check` 与 `makemigrations --check --dry-run` 通过；Lint 为 `0` error、`9` 个既有 warning。
