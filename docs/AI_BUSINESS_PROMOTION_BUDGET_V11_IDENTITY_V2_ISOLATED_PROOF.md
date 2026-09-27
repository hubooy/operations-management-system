# renderer 11 三身份 v2 窄读：仅隔离试验

本文件记录 0070 之前的无迁移原型；持久、仍默认关闭的后继方案见 [0070 交接](AI_BUSINESS_PROMOTION_BUDGET_V11_IDENTITY_0070.md)。下文仅适用于原独立试验，不表示正式身份已启用。

本切片依赖已整合的 0067/0068，但**不添加迁移、不分配 0070、不开 LOGIN 或生产凭据**。原型仅在隔离 test 数据库安装，测试结束删除候选函数、票据表和角色。0067/0068 原函数、OID/正文/ACL/owner、renderer 1–10 与 v11 `ready` 双拒保持原样。

原型创建三个独立、无成员且初始 `NOLOGIN NOINHERIT` 的角色：`attest_login` 只能为已暂存且已有 0067 证明的**精确 run/attempt/SHA**签发一个十分钟票据；`sign_login` 只能一次性领取该票据、返回同一 0067 证明原文及必要的 run/report/owner/binding 标量；`publish_login` 只能调用只读的 `verify_protected_receipt_v2`，验同一受保护回执。三者无业务表/证明表/票据表直读或 DML，无私钥表权限、无 SQL 签名函数，也不能继承彼此或 Web reader/writer。v2 验签函数复制 0068 的检查但只改专用登录身份谓词，并非发布函数，仍不锁活动 key，不授予 `ready` 或下载。

隔离 PostgreSQL 目标会先核三个角色确为 NOLOGIN，再用测试专属随机密码显式临时改为 LOGIN；关闭管理连接后，三个真实非超级用户连接分别证明 `session_user=current_user=本身份`，完成精确票据签发、单次领取及合成回执验签。错误 attempt、二次领取、错误 MAC、跨角色函数/表读取和角色切换均拒绝，测试结束撤销 LOGIN 并清除仅在隔离库使用的合成 key。测试 SQL 函数的 definer 仍是隔离库安装管理员；正式迁移须另设不可登录且只具精确读写权限的函数所有者并复核 `SECURITY DEFINER` 搜索路径。这个测试**只证明普通登录调用方的数据库窄身份可执行**，0067 证明和 HMAC 回执仍由测试管理连接准备；不能把它写成生产独立验证器端到端验收。

明确剩余阻断：当前 `preflight.prepare` 仍通过默认 Django ORM 读取报告、五 Agent、人审、预算、卷块与来源；`sign_after_preflight` 仍 ORM 直读 0067 表。一个证明票据窄读不能让 signer 在非超级用户连接下完成整个 full-byte preflight，更不能替代来源读流、签发凭据隔离和签后当前根复验。旧 0067 ATTEST 仍要求原 NOLOGIN `session_user`，本原型的 attestor 只发行已存在证明的票据，**没有实现非超级用户证明写入**。票据签发响应未知时也缺少原票据 OUTCOME，不能盲目再发；签发所用的管理员/报告授权仍需受保护工作流入口。

正式后继设计需独立迁移版本化 0067 证明写入身份、精确同票据的所有来源/卷块只读流，并为票据签发与领取建立响应未知恢复；随后在隔离库完成不使用超级用户 `SET SESSION AUTHORIZATION` 的全链路正反验收与旧版本冻结、备份恢复。0068 当前验签没有活动 key 行锁，不能供 0069 原子发布使用。此切片不触碰生产、不生成真实凭据、不开放路由、模型或 Office。
