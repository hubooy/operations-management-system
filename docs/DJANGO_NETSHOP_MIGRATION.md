# 网店分析 Django/PostgreSQL 迁移手册

## 1. 当前结论与生产边界

2026-09-01，本机网店分析后端已完成 Django/PostgreSQL 正式单写切换、完整垂直链路验收和 D1 终态退役。PostgreSQL 现在是网店事实、批次、SKU/SPU、推广、上传、revision、幂等/尝试审计、scope head、查询和写入的唯一权威；`127.0.0.1:8021/8022` 已作为独立最小权限 reader/writer 加入现有受控开机启动链。切换已跨过 PNR，不再允许把旧 D1、旧 Worker 分支或 `legacy`/`shadow` 路由作为读取、写入、容灾或回滚路径。

网店分析继续使用现有 React/Next.js 前端；前端仍由 `app/shop-module-view.tsx` 提供，只通过现有同源公开 API 读取数据，没有改写为 Django template，不直接连接 PostgreSQL，也不承担读写权威。公开 Worker 继续负责真实 principal、权限与 scope、Excel/ZIP/图片解析、HMAC 信封、请求体积和超时边界及薄边缘适配；Django 负责领域二次校验、事实发布、查询、revision、幂等、审计和写入所有权。

旧 D1 网店对象已变为 15 个空 tombstone view，共享导入表安装 9 个永久网店域 guard；原先为当时尚未迁移的市场域保留的 `market_netshop_projection`，已在同日后续市场正式切换时由 `0098` 终态退役。当前市场兼容投影只由 Django netshop consumer 原子写入 PostgreSQL 市场表。D1 仅保留 retirement receipt、tombstone、guard 和审计材料；恢复只允许 PostgreSQL 备份/WAL/PITR、兼容代码或经审批的前向修复。

## 2. 目标拓扑

```text
浏览器
  -> 现有 React/Next.js 网店分析页面
  -> 公开 Worker /api/sales/* 与 /api/netshop/*
     - 真实 principal、角色/scope、平台+店铺复合身份和参数边界
     -> Django sales reader 与 netshop_reader 127.0.0.1:8021
        - 概览、商品、SKU/SPU 日表现、推广、导入历史

导入、自动化与跨模块 consumer
  -> 公开 Worker
     - Excel/ZIP/图片解析与规范化、HMAC、体积/超时/响应边界
     -> netshop_reader 127.0.0.1:8021
        - 客服/全局搜索/AI/市场使用的有界 consumer
     -> netshop_writer 127.0.0.1:8022
        - 规范化导入、分片会话、图片元数据、幂等与原子发布

PostgreSQL netshop_*：唯一网店事实、批次、revision、审计和 authority

PostgreSQL market_*：市场域唯一权威
  <- Django netshop consumer 的有界兼容投影
  <- 来源固定、原子激活，不是第二个网店事实源
```

reader、writer 必须使用不同 URL、不同最小权限数据库角色和不同请求体上限。网店服务故障只能使网店以及依赖其有界 consumer 的对应结果失败关闭，不得改变销售、财务、ERP、库存或市场自身的权威边界。

## 3. 已实现契约

- Django `netshop` app 独立拥有网店事实、导入批次、SKU/SPU 与推广 revision、内容指纹、导入尝试、scope head、分片上传、请求 receipt、迁移运行和 write authority。
- 现有 React 网店工作区继续调用同源的 Django-backed 销售/网店公开 API；筛选请求可取消并用 generation 阻止迟到覆盖，商品跨页固定首屏 snapshot，推广概览必须与当前商品页 snapshot 精确一致。浏览器不连接 PostgreSQL，也不在前端重算服务端业务口径。
- Django reader 只提供版本化 JSON 领域响应和有界 consumer；所有成功读取必须携带有效 revision，页面渲染仍属于现有 React/Next.js 前端。
- TypeScript 解析器输出版本化规范化载荷；Django 再次拒绝未知字段、非法日期/金额/指标、范围不一致、重复业务身份、聚合行、零行和正文指纹不一致。
- 重复判定绑定“领域 + 精确业务范围 + 清洗后完整规范化内容”。文件名、工作簿元数据、行序和对象键序不产生新批次；业务内容或范围变化必须形成新批次并在同一事务中完整替换。
- 范围 owner、attempt、fingerprint、事实发布、revision 和完成回查由 PostgreSQL 事务统一控制；迟到 owner、过期租约和 request ID 复用失败关闭。
- reader 成功响应必须携带 `X-Netshop-Data-Revision`，格式为 `<单调序号>:<源摘要前12位>`；跨页读取必须固定同一 revision。
- writer 只有在 `netshop_write_authority.status=postgres` 且进程环境中的 authority epoch/cutover ID 精确一致时才可接收权威写入。
- reader 事务固定只读；writer 只能对明确网店表执行所需 DML，不能 DDL、不能更新 authority、不能写销售/财务/ERP 或其他领域。authority 只允许 migration owner 改变。
- 客服商品主数据映射、全局搜索、AI 网店工具和市场投影只调用固定 consumer 操作，不接收任意 SQL、表名或排序表达式。
- 市场兼容投影由 PostgreSQL 市场域持有投影记录、单行 control 和 active revision；新 revision 只有在完整分页落库、行数回查和 owner fencing 通过后才原子激活。
- 旧 D1 网店表终态退役后变为 15 个空 tombstone view；共享导入表保留其他领域数据，并安装 9 个永久网店 insert/update/delete guard。

## 4. 服务、凭据与最小权限

正式端口固定为：

| 服务 | 端口 | 数据库角色 | 主要边界 |
| --- | ---: | --- | --- |
| netshop_reader | `8021` | `teruisi_netshop_reader` | 只读事务、1 MiB 请求上限、有界查询/consumer |
| netshop_writer | `8022` | `teruisi_netshop_writer` | 128 MiB 请求上限、网店表 allowlist DML |

受保护 runtime 入口为 `D:\teruisi-runtime\django-sales\app\tools\django-netshop-service.ps1`。凭据只保存到当前 Windows 用户绑定的 DPAPI 文件；源码工作树、环境日志、命令参数和本文不保存明文密码。

```powershell
$netshop = "D:\teruisi-runtime\django-sales\app\tools\django-netshop-service.ps1"

& $netshop -Action ConfigureCredentials
& $netshop -Action ProvisionRoles
& $netshop -Action Status -Json
```

`ProvisionRoles` 必须在 `8021/8022` 无监听时运行，并在完成后回查角色属性、只读默认值、表/序列权限、DDL 拒绝和跨领域 DML 拒绝。普通 Django `runserver` 会尝试读取 `django_migrations`，不适合最小权限长期角色；正式进程固定使用 runtime 中的 Waitress 参数和 readiness 门禁。

`EnableStartup` 只有在 PostgreSQL authority 已激活且 reader/writer 均 ready 后才允许写入网店启动状态。基础 Django 控制器只有看到该受控状态文件才会随主栈启动网店服务；`Stop` 会先在服务 mutex 内将 desired state 置为 stopped，再停止网店和既有 Django 栈，避免 supervisor 竞争重启。

## 5. 历史数据迁移

所有开发、演练和正式预检都必须先对权威 D1 创建一致、封存、无 `-wal/-shm` 的独立快照。不得把迁移命令直接指向在线 D1。对快照应用 `0094_netshop_write_authority.sql`；`0095_market_netshop_projection.sql` 只建立市场兼容投影结构。二者均为 operator-only，不进入普通 Drizzle journal，也不授权正式切换。迁移 apply 前必须先让**同一封存快照**用精确 run/cutover 进入 `pending`；否则 apply 会拒绝。在线正式 D1 的 `0094 + prepare + activate` 是后续独立切换步骤，不能拿封存快照的 pending 状态冒充线上 authority。

迁移命令省略模式为只读 plan；apply 和 verify-only 必须消费同一规范化快照推导出的精确 run ID：

```powershell
cd D:\teruisi-runtime\django-sales\app\backend

python manage.py migrate_netshop_from_d1 --source <封存快照.sqlite>
python manage.py netshop_write_authority --source <同一快照.sqlite> `
  --prepare --approved-run-id <plan返回的netshop-run-id> --cutover-id <cutover-id>
python manage.py migrate_netshop_from_d1 --source <同一快照.sqlite> `
  --apply --approved-run-id <plan返回的netshop-run-id>
python manage.py migrate_netshop_from_d1 --source <同一快照.sqlite> `
  --verify-only --approved-run-id <同一netshop-run-id>
```

门禁会逐节计算行数和规范化摘要，覆盖事实、事实投影、批次、推广聚合、revision、指纹、尝试、scope head 与上传状态。缺失的历史指纹只允许从当前已发布权威事实确定性补建并记入迁移审计；它不冒充原始导入事件。目标 PostgreSQL 必须处于 `d1` authority 且所有网店业务表为空，apply 才能执行；事务内逐节回查不一致会整体回滚。

迁移后必须使用真实 reader 完成公开概览、商品、SKU/SPU、推广、导入历史和 consumer 对比，并验证：真实 principal/scope、非法参数、HMAC 篡改、响应上限、reader 写拒绝、writer 跨域拒绝、重复内容、范围替换、owner fencing 和 revision 固定。

## 6. 正式切换与 PNR

正式切换必须覆盖浏览器/自动化、公开 Worker、reader/writer、PostgreSQL、市场兼容投影、客服、全局搜索、AI、部署启动和监控。推荐顺序如下：

1. 冻结网店导入、分片续传、推广聚合和图片元数据写入，并停用会自动拉起旧 Worker 的启动入口；销售、财务、ERP 与其他模块继续运行。
2. 生成并验证正式 PostgreSQL 备份、在线 D1 一致快照、源 SHA-256 和 Worker 候选构建 SHA-256。
3. 对最新封存快照应用 `0094`，重新 plan，并让**该封存快照**用精确 run/cutover 进入 `pending`；然后才可对 PostgreSQL 执行 apply/verify。
4. 部署 Django migrations、DPAPI 凭据和最小权限角色，启动 `8021` reader；writer 不得提前接流量。完成公开读、跨模块 consumer 和权限负向测试。
5. 将 `0094_netshop_write_authority.sql` 受控应用到精确在线正式 D1，记录其 SHA-256；普通 Drizzle journal 不得自动消费 operator-only 迁移。
6. 对在线正式 D1 使用同一 run/cutover ID 执行 `--prepare`。此时 D1 拒绝新网店写入，但 PostgreSQL writer 尚未激活。
7. 在 writer 未接请求且 PostgreSQL authority 仍为 `d1` 时，若门禁失败，可用 `--abort-pending` 恢复 D1 写入。
8. 门禁全绿后执行 `--activate`，记录唯一 authority epoch；启动 `8022` writer 并验证 readiness。此后跨过 PNR，不再允许回到 D1。
9. 构建并验证新的不可变 Worker successor，使所有网店公开 API 和 consumer 一次性指向 Django/`8021/8022`；现有 React 页面保持不变，生产 API 包中不存在旧 D1 领域分支或 fallback。
10. 受控应用 `0095`，同步并原子激活当前 PostgreSQL revision 的市场兼容投影；使用真实浏览器验证现有 React 页面、principal/scope、筛选、分页 snapshot、公开读写与落库回查，并完成客服/全局搜索/市场/AI 冒烟和旧路径拒绝证明。
11. 稳定观察及独立退役审批通过后，才执行第 7 节 D1 终态退役。

authority 命令骨架：

```powershell
python manage.py netshop_write_authority --source <精确D1> `
  --prepare --approved-run-id <run-id> --cutover-id <cutover-id>

# 仅 prepare 后、activate 前允许
python manage.py netshop_write_authority --source <精确D1> `
  --abort-pending --approved-run-id <run-id> --cutover-id <cutover-id>

python manage.py netshop_write_authority --source <精确D1> `
  --activate --approved-run-id <run-id> --cutover-id <cutover-id>
```

`activate` 使 PostgreSQL 获得唯一写权；第一笔 PostgreSQL 权威写入后跨过 PNR。此后禁止恢复 D1 写入、旧 Worker 分支或反向迁移。恢复只允许兼容 Django/Worker release、PostgreSQL 备份/WAL/PITR 或经审批的前向修复。

## 7. D1 终态退役

`0096_netshop_domain_retirement.sql` 只能由 `retire_netshop_d1` 在完整 preflight 和精确 plan ID 下事务执行，不得由普通 Drizzle 自动应用。退役前系统测试 receipt 必须是 30 分钟内生成的严格 JSON，并证明：

- Django reader 正向和 writer 负向；
- 公开概览、商品和推广；
- 市场兼容投影、客服映射、全局搜索；
- 旧 D1 写入已拒绝；
- source/target 摘要、迁移 run、cutover 和 Worker build SHA-256 精确绑定。

```powershell
# 只读 plan
python manage.py retire_netshop_d1 --source <精确D1> `
  --cutover-id <cutover-id> --approved-run-id <run-id> `
  --smoke-receipt <system-test-receipt.json>

# 只能使用上一命令返回的精确 plan ID
python manage.py retire_netshop_d1 --source <同一D1> `
  --cutover-id <cutover-id> --approved-run-id <run-id> `
  --smoke-receipt <同一receipt.json> --apply `
  --approved-plan-id <plan-id> --audit-output <新的audit.json>
```

提交后必须证明 15 个 tombstone view 全部为空、9 个共享表 guard 完整、`market_netshop_active_projection` 的 revision/行数/摘要未变、其他领域共享行的逐节摘要未变，并以同一 plan 重放得到 `duplicate`。终态退役不是可逆切换；D1 只保留 receipt、tombstone、guard 和市场兼容投影，不再是网店事实或恢复来源。

## 8. 2026-09-01 本机正式生产证据

本次正式切换保留现有 React/Next.js 前端，只替换网店后端事实源和读写路径。冻结期间发现旧启动快捷方式曾重新拉起 predecessor Worker；最终封存回查因此比初始迁移多出 `5,781` 条事实和 `12` 个批次。该差异在 PNR 前被门禁捕获：旧 Worker 被受控停止并移除旧启动绑定，重新生成最终 D1 快照，只清空 PostgreSQL 中 17 张尚未取得 authority 的 `netshop_*` 暂存表后重新全量迁移。外键回查为 0，销售、财务、ERP 和其他域未被清空或修改；旧迁移 run 和旧备份作为审计保留，不作为正式数据水位。

| 项目 | 正式结果 |
| --- | --- |
| cutover / authority epoch | `netshop-pg-20260901T013053Z-4977777cec32` / `82694b25-5036-47cd-90f4-4d822374e594` |
| 最终迁移 run | `netshop-edb60eb6a0055d9a8b32a6dc` |
| source / target 摘要 | `edb60eb6a0055d9a8b32a6dca8816f0326d79c4b18647a46979330d06c2a0282`，完全一致 |
| 最终 D1 冻结快照 | `9,586,368,512` bytes；SHA-256 `5fb6d8638f58773ee5999ef16cb6114600804caab6a65f955e27e9cd5a7b8586`；`quick_check=ok` |
| 网店事实 / typed projection / 批次 | `1,070,473` / `1,070,473` / `964` |
| 指纹 / 尝试 / scope head | `795` / `989` / `38`；历史指纹确定性补建 `169` |
| 推广商品 / 店铺日 / 聚合状态 | `45,956` / `287` / `287` |
| 上传 / 完成结果 / 分片 | `4` / `4` / `0` |
| Django revision | `1:edb60eb6a005` |
| operator-only migration SHA-256 | `0094`：`9966ab22a37a10c48a53b1692bec8b1b8322b6028284581328e68f2e1cd5ecae`；`0095`：`0bc6a97504a233afe470419069c635b077df64d544179ade70b9e1f97f81a9d5`；`0096`：`6aec29e7d7e320401694c39bb82a60bbfeafdb4fbbf003e20c41b9f691625305` |
| 最终 Worker source / release | commit `89b47202c8c552b878c85e7fbe6cd68b47ed3aad`；`20260901T045030Z-917076c7520e4f95` |
| Worker manifest / entry | `76a290b322d740d99631bd00a09182ffbee0c831c7b9f54fe4b2aa2ef76303bb` / `04cdd6907c261633fd924a75457bcfcde8a0dea0624a3e5a87bd74c28390fc9b` |
| Worker plan / successor / consumption / startup | `ed854d065c0f3e65e135bd4c58414bdc69de3d0f9f8cfb12548a7b986541ee7b` / `bc805674cb19d179374f048880b97340ef72247a88ded078bf6dd5ef02adee96` / `6d69f2df4be6de456d0e435943d82f9093a03f8c83ce2b86f0caa5fdfd512fe9` / `2fbf1775a2345c90bc68d8251051505ec369678f6c81a07d5bc36cf1ae60019b` |
| 市场兼容投影 | revision `1:edb60eb6a005`，`115,341` 行；摘要 `7c5d3345eea046a2f34e8b9af53cc799b286c78b211b7380d70be38ab819c6b2` |
| smoke receipt | SHA-256 `1250e682d7032b29d7e11f4373e6f341ce00672ef520b724ba70bc0907d60c73`；9 项完整垂直链路均通过 |
| D1 retirement plan / audit | `b35a2f89eeb6a770a3563d663858842b05ff8043f663b4b4e0251efef5a1f6b7` / `51c589deb5de7b9e5d21aee7fec383d14aabed6558691afd7731bb1b924406e6` |
| tombstone / guard / 重放 | `15` 个空 view；`9` 个永久 guard；三类共享表写入均返回 `netshop_domain_retired`；同一 plan 重放返回 `duplicate` |
| 非网店共享行 | 指纹 `251`、尝试 `325`、scope head `7`；退役前后摘要 `b7eea733abd0d4aa313ce63f69375779cd5e7218154b2f2dce3de1b209030f10` 一致 |
| 切换后 PostgreSQL 备份 | `daily-20260901T051159Z-d881cc7c9118`；manifest `20b4b0f10ecf57d2fb6b8cf25852179d10a10c5b7478acb0e6243476a5e015f4`；content `f7a792a7a8ae676e52f929987fd7b84dcc471d4af7b68e53feac2ebaad8ecfcf` |
| 独立恢复演练 | `a17c4e9b20d6`，端口 `55432`；恢复摘要与备份完全一致，`productionDatabaseTouched=false`，临时数据已清理 |
| 退役后系统检查 | Worker exact release / startup verified；`8021/8022` ready；网店概览、商品、推广、导入历史、市场、客服、全局搜索均 `200 no-store`；D1 `quick_check=ok` |

市场首次全量投影在 D1 已完成激活时曾因 Miniflare 未返回 batch change metadata 而误报一次 503。生产回读证明 control row 已原子激活且数据完整；随后以前向修复 `89b47202` 增加 durable control-row readback，定向测试与 Vinext build 通过，并通过新的 immutable successor 发布。该修复不改变数据或 authority，只消除未来 revision 首请求的假失败。

正式切换的 Django 测试为 `183/183`；最终 Worker/TypeScript 全量为 `1,695` 项中 `1,691` 通过、`4` 跳过、`0` 失败，渲染契约 `19/19`。activation readback 修复另通过定向 `4/4`、ESLint、Vinext build 和 immutable release 契约构建；现有 React 页面已用真实浏览器验证网店商品与推广视图。

## 9. 2026-09-01 隔离演练证据

演练从正式 D1 做只读一致快照，随后只在独立 worktree、独立 PostgreSQL 17.11 数据目录、`127.0.0.1:55432` 和临时 Django 端口运行。正式 D1、正式 PostgreSQL、正式 Worker和 `8021/8022` 均未改动。

| 项目 | 演练结果 |
| --- | --- |
| D1 快照大小 / SHA-256 | `9,586,368,512` bytes / `9237e03c754a02e6df33b1482c1483829a1bf51018d7c9c9d2d64094eefadc8d` |
| 网店事实 / 批次 | `1,064,692` / `952` |
| 推广商品 / 店铺日 / 聚合状态 | `45,956` / `287` / `287` |
| 指纹 / 尝试 / scope head | `783` / `977` / `38` |
| 上传 / 完成结果 / 分片 | `4` / `4` / `0` |
| 迁移 run ID | `netshop-6571a668eecb739f75c71850` |
| source/target 摘要 | `6571a668eecb739f75c7185005492a16fd3a127d7ca18ab18f16297293eb187d`，完全一致 |
| 历史指纹确定性补建 | `169` |
| 演练 cutover / epoch | `netshop-rehearsal-20260901-a1` / `9f190594-9a0a-46d1-a9c1-e2c6ac41a7fc` |
| Worker entry SHA-256 | `f767c3f0cd473f82d2ff7503a63fd598c954295ff610a0db16864239552b3d6d` |
| 市场兼容投影 | revision `1:6571a668eecb`，`113,808` 行 |
| 退役 plan / audit | `b78e6618fd1b9d84dd03e20a98a4dca9bc157729d97bde526cdbb3b7ad9c0622` / `fbc51c815d03ad83655b4d9341331189484d2ab3179838954c7e1df5da3d5412` |
| tombstone / guard | `15` 个空 view / `9` 个永久共享表 guard；三类插入均返回 `netshop_domain_retired` |
| 非网店共享行 | 指纹 `251`、尝试 `324`、scope head `7`；退役前后摘要一致 |
| 退役重放 | 同一 plan 返回 `duplicate`，plan/audit 不变 |
| 退役后跨模块 | 市场 `113,808` 行；客服 `584` 个候选编码映射 `483` 行；全局搜索精确 `1` 条；旧 D1 事实 `0` 行 |
| 后端/数据退役演练当时的 Django 测试 | `183` 项通过 |
| 后端/数据退役演练当时的 Worker/TypeScript 全量 | `1,694` 项中 `1,690` 通过、`4` 跳过、`0` 失败；渲染契约 `19/19` 通过 |

reader/writer 最小权限、真实 HMAC、独立端点、PostgreSQL authority、旧 D1 写拒绝、公开 API、现有 React 页面契约、市场、客服和全局搜索均完成正向/负向检查。表中的 Worker entry SHA-256 和退役 plan/audit 只绑定当时的后端/数据演练构建，不能冒充正式不可变发布 receipt。正式切换时仍必须为当时的精确 immutable successor 重新生成 SHA-256、smoke receipt 与 retirement plan。该证据**不代表生产已经切换**。

## 10. 后续回归与运维门禁

- 最新正式 D1 快照 SHA-256、source/target 逐节摘要、迁移 run 和 PostgreSQL 备份/恢复演练完整。
- `8021/8022` 使用受保护 runtime、DPAPI 和最小权限角色；readiness 验证 schema、index、revision、authority、只读事务和进程身份。
- 现有 React 网店页面、所有网店公开 API、分片、图片、京东/天猫 SKU/SPU、推广、AI、客服、搜索与市场投影均通过真实 principal/scope 测试。
- 生产包继续包含完整 React 网店模块；请求取消、迟到响应门禁及商品/推广 snapshot 约束均有正向和负向证据，且公开 API 静态扫描不存在旧 D1 网店事实路径。
- 重复、范围替换、并发 owner、迟到请求、HMAC/JSON/响应上限/超时和 reader/writer 错路由全部失败关闭。
- 不可变 Worker effective head 的构建摘要与 smoke receipt 一致，生产包静态扫描不存在旧 D1 网店事实路径。
- 旧 D1 写拒绝、终态 tombstone/guard、其他领域摘要、退役幂等和发布后只读/安全写冒烟均保存审计。
- 后续 release 必须继续证明 PostgreSQL 是网店唯一权威、D1 tombstone/guard 未被复活、市场投影只来自 Django consumer，且现有 React 前端契约没有被 Django template 替换。

本机生产切换已跨过 PNR。后续故障不得恢复 D1 网店读写、旧 Worker 分支或反向迁移；只能使用上述正式备份/WAL/PITR、兼容修复或经审批的前向数据修复。
