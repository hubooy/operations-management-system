# 库存管理 Django/PostgreSQL 重构与迁移

## 1. 当前状态

2026-09-03，本机库存管理板块已完成 Django/PostgreSQL 正式单写切换、完整垂直链路系统测试、D1 终态退役和库存 R2 路径下线。现有 React/Next.js 页面和公开 API 契约保持不变；公开 Worker 只保留真实鉴权、XLSX 解析、HMAC、请求体积/超时边界和薄适配。

正式 cutover ID 为 `inventory-pg-20260902T162501Z-c6f5c5af1d254c0d`，PostgreSQL authority epoch 为 `7cc50a33-91a6-4e2d-8253-1531c6de3a22`。切换已跨过 PNR：D1/R2、`legacy`、`shadow` 和反向迁移都不能作为库存恢复路径；恢复只允许 PostgreSQL 备份/WAL/PITR、兼容代码或经审批的前向修复。全局 D1/R2 binding 仍供 ERP、市场图片和其他业务域使用，不得因库存域退役而删除。

## 2. 终态边界

库存 Django app 位于 `backend/inventory/`，负责：

- 分仓库存和库龄事实、批次、内容指纹、尝试审计、scope head 与 revision；
- 备货计划、库存运营设置、请求幂等 receipt 和 PostgreSQL 原始分片；
- 库存总览、库龄分析、京东入仓监控和有界 consumer 查询；
- 从 D1 冻结副本执行 `plan -> apply -> verify` 的确定性历史迁移；
- 双侧 write authority、reader/writer readiness、备份证据和 D1 退役门禁。

库存 reader/writer 使用独立最小权限角色，正式监听地址固定为 `127.0.0.1:8051/8052`。reader 只读库存、销售需求和 ERP 参照所需对象；writer 只能维护库存域表，不能修改销售事实、ERP 主数据、财务、网店、市场、商品经营或其他领域权威。

商品经营继续把库存当作上游事实。切换后由库存 Django consumer 提供版本化完整投影；商品经营只保存其受控只读投影，不形成库存第二写入源。系统成本、AI 工具、全局搜索和库存执行事项也统一通过有界 Django consumer 读取，不得回查旧 D1 库存表。

## 3. 公开链路

```text
浏览器
  -> React/Next.js 原页面与同源 API
  -> Worker：真实 principal、权限/scope、XLSX 解析、HMAC、有界传输
  -> Django inventory reader/writer
  -> PostgreSQL：库存唯一权威事实、控制状态和审计
```

公开链路覆盖：

- `GET /api/inventory/overview`
- `GET /api/inventory/age-analysis`
- `GET /api/inventory/inbound-monitor`
- `GET/POST/PATCH /api/inventory/replenishment`
- `POST /api/inventory/work-items`
- `GET/POST /api/imports/inventory`
- `POST /api/imports/inventory/chunks`
- ERP 共享导入入口中仅 `source=inventory_age` 的库存分支
- `GET/PUT /api/settings` 中的库存运营设置
- 商品经营库存投影、系统成本、AI 和全局搜索 consumer

边缘失败时库存路径失败关闭，不读取 D1/R2 作为 fallback。`/api/inventory/import/` 的 `inv_selfop` 京东自营快照属于已经迁移的网店域，不是本次分仓库存事实路径。

## 4. 数据和导入口径

- 业务时区固定为 `Asia/Shanghai`，快照日期是日历日。
- 库存身份固定为“仓库 + 货品编码”；同一规范化文件出现重复身份时拒绝导入。
- 库龄身份固定为数据集、快照日、仓库和货品编码。
- `刷刷仓` 在解析、迁移、查询、投影、成本、AI 和搜索边界统一排除。
- 金额按人民币分保存；负库存是否允许由库存设置控制。明确填写的零固定成本按有效 0 元成本保存并参与库存及补货，缺失或负成本不得解释为零成本。
- 业务判重使用“领域 + 精确范围 + 清洗后的完整规范内容”，原文件 SHA-256 只用于签收和追踪。
- 通过校验的新快照在稳定 scope head 下原子替换精确日期事实；owner、generation、旧 state token 和 write authority 必须在同一提交事务内复验。
- 自动补货默认关闭。只有管理员明确启用、库存新鲜且全局质量门禁通过时，才可输出和落地精确补货计划。

## 5. 迁移工具与切换门禁

Django migration 为 `backend/inventory/migrations/0001_initial.py` 和 `0002_seed_control_rows.py`。历史数据命令固定为：

```powershell
python backend/manage.py migrate_inventory_from_d1 --source "<冻结 D1 副本>" --mode plan
python backend/manage.py migrate_inventory_from_d1 --source "<同一冻结 D1 副本>" --mode apply --approve-run-id "<精确 plan run ID>"
python backend/manage.py migrate_inventory_from_d1 --source "<同一冻结 D1 副本>" --mode verify --verify-run-id "<精确 apply run ID>"
```

`plan` 只读源并输出摘要；`apply` 只接受仍为空且 control 状态全新的目标；`verify` 独立重建源/目标摘要。源路径、源内容、排除数、计数、目标摘要、revision 或批准 run ID 任一变化都会失败关闭。生产运行时只允许 `migration_writer` 角色执行这些命令。

上面的“冻结 D1 副本”用于开发和恢复演练。正式 cutover 的 `--source` 必须是安装 `0101`、随后由同一 cutover 进入 `pending` fencing 的那个精确权威 D1 文件；另存的冻结副本只作备份和离线复核，不能替代正式 authority target。正式迁移期间须保持库存写维护窗口，并在进入 `pending` 后再次用同一源执行独立 `verify`，任何摘要漂移都必须停止切换。

`drizzle/0101_inventory_write_authority.sql` 是 operator-only 的 D1 写权准备迁移，不进入普通 Drizzle journal。它在 `owner=d1` 时行为中性；进入 `pending` 后冻结全部库存事实、库龄共享命名空间、库存上传、指纹、attempt 和 scope head 写入。双侧切换只允许：

```text
D1 d1 -> pending -> postgresql
PostgreSQL d1 -> postgres
```

PostgreSQL 尚未激活时可把同一 cutover 的 D1 `pending` 受控退回 `d1`。PostgreSQL 激活后不允许反向迁移。

`drizzle/0102_inventory_domain_retirement.sql` 也是 operator-only。只有精确 approved receipt、completed 系统测试、双侧 authority、无 processing owner/上传和其他域保留摘要全部通过时才能应用。完成后旧 D1 库存对象变为 6 个空 tombstone view，并由 24 个永久 guard 拒绝库存共享命名空间复活；ERP、其他共享导入行和既有 retirement receipt 必须保持不变。

## 6. 真实副本迁移证据

最终镜像演练使用正式 D1 的只读冻结副本，源和目标均位于 Git 忽略的隔离运行目录。运行时未连接或修改生产 D1/PostgreSQL。

| 项目 | 最终镜像证据 |
| --- | --- |
| 源 D1 文件 | 9,586,368,512 bytes；SHA-256 `47f640a822456d4c481e749021b5447c622b0f2ffc97c07b66654953ff256a9e` |
| 源/目标完整性 | 退役演练后再次执行 `PRAGMA integrity_check`，两侧均为 `ok` |
| plan | `inventory-plan-8578c8621ba94558b1b42c82b1e3f7d3` |
| apply | `inventory-apply-664cda330623447982cd0258e0a752b5` |
| 规范源/目标摘要 | `2249cc533aa898007e3fc882b454f7730604ffbd0d87feb6dc1e541996273390` |
| 目标 SQLite 镜像库（authority 前） | 1,168,932,864 bytes；SHA-256 `becdf67296568c275212aa971639f3c1bad9f281193c56468c9aa1974bb240d9` |
| 事实与审计 | 库存 1,085,958；库龄 275,669；批次 111；指纹 53；attempt 58；备货计划 1 |
| 数据截止 | 分仓库存和库龄均为 2026-09-01 |
| 最新权威快照 | 分仓库存 22,586 行；库龄 5,539 行 |
| 迁移排除 | `刷刷仓` 1 行；同日旧版本库存 132,361 行 |
| 设置 | 目标 30 天、临界 7 天、缓慢 45 天、滞销 90 天；自动补货关、库存预警开、负库存关 |

同日旧版本排除不是丢失当前事实。旧 D1 查询以同一快照日最后一个完成批次作为当前权威；历史库中存在 87,861 个跨批次重复业务键、132,361 条旧版本行，但任一单批次内没有重复身份。迁移保留全部历史批次和审计，只把每个日期最后完成批次的事实放入权威事实表，并在批次 totals 中记录 `migrationSupersededStockRows`。这使迁移后的当前查询与旧生产语义一致，而不是把历史版本叠加成库存现状。

镜像 authority/retirement 演练使用 `inventory-mirror-final-20260902T1146`，成功生成 PostgreSQL authority epoch `a4178568-2f7f-47e7-9345-9b32f1250ff3`，并验证 D1 `pending` 阶段写入阻断、终态 tombstone/guard 和其他域共享行数量不变。这些 ID 只证明隔离演练，不得写入或冒充生产 cutover 证据。`0102` 当前 SHA-256 为 `66e4e70f4ad6677b0f67ed737d6409ce79b879df3fd5ed76995583873510c29b`。

### 6.1 PostgreSQL 17.11 独立复验

在上述 SQLite 双侧 authority/retirement 演练之后，又从当前权威 D1 只读创建了一份新的在线一致性备份，并在独立数据目录、独立数据库和临时 `127.0.0.1:15432` 端口上完成 PostgreSQL 17.11 的全链 `migrate -> plan -> apply -> verify`。该集群不复用生产 `5432`、生产角色、生产凭据或生产存储；复验完成后已受控停止，未执行 authority、Worker 激活或 D1 退役。

| 项目 | PostgreSQL 独立复验证据 |
| --- | --- |
| PostgreSQL | 17.11，x86_64-windows，独立 `inventory_mirror` 数据库 |
| 新冻结 D1 | 9,586,368,512 bytes；SHA-256 `e5227ec28437835736de05d0c9bca0fd977d80f826a86122ebc9de4d4141c5c3`；`quick_check=ok` |
| plan | `inventory-plan-1cbd023c50a946998725f7b8c2f50667` |
| apply / verify | `inventory-apply-8f9dcdb102ca495b8b73f53d5bc8fd33`；最终状态 `verified` |
| 源/目标规范摘要 | `2249cc533aa898007e3fc882b454f7730604ffbd0d87feb6dc1e541996273390` |
| PostgreSQL 数据库大小 | 1,406,113,459 bytes |
| 事实与审计 | 库存 1,085,958；库龄 275,669；批次 111；指纹 53；attempt 58；备货计划 1 |
| inventory revision | `1`；source digest 与迁移摘要一致 |

这次 PostgreSQL 复验使用迁移命令自身的独立源/目标摘要重建，不把 SQLite 目标结果当作 PostgreSQL 成功证据。它证明 Django 模型、索引、事务和批量迁移在正式目标数据库引擎上可执行；仍不代表生产 cutover 已获批或已发生。

## 7. PostgreSQL 目标数据系统冒烟

最终 PostgreSQL 迁移目标直接运行库存领域查询，结果为：

| 查询 | 结果 |
| --- | --- |
| 库存总览 | 有库存；截止 2026-09-01；SKU×仓 22,586 |
| 库龄分析 | 有库存；截止 2026-09-01；明细 5,539 |
| 京东入仓监控 | 有库存；截止 2026-09-01；明细 1,523 |
| 商品经营库存投影 | 截止 2026-09-01；货品 6,464；分页/截断契约正常 |
| freshness consumer | 分仓 22,586；库龄 5,539；批次和日期与查询一致 |
| 设置 | 自动补货关、库存预警开、负库存关 |

冒烟使用无范围限制的镜像管理员 principal，仅读取迁移目标，没有创建计划、修改设置或导入新事实。

## 8. 运行、备份与恢复

`tools/django-inventory-service.ps1` 复用受控 Django runtime，提供 `ConfigureCredentials`、`ProvisionRoles`、`Start`、`Stop`、`Status`、`EnableStartup` 和 `DisableStartup`。凭据只保存在当前 Windows 用户绑定的 DPAPI 密文中。启动前必须验证：

- PostgreSQL 只监听 `127.0.0.1:5432` 且 `max_connections >= 80`；
- 8051/8052 端口、PID/CreationDate/命令行、文件 ACL 和 receipt 身份；
- inventory schema/index/revision、reader 只读事务、writer 最小权限；
- writer authority epoch/cutover 与启动配置精确一致；
- 销售 revision 与 ERP checkpoint 可读，但库存角色不能修改上游事实。

Worker successor 必须显式绑定以下回环端点和有界传输配置；缺失、非回环、读写同端点或超限配置都会失败关闭：

```text
TERUISI_DJANGO_INVENTORY_READER_BASE_URL=http://127.0.0.1:8051
TERUISI_DJANGO_INVENTORY_WRITER_BASE_URL=http://127.0.0.1:8052
TERUISI_DJANGO_INVENTORY_TIMEOUT_MS=120000
TERUISI_DJANGO_INVENTORY_MAX_REQUEST_BYTES=67108864
TERUISI_DJANGO_INVENTORY_MAX_RESPONSE_BYTES=33554432
```

这些值属于 release/runtime 配置，不得硬编码密钥；内部 HMAC 继续复用受控 `TERUISI_DJANGO_INTERNAL_SECRET`。正式 smoke 必须回读 successor 的有效配置并分别验证 reader、writer、超时、请求/响应上限和非回环拒绝。

PostgreSQL 一致性备份已把库存表、inventory revision、migration run 和 authority 纳入同一 exported snapshot 证据。恢复演练必须使用独立端口和独立临时数据目录，并回查库存最新批次、事实计数、摘要、设置、revision、authority 及 reader/consumer 查询。不得在生产 cluster 内创建恢复演练库。

切换越过 PNR 后，恢复只能使用 PostgreSQL 备份/WAL/PITR、兼容代码或审批过的前向修复；不得恢复 D1/legacy/shadow、双写或反向迁移。库存故障只能使库存相关 API、投影和导入失败关闭，不得改变销售、ERP、财务、网店、市场、商品经营或其他领域 authority。

## 9. 2026-09-03 正式生产 cutover 证据

| 项目 | 正式生产证据 |
| --- | --- |
| cutover / authority | `inventory-pg-20260902T162501Z-c6f5c5af1d254c0d`；epoch `7cc50a33-91a6-4e2d-8253-1531c6de3a22` |
| plan / apply / verify | `inventory-plan-6d2a3a052f8f401ba51c8f2d986767ba`；`inventory-apply-2c525a0e624141fab8f577d9fc5b91cd`；verify 与 apply 绑定 |
| 源/目标规范摘要 | `2249cc533aa898007e3fc882b454f7730604ffbd0d87feb6dc1e541996273390` |
| 正式数据 | 库存 1,085,958；库龄 275,669；批次 111；指纹 53；attempt 58；备货计划 1 |
| 排除 | `刷刷仓` 1 行；同日旧版本库存 132,361 行 |
| Worker effective head | release `20260902T161554Z-82b90718e2caf6f3`；build SHA-256 `03f67d55dd0a8c1996a2a1c9969edd2d0d6a5afc2cc726d9cffb1ab5e76c8ff1` |
| 系统测试 | 16 项 reader/writer、公开 API、直接/分片导入负向、商品投影、系统成本、AI、搜索、旧路径拒绝和其他域保留检查通过；smoke receipt SHA-256 `07ec0bb7e4e5a23032ca7301d6fe999c76620f859c06718c2e1ebba07479ba00` |
| R2 下线 | `inventory-upload/` 对象、字节、multipart upload/part 均为 0；证据 SHA-256 `e1519f1fcc3daf98e132093435513e94f0610d79550aebe604524ac6d96e7203` |
| D1 退役 | plan `f9a4f052b0b3ffb6614a6692b7a4813219e798231c2cb099ed0cc17574ee3638`；6 个空 tombstone view、24 个永久 guard、库存共享行 0；`PRAGMA quick_check=ok` |
| D1 切换前备份 | SHA-256 `7137049c4fbb30566a8a6c67068a763c8b363efceaaa5d21cd403f96ec5b587a` |
| PostgreSQL 正式备份 | `daily-20260902T171805Z-180898b39f33`；manifest `b3aa9d30b537e1f8802f66fe560b1d4494cc47382c678b517a48262595a39e7b`；dump `0f3885179bd06d1b984a2d3847e24a05949164e34cb2211a645c8d047b09f6f1` |
| 独立恢复演练 | `8f3a1c2d4e5f`，临时端口 55432；恢复摘要与备份 `133c47edfdd7cd7eb16974632486d4c3ad41ff3977748eeda0f1ae939fdd619a` 一致；生产库未触碰、服务状态未改变、临时数据已清理 |
| 终态运行 | PostgreSQL 17.11 `127.0.0.1:5432`；inventory reader/writer `127.0.0.1:8051/8052`；`inventory-service-enabled.json` 已绑定 authority 并加入受控开机链；退役后 7 个总控组件全部 ready |

正式证据保存在 `D:\teruisi-runtime\django-sales\audits\inventory-cutover\inventory-pg-20260902T162501Z-c6f5c5af1d254c0d`、对应 PostgreSQL backup/rehearsal 目录和不可变 Worker release 中。证据、backup、retirement receipt、authority epoch 和 successor 链均不得清理或覆盖。

## 10. 后续 release / 恢复门禁

以下清单是后续 release、灾难恢复或同类迁移必须继续满足的门禁；本次正式 cutover 已完成全部项目：

1. 合并并复审实现，确认与最新 `main` 无冲突；构建不可变 Worker successor，但不要激活。
2. 在不停服正式备份中核对库存表清单、revision、migration run 和 authority；另存 D1 冻结副本及 SHA-256。
3. 暂停库存/库龄导入和备货计划写入；确认没有 processing batch/attempt、owner、上传、receipt 或商品库存投影租约。
4. 在精确权威 D1 文件上受控安装行为中性的 `0101`，以该文件作为 `--source`，用正式 PostgreSQL migration writer 运行 Django schema、`plan -> apply -> verify`；独立复核计数、摘要、排除、设置和最新批次。另存冻结副本只作备份/复核。
5. 用精确 apply run 与 cutover ID 执行 authority `prepare`，使同一 D1 文件进入 `pending`；验证旧 D1 写入失败关闭、其他域仍可写，并立即从同一权威源重跑独立 `verify`，确认摘要未漂移。
6. 激活 PostgreSQL authority 和带 Django 库存路径的 Worker successor；启动 8051/8052，并验证 HMAC、权限、scope、超时、体积、幂等、导入回查和无 D1/R2 fallback。
7. 执行公开总览、库龄、入仓、备货、设置、直接/分片导入、商品投影、系统成本、AI、搜索、负向权限和其他领域隔离系统测试，生成绑定 build/run/cutover/摘要的正式 smoke receipt。
8. 完成正式备份和独立恢复演练；观察期内只允许 PostgreSQL 前向恢复。
9. 经独立审批写入精确 retirement receipt，受控应用 `0102`，回读 6 个 tombstone、24 个 guard、completed receipt 和其他域保留摘要。
10. 验证受控开机启动链、登录快捷方式和 supervisor 后再解除库存写入维护窗口。

任何一步证据不完整时停止，不得宣称库存正式迁移完成，也不得以旧 D1 作为隐式 fallback。
