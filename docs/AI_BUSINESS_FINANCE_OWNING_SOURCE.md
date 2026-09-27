# 财务 owning 内存来源候选

2026-09-18。内部服务 `finance.business_analysis_source.open_source(principal, query, analysis_period=None)`，调用方必须正常离开 context 后才可交付派生成果。不新增公共路由、模型工具、迁移或旧 evidence 协议枚举；不是持久封存实现。

按真实 `access_control_users` 的 email/role/status/scope/version 前后核验无范围限制的 active 管理员；不接受不存在的本地管理员回退。读取完整 finance revision 整数及 64 位摘要，不使用页面接口的短摘要作为证据版本。

查询固定 1—24 个连续自然月和完整财务 scope 四字段，每批最多 100 行按 id keyset 扫描。每页前后比较账号、完整 revision、所选月份发布状态和 month→batch 完整元数据；不完整发布、批次缺失或迟到变化整源拒绝。所选缺月和某精确 scope 零行如实成为缺口，不猜测相邻组或店名别名。完整行由纯合同按既有行数/字节边界生成不可变内存页，退出 context 再核验一次。返回 binding 的副本不能修改服务内部复验状态。

返回 source 继续固定 `sourceAuthorityVerified=false`，因为纯对象不能携带可转让授权；binding 仅记载此次内部读取，`persistentEvidenceVerified=false`。后续真正持久化、跨进程恢复、签名 HTTP 路由、模型读取和报告采用均未接通。所有行源端是否本应属于跨组同名店、合并比率是否可比较等限制仍见 `AI_BUSINESS_FINANCE_SOURCE.md`。

## 权限及采用门槛

现正式 `teruisi_finance_reader` 只有财务业务表 SELECT，没有账号五列权限。候选 `business_source_permissions.grant_actor_read` 仅新增 `access_control_users(email,role,status,scope,version)` 列级 SELECT，拒绝非受限数据库角色名；不增加 display_name、写权限或其他业务表权限。

本片没有把 helper 接入正式安装与 health。正式采用前须由集成任务同步安装授权与 readiness 检查，再用真实 reader 验证。不得用测试 owner 角色通过替代此验收。

### 后续候选接线（源码，未生产采用）

现已在 `tools/django-local-service.ps1` 的真实 `Invoke-DjangoMigrations` 内 `$grantCode` 中接入 `grant_actor_read(c)`，紧跟 finance reader 原表 SELECT；这是受控安装/迁移后执行的业务权限设置，不是其他域控制器。独立 `ProvisionFinanceRoles` 负责创建/配置登录角色和秘密，表/列权限仍由 `Invoke-DjangoMigrations` 统一重置后授予；不存在单独 finance-service.ps1。writer 没有新增账号授权。

`health.py` 的实际 `finance_reader_process` 分支新增权限门禁：所需五列及四张来源表可读，其余用户列不可读，用户及四表均无 INSERT/UPDATE（含单列授权）、DELETE/TRUNCATE。writer 分支不调用。新增5项隔离 PostgreSQL 测试待root执行，包含实际 SELECT/拒绝写和显示名、撤销 version 列导致 health 失败、额外敏感列/单列事实写授权/缺事实读取的拒绝。仅源码候选接线，未执行正式 Provision/Migrate/Restart。

## 验证状态

已通过静态编译。root 首次隔离 PostgreSQL 9 项中 8 项通过，1 项暴露退出复验时发布元数据失效泄漏纯合同异常（`.runtime/ai-pg-b96262e6d6d3/failure.log`）。现已在复验内部映射为稳定 409，并在元数据与 revision SQL 之后再次校验账号，保留调用方异常原样传播。`finance.tests.test_business_analysis_source` 增至 11 项，待 root 串行复测。覆盖真实规范导入、多页/空值/只读 SQL、缺月/未发布、账号权限、完整摘要、发布关系、页间变化、精确组隔离、最终读取期间撤权、调用方异常及临时最小角色的允许五列读取/禁止显示名称和写入。子 Agent 未运行 PostgreSQL 或生产数据库。

## 主线程隔离复测

2026-09-18：11项真实PostgreSQL测试全部通过，与1项ERP最小角色测试合跑共12项；日志 `.runtime/ai-pg-faf9c2a5417d/tests.log`。首次9项中8通过、发布状态改变时错误类型未映射的1项失败，已在所属服务复验内修复为409；调用方异常仍原样传播。未运行生产、模型或正式权限安装。

候选随后将最小五列账号读取授权接入实际 Django 统一迁移授权路径，并在 finance reader 健康检查中验证五列、四张来源表只读及禁止额外用户列/业务写入。新增5项权限测试与原11项 owning 测试合跑共16项通过，日志 `.runtime/ai-pg-8e6b283775b4/tests.log`。writer权限未改变；尚未部署或重启正式服务。
