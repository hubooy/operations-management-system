# 商品经营 Django/PostgreSQL 重构与迁移

## 1. 当前状态

2026-09-02，本机商品经营板块已完成 Django/PostgreSQL 正式单写切换、完整垂直链路系统测试、D1 终态退役和商品路径 R2 下线。现有 React/Next.js 页面和公开 API 契约保留，未改写为 Django template。正式 cutover ID 为 `products-pg-20260901T164758Z-1c636a3a0f564bc9`，authority epoch 为 `82834127-5232-41fd-8e16-fa5c02bc3e8d`。

PostgreSQL 现为 SKU 快递费率、批次、内容指纹、scope head、尝试审计、原始分片、revision、库存只读投影和商品经营读写的唯一权威。旧 D1 商品对象只保留 3 个空 tombstone view、18 个永久 guard 和 completed retirement receipt；商品生产路径不读写 R2。全局 D1/R2 binding 仍供库存、ERP、市场图片及其他业务域使用，不在本次下线范围内。切换已跨过 PNR，不得恢复 D1、`legacy`、`shadow`、双写或反向迁移。

机器可读的正式切换证据见 [`evidence/products-django-cutover-20260902.json`](evidence/products-django-cutover-20260902.json)；切换前隔离演练证据仍保留在 [`evidence/products-django-mirror-20260902.json`](evidence/products-django-mirror-20260902.json)。

## 2. 领域边界

商品经营 Django app 位于 `backend/products/`，负责：

- 商品经营汇总、分页和 snapshot token；
- SKU 快递费率全量权威集合、批次、尝试、内容指纹、scope head、revision 和写请求 receipt；
- PostgreSQL 原始分片、owner fencing、超时接管和完成结果；
- 从 D1 库存权威源接收的版本化商品库存投影；
- AI 助理和全局搜索的有界只读 consumer；
- D1 历史迁移、单写 authority 和终态退役证据。

商品经营汇总只组合以下受控来源：

| 数据 | 权威来源 | 商品经营中的用途 |
| --- | --- | --- |
| 销售事实 | Django/PostgreSQL `sales` | 净销量、销售额、退款、成本、费用、毛利和店铺表现 |
| ERP 货品参照 | D1 权威，经 ERP-only bridge 同步到 PostgreSQL | 货品名称、品牌、供应商、规格和品类 |
| SKU 快递费率 | `products` PostgreSQL | 商品经营展示与导入历史 |
| 库存事实 | D1 `inventory` 继续为唯一权威 | 每次成功或 duplicate 库存导入后，薄 Worker 把最新完整快照同步为 PostgreSQL 只读投影 |

库存域本次不迁移。投影固定排除 `刷刷仓`，按规范化货品代码聚合，最多 20,000 个货品、每页 1,000 行；`begin_sync → stage_page → activate_sync` 全程绑定 owner、来源批次、快照日、总行数和完整规范摘要。Django 在激活时自行重算全部规范 SHA-256，不信任调用方声明。同步失败只让商品经营库存视图失败关闭，不改变 D1 库存事实或其他业务域。

公开 Worker 只保留真实 `requireAppPrincipal()`、无范围账号门禁、XLSX 解析、HMAC principal 信封、请求/响应上限和薄转发。商品经营在线路径、AI 和全局搜索都没有 D1/R2 fallback。PostgreSQL 原始分片最多 20 MiB，固定 1 MiB/片；分片内容、摘要和生命周期均在 PostgreSQL，不使用 R2。

## 3. 业务与一致性契约

- 业务时区固定为 `Asia/Shanghai`；查询日期使用左闭右开区间。
- 金额使用人民币分。净销售额保留退款负值；退款率以退款金额/正向销售额重算。
- 商品经营毛利率沿用当前商品经营字段口径；销售分析的“大毛利率”权威定义仍由销售域保持，不在前端复制口径。
- 默认统计周期为最近 30 天；支持最近 90 天、半年和显式自定义范围。分页必须携带同一完整查询返回的 64 位 snapshot token；销售 revision、商品 revision 或库存投影在分页期间变化时返回可重试冲突，不拼接跨版本页面。
- 快递费率解析仍固定读取工作表 `SKU累计` 的 B/M/Z/AA 列，以 `Z / M` 重算并交叉核验 AA；M 为 0 时为 0。负值和超过 100% 的值保留并告警，不截断。
- 快递费率是单一全量 scope。业务判重只使用规范化完整集合；文件名、工作簿元数据和行序变化不创建新批次，任一业务值或权威行集合变化都原子替换完整事实。
- 无效签名、范围不符、表头错误、空集合、重复规格冲突、请求 ID 复用、owner 失效、摘要不一致和权限不足全部失败关闭。预校验拒绝只写有界失败尝试，不创建事实批次或内容指纹。

## 4. API 与运行拓扑

| 地址 | 角色 | 数据库身份 |
| --- | --- | --- |
| `127.0.0.1:8041` | products reader | `teruisi_products_reader` |
| `127.0.0.1:8042` | products writer | `teruisi_products_writer` |

公开 API 保持不变：

- `GET /api/products/summary`
- `GET/POST /api/imports/product-shipping-rates`
- `POST/PUT /api/imports/product-shipping-rates/chunks`
- 库存导入成功后的内部库存投影同步

内部 Django 路径由固定 allowlist 控制，reader/writer 不共享端点。reader 只读销售、ERP 参照和商品查询表；writer 只拥有商品域精确表权限，不能写销售、ERP、财务、网店、市场或 D1 库存。writer 只有在 PostgreSQL `product_write_authority.status=postgres`、authority epoch 和 cutover ID 与进程环境完全一致时才 ready。

当前多领域本机部署要求 PostgreSQL `max_connections=100`。商品经营启动 operator 会在读取 authority 时同时执行 `SHOW max_connections`，低于 80 时拒绝启动 reader/writer；该门禁避免所有领域进程占满普通连接槽后把商品导入变成不确定的 503。

受控 runtime operator：

```powershell
$products = "D:\teruisi-runtime\django-sales\app\tools\django-products-service.ps1"

& $products -Action ConfigureCredentials
& $products -Action ProvisionRoles
& $products -Action Status -Json
```

凭据只保存到当前 Windows 用户绑定的 `secrets\products-credentials.dpapi.json`。`ConfigureCredentials`、`ProvisionRoles`、部署和 authority 操作都要求对应服务端口无监听并核验 runtime 身份。正式切换后，只有 `EnableStartup` 能创建绑定 authority epoch、cutover ID 和迁移 run 的 `products-service-enabled.json`；基础启动器看不到该文件时不得自动启动商品经营服务。

不可变 Worker release 必须显式配置：

```text
TERUISI_DJANGO_PRODUCTS_READER_BASE_URL=http://127.0.0.1:8041
TERUISI_DJANGO_PRODUCTS_WRITER_BASE_URL=http://127.0.0.1:8042
TERUISI_DJANGO_INTERNAL_SECRET=<与 Django runtime 相同的受控密钥>
```

密钥、数据库口令和真实数据不得写入仓库、命令历史或审计文件。

## 5. 历史迁移工具

`drizzle/0099_product_write_authority.sql` 是 behavior-neutral 的 D1 authority 准备迁移；`drizzle/0100_product_domain_retirement.sql` 是跨过 PNR 后的终态退役迁移。两者都是 operator-only，故意不进入普通 Drizzle journal，不能由 `db:generate`、应用启动或普通部署自动执行。当前正式域已完成二者，以下命令只作为历史审计说明，不得用于重新建立 D1 authority。

迁移命令采用严格的 `plan → apply → verify`：

```powershell
cd D:\teruisi-runtime\django-sales\app\backend

python manage.py migrate_products_from_d1 `
  --source <权威D1.sqlite> --mode plan

python manage.py migrate_products_from_d1 `
  --source <权威D1.sqlite> --mode apply `
  --approve-run-id <products-plan-id>

python manage.py migrate_products_from_d1 `
  --source <权威D1.sqlite> --mode verify `
  --verify-run-id <products-apply-id>
```

plan 只读源；apply 要求 PostgreSQL 商品目标为空、authority 仍为 `d1`、没有 receipt/上传/投影残留，并在单一事务中写入；verify 独立复算源/目标完整摘要并把已验证 apply run 绑定到 PostgreSQL authority。源路径、事实集合、行数或摘要在任一步变化都会拒绝。

历史迁移包含当前 SKU 快递费率批次、费率事实、尝试、指纹、scope head，以及最新完成库存批次的商品级投影。旧费率浮点值确定性转换为 12 位十进制定点；当前权威批次按新规范重算内容指纹，原历史哈希只保存在迁移 manifest，不伪装成新导入。

任何仍含 `sku-shipping-rates:*` 分片对象键的 D1 源都会在 plan 阶段拒绝。对象键必须在 D1 仍持有写权时按原上传清理契约受控清理；进入 `pending` 后不允许绕过 authority guard 删除。

## 6. 2026-09-02 切换前隔离真实数据迁移与系统测试

正式切换前的隔离演练环境没有可调用的 `teruisi_operations` MCP，因此该阶段只在隔离环境中以当时的本机权威 D1 只读事务作为商品/库存源，并把最近一份已验证正式 PostgreSQL 备份恢复到独立 PostgreSQL 17.11 集群。表中“生产影响 false”只描述该次切换前演练，不描述随后已经完成的正式 cutover。

数据口径：没有平台、店铺、SKU 或渠道筛选；快递费率覆盖当前完整权威集合；库存使用 2026-08-31 最新完成快照并排除 `刷刷仓`；系统查询使用默认最近 30 天销售窗口；金额保持人民币分，销量保持销售域净销量定义。

| 项目 | 结果 |
| --- | --- |
| 隔离环境 | `127.0.0.1:55441/products_system_mirror`；任务完成后停止并清理 |
| PostgreSQL 基线 | 已验证备份 `daily-20260901T141558Z-a68cc9120c95`，创建于 2026-09-01 22:15:58（Asia/Shanghai） |
| 商品源截止 | 最新费率批次完成于 2026-08-27 13:22:15；3,630 条 |
| 库存源截止 | 快照日 2026-08-31，批次完成于 2026-09-01 06:15:38；排除 `刷刷仓` 后聚合 6,461 个货品 |
| 迁移数量 | batches 1；rates 3,630；fingerprints 1；attempts 1；inventory 6,461 |
| 源/目标摘要 | `e44d40245ef61b8e0231fbec2a44f466cf0709eb049c75182e2fdde16d050a6e`，完全一致 |
| 迁移 run | plan `products-plan-23e6f32b9d764faa95cffa703b5d96b1`；apply/verify `products-apply-b8c848908065455799845eaf9a42b410` |
| 读链路 | 完整汇总、分页 snapshot、一致性、销售/ERP/库存/快递费率连接和响应契约通过；实际销售商品 1,259 个，抽查页 10 行 |
| 写链路 | HMAC、业务 duplicate、同请求 replay、事实/revision 稳定、未签名 401 全部通过；测试写入位于外层回滚事务 |
| 终态门禁 | 旧商品分片对象键 0；D1 退役 plan/apply/duplicate、3 个 tombstone、18 个永久 guard 和库存域保留测试通过 |
| 生产影响 | `false` |

## 7. 正式切换验收与终态

本次正式切换覆盖 React 页面、公开 API、AI/global search consumer、快递费率直接与分片导入、库存投影、reader/writer、Worker release、启动链、备份、系统烟测和 D1 退役，没有保留长期双写或旧后端可达路径。

| 项目 | 正式结果 |
| --- | --- |
| cutover / authority | `products-pg-20260901T164758Z-1c636a3a0f564bc9` / `82834127-5232-41fd-8e16-fa5c02bc3e8d`；PNR 已跨过 |
| migration | plan `products-plan-bba00f4d1844443a85b24eea3ebaa716`；apply/verify `products-apply-4e42b55942d54e8e99a57ff4653d4371` |
| 完整摘要 | 源/目标均为 `e44d40245ef61b8e0231fbec2a44f466cf0709eb049c75182e2fdde16d050a6e` |
| 正式水位 | batches 1；rates 3,630；fingerprints 1；attempts 1；inventory 6,461；revision 1 |
| 正式服务 | `127.0.0.1:8041/8042` ready；authority 绑定启动 receipt 已启用 |
| 系统烟测 | `products-system-test-receipt-v1` 10/10 通过；SHA-256 `f8ac4b34cddd80296ee255821091079d2a710608a19684550d18b5a13609891b` |
| D1 退役 | plan `7b73be65424c19e3d9bcf3d601b9790bab4b7fe84b1ff20ea76c7898854372f8`；3 个空 tombstone；18 个永久 guard；`quick_check=ok` |
| D1 其他域保护 | 共享摘要 `277a73c006c72a0014efe7734ad73f104f0d77eb60cabb9046f598da8b5de6aa` 未变；库存/ERP 和其他域继续按各自契约运行 |
| 商品 R2 | 直接导入和 1 MiB 分片均由 PostgreSQL writer 处理；生产源码无 D1/R2 fallback；全局 R2 binding 保留给其他域 |
| Worker effective head | `20260901T192658Z-765c43c274a681d2`；manifest `994ab0ebe7459aa3806b4182e4d8337e7f4b5e0922a960d7911b31b3b7c3573b` |
| 正向导入复核 | 3,630 行真实 XLSX 的直接与分片路径均返回 `duplicate`；事实、revision、scope head 和批次不变；形成 2 条预期 duplicate 尝试审计 |
| 连接容量 | PostgreSQL `max_connections=100`；商品服务启动门禁为至少 80；所有领域在线时商品直接/分片复核通过 |
| 切换后备份 | `daily-20260901T200551Z-0087e9194573`；manifest `91981131be29aaa2844b26bd8c2db976ca265e510ab801b2624b87c502c615ea`；内容摘要 `e540009458e9f1704d1de2b0e07ef0c3f01e1379196aa383909088363082c1ee` |
| 隔离恢复 | rehearsal `2db116c73f84` / `127.0.0.1:55443`；恢复摘要一致；生产库未触碰；服务状态未改变；临时数据已清理 |

本次已完成的受控顺序（仅作审计记录，不得重放）：

1. 停止商品快递费率导入并短暂冻结库存导入；核验没有 product processing batch/attempt、scope owner、PostgreSQL receipt/上传、投影租约和 D1 旧商品分片对象键。
2. 停止 Worker 或以等价 activation fence 阻断旧商品写入口，生成正式 PostgreSQL 预备份和 D1 一致快照；安装并回读 operator-only `0099`。
3. 部署产品 Django schema 和代码，配置 DPAPI 凭据、最小权限角色；reader 可做影子核对，writer 仍必须保持停止。
4. 在冻结的同一权威 D1 路径执行第 5 节 plan/apply/verify，保存精确 run、源摘要和目标摘要。
5. 执行 D1 `pending`：

```powershell
python manage.py products_write_authority --source <权威D1.sqlite> `
  --prepare --approved-run-id <products-apply-id> --cutover-id <cutover-id>
```

   此时旧 D1 写入口已失败关闭。当时在跨过 PNR 前唯一可能的回退是同一 run/cutover 执行 `--abort-pending`，且 PostgreSQL authority 必须仍是 `d1`、两端保持静默；该窗口现已永久关闭。
6. 构建并验签只含 Django 商品路径的 Worker successor，复验 8041 reader、8042 writer 负向、权限、HMAC、超时、体积、snapshot 和无 D1/R2 fallback。
7. 激活 PostgreSQL：

```powershell
python manage.py products_write_authority --source <权威D1.sqlite> `
  --activate --approved-run-id <products-apply-id> --cutover-id <cutover-id>
```

   PostgreSQL 获得 `postgres` authority epoch 后即跨过 PNR，不得恢复 D1、legacy、shadow 或双写。
8. 启动 products reader/writer，激活不可变 Worker successor，执行公开汇总、直接导入、分片导入、库存投影、AI、全局搜索、权限负向和旧 D1 拒绝系统测试；生成 30 分钟内、严格字段集、绑定 Worker build/run/cutover/摘要的 `products-system-test-receipt-v1`。
9. 先生成 D1 退役 plan，再精确批准：

```powershell
python manage.py retire_products_d1 --source <权威D1.sqlite> `
  --cutover-id <cutover-id> --approved-run-id <products-apply-id> `
  --smoke-receipt <products-system-test-receipt.json>

python manage.py retire_products_d1 --source <权威D1.sqlite> `
  --cutover-id <cutover-id> --approved-run-id <products-apply-id> `
  --smoke-receipt <products-system-test-receipt.json> `
  --apply --approved-plan-id <plan-id> `
  --audit-output <products-retirement-audit.json>
```

10. `0100` 完成后必须回读 3 个空 tombstone view、18 个共享表永久 guard、completed retirement receipt，以及库存表、其他域共享导入行和其他域 retirement receipt 摘要未变。随后执行 `EnableStartup`、正式 PostgreSQL 备份和独立恢复演练，再恢复库存导入。

## 8. 恢复边界

正式 authority 已激活且 D1 已终态退役，`pending → d1` 回退窗口永久关闭。恢复只允许兼容代码、PostgreSQL 备份/WAL/PITR 或经审批的前向数据修复；不能把商品读取或写入重新指向 D1，也不能恢复旧 Worker 分支、R2 分片或双写。

D1 库存域仍是权威并不构成商品经营 D1 fallback。库存同步失败时应修复或重放同一版本化投影，不得让商品汇总直接读取 D1。正式备份、迁移 run、authority、smoke receipt、retirement plan/receipt、审计和恢复演练证据都必须长期保留。
