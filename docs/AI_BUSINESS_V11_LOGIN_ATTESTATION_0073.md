# v11 独立登录证明侧车（0073，默认关闭）

`0073_business_promotion_budget_v11_login_attestation` 只新增一张受保护证明表、一个初始 NOLOGIN/无密码/零成员的独立角色和四个新函数。旧 0067/0068/0070 函数及权限、v11 的 ready 双守卫与下载拒绝保持不变。正式 `PrepareApp`、`DeployApp`、普通迁移、备份和恢复仍会在此候选出现时提前拒绝。

测试专用管理员仅在精确隔离 PostgreSQL 库临时给新角色随机凭据与 LOGIN；真实非超级用户可对已暂存且根摘要匹配的 run/attempt 追加独立证明，并窄查 `committed`、`absent_observed` 或 `conflict`。三态都不允许自动重试。旧证明表、私钥表、票据表和既有文件表不能由该角色直接读写；旧触发器函数虽在 PostgreSQL 的 PUBLIC EXECUTE 元数据中可见，也不能直接调用。默认目录核验拒绝激活的 LOGIN，只有显式 test 模式可在精确测试库与回环端口验它。

隔离真实角色 2 项、补充拒权 1 项通过；两个有效暂存报告间错用证明拒绝。`0072→0073` 升级演练通过预存密码角色的原子失败、旧函数 OID/正文/ACL/owner 和旧文件块字节冻结、前后独立恢复、空表逆迁移重装，以及正式备份在写归档前拒绝。证据分别在 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-2fb50746cca9-v11-login-role\tests.log`、`E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-c46768d40619-v11-login-negative\tests.log` 与 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-8a07db77de95-v11-login-upgrade\business-v11-login-attestation-upgrade-evidence.json`。这些只属于合成隔离证据。

新版显式 0073 第二全新集群演练也通过：13 个受保护角色、9 张表、旧合成私钥 1 行及新空证明表的 owner/ACL 保留；新表 owner/函数 EXECUTE 故意漂移拒绝，AES-256-GCM v2 分块密文的六类损坏输入均拒绝且明文 dump 为 0。两端 PostgreSQL 停机，证据在 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-3867919323eb-protected-v2-0073\evidence.json`，SHA-256 `cc18231f0ae924a8eee14a399fcc407582c353fd5100666d74627ed02166e5b6`。此轮新证明表为空，不能据此声称非空证明行已完成异集群恢复。

正式启用仍缺精确单报告/店铺的一次性工作许可、可持续保管的独立签名密钥、全文件字节和来源根的可信签发、锁内原子发布/结果查询、窄下载回执及 0073 受保护库的正式跨集群可恢复备份。现有 SQL 对部分进程计算摘要只做形状与绑定核验；不能把侧车证明当成可交付文件已完整复核。新角色在正式环境必须保持 NOLOGIN，ready、下载和自动经营调整继续关闭。
