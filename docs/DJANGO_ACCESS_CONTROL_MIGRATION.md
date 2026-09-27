# 用户、角色、数据范围与权限审计域 Django 迁移手册

## 当前状态

2026-09-05，已在用户批准的受控停写窗口完成本机权限域 PostgreSQL 正式迁移、唯一写权激活及 D1 终态退役，Worker 薄适配、React 管理界面及权限服务均已完成真实部署检查。权限域没有 R2 文件/字节所有权或可达路径；全局 D1/R2 仍供其他未迁移范围使用，没有关闭或删除。下方早期镜像 run 仅是开发演练，不是生产 authority。

隔离演练证据：

- 源用户 1、启用 1、停用 0、管理员 1、受限 scope 0；
- PostgreSQL dry-run：`access-control-dryrun-4b57587f6f104430b94031f0dcc4140a`；
- PostgreSQL apply：`access-control-6692d8dac02047c89d91cbf56e8c2d21`；
- PostgreSQL verify-only：`access-control-verify-38aefa9ae6394466abf81f65904944e7`；
- SQLite 补充演练 apply：`access-control-428697830f904021a4593dac859170ce`，最终代码复验：`access-control-verify-e8164a3719b54116831645b3fb04c373`；
- 源/目标规范摘要：`526175808c1c6a6aa1d2656f266d12eaba4692a782f3674ebd4a1e7602082e08`。
- PostgreSQL 镜像中的独立 reader/writer 均通过固定角色、只读事务、authority 与精确表/序列权限 readiness；向 reader 临时注入一项跨域 `SELECT` 后 readiness 按预期返回 503，撤权后恢复。临时实例随后受控停止并释放独立端口。

这些 ID 只证明本次隔离镜像演练，不是生产 cutover、authority epoch 或 D1 退役凭据。

## 架构与契约

- Django app：`backend/access_control/`；PostgreSQL 是迁移完成后的用户、固定角色目录、数据范围、权限变更审计、revision、幂等回执和迁移证据的唯一权威。
- 公开 Worker 保留 ChatGPT 登录身份、同源写入门禁、请求解析、HMAC principal 信封、请求/响应体积与超时边界；它不再读取、写入或自动补建 D1 `app_users`。
- reader 固定监听 `127.0.0.1:8101`，仅使用 `teruisi_access_control_reader`；writer 固定监听 `127.0.0.1:8102`，仅使用 `teruisi_access_control_writer`。二者必须使用独立 DPAPI 凭据和回环地址。
- 固定角色为 `viewer`、`analyst`、`operator`、`admin`。角色能力目录由代码和迁移共同定义，管理端只分配角色，不允许在线改变能力集合。
- scope 必须为 `null`（不受限），或完整声明 `warehouses`、`channels`、`platforms` 三个字符串数组。后台 Agent 每个微步重新解析当前账号，并验证当前 scope 仍覆盖任务创建快照。
- 未登记、停用或响应契约漂移的账号一律失败关闭，不再自动创建 `viewer`。权限管理和审计只允许当前数据库中仍为启用、无限制 `admin` 的签名 principal。
- 每次用户权限变更都要求 1–200 字原因、`expectedVersion` CAS、稳定内部 request ID；审计保存操作者、目标、前后快照与摘要。系统引导管理员不得停用、降权或限制 scope，并始终至少保留一名启用且不受限的管理员。

## 服务配置

Worker 必须同时配置：

```text
TERUISI_DJANGO_ACCESS_CONTROL_READER_BASE_URL=http://127.0.0.1:8101
TERUISI_DJANGO_ACCESS_CONTROL_WRITER_BASE_URL=http://127.0.0.1:8102
TERUISI_DJANGO_INTERNAL_SECRET=<与 Django 相同、至少 32 字节的受保护密钥>
```

生产 writer 还必须由进程环境绑定已激活的：

```text
TERUISI_DJANGO_ACCESS_CONTROL_AUTHORITY_EPOCH=<UUID>
TERUISI_DJANGO_ACCESS_CONTROL_CUTOVER_ID=<本次受控切换 ID>
```

`tools/django-access-control.ps1` 负责独立凭据、最小权限角色、reader/writer 启停、readiness 和 `access-control-enabled.json`；顶层 Django/Worker 控制器把该域纳入统一启动、停止和完整就绪门禁。

## 数据迁移与切权

生产操作只能在批准的停写窗口，由 `migration_writer` 指向独立 PostgreSQL 权限域执行。不得把下列骨架中的占位符、密码或连接串保存进仓库或命令历史。

1. 备份并验证 PostgreSQL 全库；冻结旧权限写入口，复制同一时点 D1 权威文件并记录哈希。
2. 只读执行 dry-run，并保存精确 run ID、规范摘要和计数：

   ```powershell
   python backend/manage.py migrate_access_control_from_d1 --source <冻结的-D1.sqlite> --mode dry-run
   ```

3. 使用同一文件和精确 dry-run ID 执行一次 apply，再独立 verify-only。源路径、内容、计数或摘要变化都会拒绝 apply：

   ```powershell
   python backend/manage.py migrate_access_control_from_d1 --source <冻结的-D1.sqlite> --mode apply --approve-run-id <dry-run-id>
   python backend/manage.py migrate_access_control_from_d1 --source <冻结的-D1.sqlite> --mode verify-only
   ```

4. 在 Worker 停止、旧写入口冻结且处理中写请求为零时，先以 operator-only 方式安装 `drizzle/0111_access_control_write_authority.sql`，再使用精确 apply run 与 cutover ID 执行 `prepare`。此阶段仍允许同一 cutover 的受控 abort。
5. 回查 PostgreSQL 用户、角色、scope、审计、revision、最小权限和 reader readiness；激活 PostgreSQL authority 后启动 writer，并原子发布只调用 Django 的 Worker release。
6. 完成真实登录正向/负向测试、未知用户拒绝、角色与 scope 收紧、后台任务撤权、并发 CAS、幂等重放和审计回查后，才可运行 operator-only `drizzle/0112_access_control_domain_retirement.sql`。该脚本要求精确退役回执，将旧表替换为空 tombstone views，并安装 6 个永久写入 guard。

PostgreSQL authority 激活后即按 PNR 处理（不等到 `0112`）：禁止恢复 D1 用户权限读写、自动补建、`legacy`/`shadow`、双写或反向迁移。恢复只允许 PostgreSQL 备份/WAL/PITR、兼容代码或审批过的前向修复。

## 2026-09-05 受控发布前复查

- 用户已批准本次受控停写窗口与旧路径退役；全局 D1/R2 仍有 AI 数据、市场图片和运营事务附件依赖，权限域退役不得删除其他域的数据或全局 binding。本权限域没有 R2 文件/字节所有权，不虚构 R2 数据迁移。
- 修复了最小权限 writer 的 authority/角色联表 `FOR UPDATE` 权限错误：writer 只锁自己的 revision、用户和回执，不获得 authority 或角色目录的更新权；等待 revision 锁后重新验证操作者，撤权后的请求和重放均拒绝。
- `local-admin@teruisi.local` 是经 Worker 开发标记、受控本机构建和精确回环地址验证后的保留签名操作者，不是可注册或可经登录解析取得的用户。普通未知账号仍拒绝；审计保留本地操作者身份。
- SQLite `CURRENT_TIMESTAMP` 按 UTC 解释，再由系统按上海时区展示。上节较早真实源演练摘要使用了旧时间解释，不得作为生产迁移批准摘要；正式迁移须重新 dry-run/apply/verify。
- 独立 PostgreSQL 17 端口 `55439` 的固定合成夹具完成真实 reader/writer CRUD、审计、重放、CAS、错端点、未知用户和失配 authority 检查。apply：`access-control-768ce53cd85048d696bd804910e686b6`；verify：`access-control-verify-c389f9757a5e42ba9d8882d29a622e99`。合成源/目标摘要均为 `6ceeedeac19caf93232e20fcbdf0afefe2fc16313d5bb7752963eeb6d314ec70`，不代表生产用户。
- 已完成线上只读全库备份 `daily-20260905T084345Z-6b5fd76b5aab`，manifest SHA-256 为 `8813298e9eecdb591caf5c703978a39f394078cc3684d8703bf958750f10b703`。独立 `55441` 恢复演练 `ac2026090501` 成功，源/恢复 content SHA-256 均为 `c81b33538933501e6ef249cfb034a3889ba11ee6c6fccfcb43d837da4c62ff10`；生产数据库和服务状态未改变。
- 正式 operator 为 `tools/django-access-control-cutover.ps1`，只能从受保护 runtime app 执行。它提供 `PrepareRuntime`、`Snapshot`、`MigrateDryRun/Apply/Verify`、`InstallD1Authority`、`AuthorityPrepare/Activate`、`Smoke`、`RetirementPlan/Apply`；变更需 Worker 与权限 writer 停止，retirement 需要精确 plan ID、30 分钟内的真实部署 smoke 和同一事务内其他 D1 域事实摘要不变。权限表、revision、authority、迁移与审计已加入日常备份/恢复证据。

## 正式切换证据

- cutover：`access-control-pg-20260905T091024Z-06e143235de1`；authority epoch：`5903a114-ec95-4d17-95bd-d612f013d0ed`。
- D1 冻结全量快照：`audits/access-control-cutover/20260905-170537-ed638821/access-control-source.sqlite`（相对 Django runtime）；9,586,368,512 字节，SHA-256 `bea18c97d3f060594bacec3d138d63e174effe5d18ae311e348704f30dfa103a`。保留完整备份，不删除原始审计证据。
- 正式 dry-run：`access-control-dryrun-7f577890ed514d4e86e8d568a69d1e69`；apply：`access-control-06e143235de14abf80541c6d27e15781`；独立 verify：`access-control-verify-6de38cdcc2214a1eaa5ac0edbf05dc66`。
- 正式用户 1、启用 1、无限制管理员 1、停用 0、受限 scope 0。源/目标规范摘要均为 `92ff6dd59dd97011d692da1453a46f6d982451494c990dcb3207ffdb0364c497`；历史时间戳按 UTC 正确保留。
- Worker effective release：`20260905T090134Z-6a5d72609fb56a8a`；manifest SHA-256 `33ce62082f5bc91c3d79af2e004c692cfb4b18a71aa599b7701698783aa53472`；发布 plan SHA-256 `833ba1d142146eef1627baed7dd2c45b5fa54bc12ed6e22dfa34ff1bc580f32f`。successor SHA-256 `257e63e3ce17fe562805be0c58fa60441db88b30fe1406d1116c5cca179a55ca`；登录快捷方式由受控 apply 同步重绑并回读。
- 正式 smoke：`audits/access-control-cutover/20260905-171528-2d842221/Smoke.json`，SHA-256 `026c4433e96badac804a21fafe518e90fa616d57d159ce8d6053aa4ba6d53432`。12 项检查通过：reader/writer、公开用户/角色/审计、无效权限写入、跨站拒绝、未知账号、无签名请求、旧 D1 拒写、旧源码路径不可达及其他域 readiness。
- Chrome 独立无头上下文真实打开 `/?module=settings&view=permissions`：4 个角色卡片、1 个账号、新增和编辑表单、审计页面均成功渲染，3 个权限 API 均为 200，页面错误 0、生产表单提交 0。这是受控本机直连 principal 验证，不声称完成外部 ChatGPT SSO 登录演练。
- 代码门禁：Django 330 项通过；Node 1848 项中 1828 通过、20 项有条件跳过、0 失败；构建及 19 项构建产物检查通过；lint 0 错误、10 项既有警告；迁移检查无漂移。额外真实 PostgreSQL 固定角色演练及权限域 14 项回归通过。
- 首次 retirement apply 因 `sqlite_stat1` 中权限表自身的优化器统计被误算为其他域数据而安全回滚；未改变旧账号事实、其他域事实或 PostgreSQL authority。修复只在保全摘要中排除 `app_users` 和 `access_control_write_authority` 自身的 `sqlite_stat*` 行，仍比较所有其他域统计；保留该失败尝试的 intent 文件，补充 `ANALYZE` 回归夹具，按受保护 runtime 部署修复，不手改运行包。
- 运维顺序补充：涉及受保护控制入口更新时，先通过当前 effective head 受控停止 Worker，再更新入口或合并会改动入口的源码；禁止在旧 Worker 尚未停止时让受保护入口提前漂移。
- D1 终态退役：`audits/access-control-cutover/20260905-172555-e587f0a6/RetirementApply.json`，SHA-256 `eaa2c709ad7508ced1bb4df47f4ecda456df617ff95851cd1ad1881af4a6bf0e`；plan ID `e6c322b328332b9a559d7a8d48cb20bc54acb21258a93b8b4c8475aaa5a88631`。`app_users` 与 `access_control_write_authority` 已变为 2 个空 tombstone views，6 个永久 guard 完整，旧写入被 `access_control_domain_retired` 拒绝。其他 D1 域事实及统计保全摘要前后均为 `cd49349d0cd414219a13c64e1e977778a70c49aecf73038a2e63e6b9db0578a5`。
- 迁移后正式全库备份：`daily-20260905T093723Z-95b158df2c32`；manifest SHA-256 `6881b624943e26af0d9013f572d55b7eaa8c66d2f930f4ca24e0c559b1190b13`；dump SHA-256 `4556d96d7143affed5e28a50e1f787e4dae04671ef96b5f8e5a94b515abace36`；content SHA-256 `0af735396b0c09d446f4b98c1dd22cfc2f16e9f36d6ec9b3984a7295da6ab1b7`。证据明确覆盖权限域 7 张表（用户 1、角色 4、审计 1、revision 1、authority 1、迁移 run 3、请求回执 0），并绑定 revision=1、迁移 run、正式 authority epoch 与规范摘要。
- 运维备份严格白名单已同时更新 Python 证据生成器与 PowerShell 验证器；新增权限域字段、迁移标记和 7 张表必须一致，缺失/非法状态继续失败关闭。曾因白名单未同步而拒绝一次备份，不将其标记为成功；修复经 PowerShell 5/7 的 8 项新旧证据验证和 11 项维护回归验证，再按受保护 runtime 部署。所有成功备份、原始快照与失败尝试证据保留。
- 2026-09-05 17:39（上海）最终总控回读：`Running`、`backendState=Ready`、`workerState=exact_release`，11 组组件（含权限和 BI）全部 true；退役后 Chrome 权限页与三个公开权限读取 API 再次通过，生产表单提交仍为 0。
- 迁移后备份的独立恢复演练 `ac2026090502` 于 2026-09-05 17:44（上海）完成，独立端口 `55442`，源/恢复 content SHA-256 均为 `0af735396b0c09d446f4b98c1dd22cfc2f16e9f36d6ec9b3984a7295da6ab1b7`；`productionDatabaseTouched=false`、`serviceStateChanged=false`。演练实例与其临时数据由受控 operator 清理，正式备份、D1 源快照和切换证据继续保留。

## 验证清单

开发和隔离验证至少执行：

```powershell
cd backend
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test access_control

cd ..
npm run test:unit
npm run lint
```

生产 cutover 还必须补齐受控 PostgreSQL 镜像演练、角色越权拒绝、reader 只读事务、writer authority 绑定、真实部署回读、D1 guard/tombstone 证明、备份恢复证据，以及与最新 `main` 的完整回归。合并代码不等于发布或生产迁移。
