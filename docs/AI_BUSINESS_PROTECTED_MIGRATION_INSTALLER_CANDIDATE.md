# 0067–0073 受保护迁移安装器候选（仅隔离）

`tools/protected_ai_migration_installer.py` 固定已审源码摘要、13 个初始 NOLOGIN/无密码/零成员角色及 0067–0073 七步顺序。`tools/protected_ai_migration_isolated.py` 仅接受 `test` 环境、回环 55440–55999、精确合成克隆库 `teruisi_ai_migration_role_probe`、受控 `.runtime/ai-pg-<12hex>` 目录和注入的合成管理员/普通迁移账号。URL 只从进程环境读取，不从命令行接收或输出。0074 及以后不在白名单，源码、Django 计划、迁移收据或角色目录有额外/逆向/缺失项即拒绝。

安装器一次只尝试一步：普通 `teruisi_sales_owner` 仅用于 0067/0069/0071/0072；隔离特权账号预置角色并仅用于 0068/0070/0073。它不向普通 Web、迁移 owner 或日常备份身份授 `CREATEROLE`、KEY_OWNER 成员资格或私钥表 SELECT；当密钥表存在时还实测普通迁移身份无 SELECT。每步先用 create-only 步骤锁及本地账本记 `prepared`，结果异常或回执/目录不精确均记 `unknown` 并停止；新连接显式 `audit-unknown` 只确认精确已提交前缀，未见收据仍保持 unknown，绝不自动重发。账本仅含标识和 SHA，不含数据库 URL、密码或原始行。

这是测试执行器，不是生产维护授权。`--approved-plan-digest` 只确认合成计划未变，**不是管理员签名或生产审批**；本地 JSON 账本尚无正式受保护 ACL/签名和跨进程操作生命周期门禁。隔离代码和迁移 SHA 固定于 `de7ed95e`；换源码必须重新审查并更新白名单。正式 `PrepareApp`/`DeployApp`/普通 `migrate`、v1 备份与恢复仍按原栅栏拒绝受保护迁移。私钥表正式备份只能由受控特权进程或经证明等价的机制处理，不能扩大普通账号权限。

纯/静态 15 项测试覆盖并发单消费者、篡改账本、源码与 0074 漂移、计划与角色漂移、未知提交和未提交后不重试，以及从仓库根和 `tools` 目录启动 Django。唯一隔离 PostgreSQL 演练 `ai-pg-6513bfe33917` 在端口 55786 通过：从 0066 精确合成克隆库，使用真实普通非超级登录与隔离管理员，按七步正向安装，13 个受保护角色维持关闭；0068 提交后故意丢失响应，显式审计已提交目录和回执后才继续，没有重放；普通迁移身份对私钥表 SELECT 被拒。演练结果 `formalAllowed=false`、`productionWrites=false`，源 `pg_ctl status` 回报 no server running，端口无监听。完整 3,520 文件逐 SHA 匹配归档在 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-6513bfe33917-protected-installer`；`protected-installer-isolated-evidence.json` SHA256 为 `D0D1AA86C69DC23A4FF7AE0115A05F5E3B55262F2EEB9D6FA8522FCF4204BD0D`。

这次演练只注入 0068 提交后未知结果；0068/0070/0073 预置角色与迁移回执前后的全部故障矩阵、旧函数/文件字节及跨集群 owner/ACL 比对仍需后续专门验收，不能由上述通过推断。生产采用还需正式受保护 ACL/签名账本、独立流式加密备份及异集群恢复、长期密钥托管、精确操作身份与维护批准；这些均未由安装器提供。
