# 0068–0073 受保护 SQL 侧表的正式备份与异集群恢复阻断审查

状态：**阻断生产采用**。本审查只读源码，没有连接正式数据库、运行迁移、备份或恢复。`tools/protected-ai-restore-static-audit.py` 是保守静态探针：当前返回 `status=blocked`、退出码 2；将来返回 0 也只表示已知源码阻断消失，仍须真实隔离异集群演练。

0073 候选 `0073_business_promotion_budget_v11_login_attestation` 新增 SQL-only `protected_business_budget_v11_login_attestations` 和初始 NOLOGIN `teruisi_ai_budget_v11_attestor_v2_login`。正式 PrepareApp/DeployApp/普通 migrate 以及备份和恢复继续提前拒绝；正式备份的精确迁移→表清单、角色预检和 `verify_catalog` 只扩大识别范围，不授权读取、签名、发布或恢复。下文 12 角色/8 表的旧隔离证据仅覆盖 0072 及以前，不能当成 0073 的 13 角色/9 表演练。旧迁移未应用的历史备份判定仍按其当时的迁移与物理表清单，不因本候选增加而补造新表。

后续隔离 0073 第二新集群演练已补齐 13 角色/9 表目录：owner/ACL、旧私钥 1 行与空新证明表保持；新表 owner 和函数授权故意漂移均拒绝，v2 分块密文六类损坏输入拒绝且无明文 dump，源/目标停机。证据 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-3867919323eb-protected-v2-0073\evidence.json`，SHA-256 `cc18231f0ae924a8eee14a399fcc407582c353fd5100666d74627ed02166e5b6`。仅使用进程内随机测试密钥与合成超级用户，不能代替正式密钥托管、特权备份身份及非空证明行的可恢复性。

## 现有路径与确定缺口

1. 正式 `tools/django-local-service.ps1` 以 `teruisi_sales_owner`、`NOSUPERUSER NOCREATEROLE` 执行 Django migration。`0068` 在 `install()` 中创建两个 NOLOGIN 角色、临时 `GRANT KEY_OWNER TO installer`、把密钥表和私有 MAC 函数转给 KEY_OWNER、撤回成员资格，然后再 `SELECT count(*)` 密钥表。普通 installer 无法创建/授予角色；即使预置角色，零成员约束使其不能靠预留 ADMIN 成员身份执行临时 GRANT，转移所有权和撤权后的 SELECT 也没有经过真实普通角色验证。`0067` 以及后续受保护角色迁移同样有建角前提。隔离测试以超级用户 `SET SESSION AUTHORIZATION` 模拟 NOLOGIN 角色，不能证明正式 migration 身份能安装。
2. 正式 `tools/django-postgres-maintenance.ps1` 的日常备份固定以 `teruisi_sales_owner` 调用 helper。`tools/postgres-consistent-backup.py` 先对所有 `protected_business_*` 表执行 `COUNT(*)`，再用相同账号 `pg_dump`。`0068` 密钥表由独立 NOLOGIN KEY_OWNER 所有，表和列禁止其他角色读取；普通备份账号没有该表的 SELECT，备份将拒绝。不能为使备份通过而向普通 Web、AI writer 或日常备份角色开放密钥 SELECT。
3. 当前 helper 的自定义归档备份带 `--no-privileges`，恢复带 `--no-owner --no-privileges`。PostgreSQL 17 文档说明 `pg_dump --no-owner` 对 archive 格式无效，而 `pg_restore --no-owner` 会令恢复连接用户拥有全部对象，`--no-privileges` 不重放 GRANT/REVOKE。因此单纯在恢复时去掉两个参数也未必能从**已有**归档找回已排除的 ACL；先用 `pg_restore --list` 和隔离恢复核实归档内容。当前形式必定不能维持 KEY_OWNER 私有表/函数所有权和精确 EXECUTE 授权。见 PostgreSQL 官方 [`pg_dump`](https://www.postgresql.org/docs/17/app-pgdump.html)、[`pg_restore`](https://www.postgresql.org/docs/17/app-pgrestore.html)。
4. 正式恢复演练确实 `initdb` 新集群，但 `Initialize-MaintenanceRehearsalRoles` 只预置旧服务角色，列表止于 `teruisi_ai_reader`/`teruisi_ai_writer`，没有 0067–0072 十二个受保护 NOLOGIN 角色，也没有更早的专用 AI 角色清单。它不重放 Django migration、不重建对象所有权/ACL。`collect_evidence` 根据同一归档中的 `django_migrations` 调用 0068–0072 的 `verify_catalog`，其中 0068 要求密钥表和 MAC 函数仍归 KEY_OWNER、权限精确；即使行数与 revision 摘要一致，恢复探针仍会失败。此前升级演练在**同一**临时 PostgreSQL cluster 内 `createdb`、dump/restore，已有全局角色保留，且不走正式 operator，不能替代异集群验证。
5. `382c6e55` 已让 protected 表进入备份的存在性清单和行数摘要；这是必要的数据覆盖，但不是密钥表可读性、角色元数据、所有权/ACL 或恢复权限的证明。归档若含已启用的私钥，则它是高敏感备份，正式目录和 E 盘归档的静态加密、访问控制、密钥轮换与销毁边界需独立落实，不能把密钥值写入日志或证据。

## 最小受控修复顺序

1. **迁移安装**：先在全新隔离 PostgreSQL 17 以真实 `teruisi_sales_owner` 运行 0067/0068，证明按现状失败且事务零副作用；设计单次、可审计的特权安装通道来建 NOLOGIN/零成员角色及转移对象所有权，安装后回读全部角色属性、成员、KEY_OWNER 对象、SECURITY DEFINER 函数正文与 ACL。不得永久授予普通迁移/Web 账号 `CREATEROLE`、超级用户或私钥读取权。若拆分特权 bootstrap 与 migration，必须保证失败原子性/幂等和 migration 收据不早于安全目录验收。
2. **备份**：为含 0068 的库引入隔离的特权备份执行身份/进程或等效受控机制，允许读取私钥表但不扩展普通服务身份。保持 exported snapshot 对证据与 dump 的绑定，移除备份对 ACL 的抑制并验证归档 TOC/恢复后有效授权；升级备份 manifest 版本及精确工具摘要。归档与复制加密、最小可读 ACL、日志脱敏必须先到位。若采用外部私钥封存而将其排除于数据库归档，必须证明旧证明可验、密钥轮换与恢复一致性，不得仅以零行恢复冒充完整备份。
3. **新集群恢复**：在独立目录/端口上按可信 migration 版本白名单预置所有需要的全局角色，受保护角色 `NOLOGIN NOINHERIT`、零成员、无密码；普通业务角色的登录凭据另行受控配置。用经完整性批准的 archive、受控超级用户、`--single-transaction --exit-on-error` 重放原始 owner 和 ACL；不能保留当前两个抑制参数。恢复后先查角色属性/成员/表和函数所有权/ACL、SECURITY DEFINER 与密钥表行/摘要，再运行原 `collect_evidence`，比较 snapshot 内容；业务进程仍不连接隔离集群。
4. **回归门槛**：同一版本与跨版本空库分别做含 0068–0072 的全新 cluster 备份恢复，包括非空**合成**私钥和受保护表、负例角色/ACL 漂移、普通角色拒读私钥、旧版无这些 migration 的历史备份。保留源/归档/恢复三方的目录及授权摘要，而非只比较行数。通过后才考虑正式迁移/备份 operator 发布；正式生产恢复是另一项单独审批操作，不由演练自动覆盖。

静态复核命令：`python tools/protected-ai-restore-static-audit.py`。它不生成密码、触碰数据库或修改文件；当前非零退出就是明确阻断。生产仍没有授权部署 0068–0072。
补充阻断：`Initialize-MaintenanceRehearsalRoles` 的当前角色名正则 `^teruisi_[a-z_]{1,64}$` 还会拒绝包含 `v11`/`v2` 的受保护角色；即使把缺失角色加入预置列表也无法恢复，须先版本化修正规则并保持精确白名单。

## 隔离异集群原型（不是正式备份或迁移）

独立工作树中可运行 `python tools/ai-postgres-rehearsal.py --business-protected-cross-cluster-restore --upgrade-only --port <空闲55440–55999端口>`。新测试开关先完整重放 0072 的前驱链，然后在**第二个**新 `initdb` 集群中预置精确的 12 个受保护 NOLOGIN/NOINHERIT/零成员角色以及原合成集群的其他无密码角色。它只给源端写入一条随机合成私钥，完整 custom dump，目标端用合成超级用户在单事务内恢复原 owner/ACL；逐项执行 0068–0072 `verify_catalog`，比对 8 张受保护表完整行摘要和角色属性，证明普通 AI writer 不能 SELECT 私钥。目标所有权或函数 ACL 被故意改坏时，目录检查必须拒绝并回滚该负例。目标集群使用独立回环端口、独立数据目录与另一随机密码，成功/失败都只停止其所属进程。

首次原型运行的通过证据已在核验两个集群停机后归档为 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-4f7953f8f7b8-protected-audit\protected-cross-cluster\evidence.json`，但首次版本尚未包含故意 owner/ACL 漂移负例。第二轮包含负例的完整演练证据在 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-38e31f193b17-protected-audit\protected-cross-cluster\evidence.json`：0068–0072 目录验证、12 角色、8 表与 1 个合成密钥恢复通过，私钥表所有权和验证函数 EXECUTE 两项故意漂移均被拒绝并回滚。结束后源/目标 `pg_ctl status` 均为 no server running，目录已受控归档。这个结果只证明**隔离合成超级用户**保留了 owner/ACL。正式路径仍保持旧参数、旧备份身份和未加密归档；不能据此宣称正式备份可用、真实非超级用户 migration 可安装、真实密钥受保护归档完成或生产可恢复。

## 默认关闭的正式预检门禁候选

`tools/django-postgres-maintenance.ps1 -Action ProtectedAiPreflight` 是需要操作者**显式**调用的只读动作：固定核对本机正式库身份、12 个受保护角色的 NOLOGIN/NOINHERIT/零成员状态、私钥表 KEY_OWNER 所有权、当前备份身份是否具备读取权限，以及既有归档 owner/ACL 参数和加密能力。它只返回脱敏阻断原因码，不执行 `COUNT(*)` 或读取私钥字节；当前设计一定返回 `blocked`。它不会创建备份、恢复集群、角色或授权。

正常日常备份发现 0067–0072 任一受保护迁移收据时，会在创建备份工作目录前拒绝；Python helper 自身也会在 `pg_dump` 前拒绝。历史未安装这些迁移的数据库仍走原备份路径。正式隔离恢复发现归档中有受保护迁移时，会在 `initdb` 前拒绝；Python helper 直接调用 restore 时还会从 archive TOC 检出 `protected_business_*` 并拒绝。正式 `pg_dump`/`pg_restore` 参数仍为原 `--no-owner --no-privileges`，故这只是**失败关闭**切片，不是让受保护库可备份的上线实现。

`tests/postgres-consistent-backup.test.py` 覆盖备份先拒绝且不调用 dump/内容读取、受保护 TOC 恢复先拒绝、角色/所有权/能力/策略的只读诊断；`tests/django-postgres-maintenance.test.ts` 覆盖显式动作、PowerShell 解析与恢复启动前栅栏。下一阶段仍须受控特权迁移/备份身份、密钥归档加密与独立新集群的**正式**恢复路径；在这些门槛完成前不得打开受保护迁移生产采用。

后续补上了正式运行目录的源码栅栏：`PrepareApp` 在建立 staging 前拒绝含 0067–0072 的源码，`DeployApp` 在替换已安装应用前复查准备件，`Invoke-DjangoMigrations` 在普通 `teruisi_sales_owner` 调用 `migrate` 前再次拒绝。隔离运行目录仍可执行合成演练。PowerShell 动态测试与 Django 生命周期 38 项测试通过。栅栏只识别固定正式目录与精确迁移文件，保持默认关闭；它不是特权安装通道，也未把正式备份/恢复改为可用。维护编排仍须先执行 `PrepareApp` 并确认成功，然后才进入停服窗口。

## 隔离非超级用户迁移探针

测试专用 `--business-protected-migration-role-rehearsal --upgrade-only` 从已完整恢复的 0066 合成种子复制测试库，使用随机密码的真实 `NOSUPERUSER NOCREATEROLE NOINHERIT` 迁移登录账号。未预置角色时 0067 因创建角色权限失败且零业务目录副作用；独立测试管理员预置精确 NOLOGIN 角色后，普通账号可安装 0067、0069、0071、0072。0068 因临时 GRANT KEY_OWNER 权限失败、0070 因私钥表读取权限失败，两次均无迁移收据或目录副作用，随后只由隔离测试管理员安装。普通迁移账号和 AI reader/writer 都不能读取私钥表。证据：`E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-b5e25dad0289-migration-role-audit\business-protected-migration-role-evidence.json`；源 `pg_ctl status` 已核为 no server running。

本探针只分类了真实普通账号的安装边界，**没有**给正式 `teruisi_sales_owner` 特权。当前生产迁移入口已在调用普通账号前拒绝这批候选，正式发布还须另建受控特权 0068/0070 安装通道，并做非超级用户/跨集群备份恢复终验。

续跑演练又扩展为每步从隔离数据库重建精确 0067–0072 迁移收据前缀、受保护表清单、12 角色无登录/无继承/零成员和已安装目录。0068 角色预置后与安装收据后各模拟一次安装器中断并换新连接续检；伪造未来收据和临时 KEY_OWNER 成员关系均被拒绝且回滚。原普通账号 0068/0070 失败分类、其余四步安装及私钥表拒读仍通过。证据 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-6d8e94e3939d-migration-resume\business-protected-migration-role-evidence.json`，SHA-256 `36b821f36c7c2b17c62dd44898bfc2ecb90149640dd767d3564d2edffaf75c8b`，原隔离集群停机。本切片仍只在合成超级用户辅助的克隆库运行；不改变正式迁移/备份的默认关闭状态。

另一轮隔离第二新集群演练把原明文自定义 dump 改为测试专用 AES-256-GCM 封套：随机 256 位密钥只驻本进程，固定版本/上下文摘要作为附加认证数据，目标目录仅保留 `.dump.aead`；认证后经 `pg_restore` 标准输入列目录并单事务恢复。错密钥、改密文/标签、截断均在恢复前拒绝；12 角色、8 表、1 合成私钥、owner/ACL 与故意漂移拒绝沿原核验通过。6 项纯封套测试和全链通过，源/目标 `pg_ctl` 均为 no server running；证据 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-db30f987684a-protected-aead\evidence.json`，SHA-256 `1f2e90cc093cdb7ed3844a3a8899b4e2b57f7584378d5bd71af6f7f4f49341da`。随机测试密钥没有保存，密文不能作为长期可恢复备份；归档目录的正式 ACL、受控特权备份身份、密钥托管/轮换、流式大文件认证及正式恢复仍未实现，原失败关闭门禁不放开。

新增的独立 v2 格式合同采用 64 KiB AES-256-GCM 分块、每块顺序/长度与规范 manifest AAD、整体摘要及认证结束帧；默认 `KeyProvider` 抛 `NotConfigured`，无正式入口或密钥。纯负例 10 项通过，旧正式备份 Python 26 项与维护入口 Node 16 项保持通过。第二新隔离集群全链改用此 v2 格式，32 块密文、错密钥/截断/篡改/乱序/重复块/错上下文六负例均拒；12 角色、8 表、1 合成私钥和 owner/ACL/漂移负例通过，明文 dump 为 0，源/目标停机。证据 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-f31632b8cd04-protected-v2\evidence.json`，SHA-256 `330bd940854b19185d44b5049af4fdd15d2f6edc3646a9c26dbedf367b7f423c`。当前模块仍把至多 64 MiB 明文放在内存，测试密钥不持久；没有大规模流式生产备份、专用特权身份或离机恢复密钥托管，正式备份/恢复继续硬拒。
