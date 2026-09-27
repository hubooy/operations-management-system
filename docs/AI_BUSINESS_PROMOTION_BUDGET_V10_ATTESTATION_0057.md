# 0057：renderer 10 发布前不可变证明（仍禁止 ready）

0057 依赖 0056 市场权限修复；它不修改 0054 的版本 10 `ready` 硬拒绝、版本 9 发布路径或任何文件字节。新增独立角色 `teruisi_ai_budget_v10_attestor`，固定为 NOLOGIN、NOINHERIT、无成员、无普通表权限；普通 AI writer/reader 和 PUBLIC 都不能调用证明函数。没有配置登录凭据、专用进程或公开路由。

新表 `ai_business_promotion_budget_v10_attestations` 对 `(run_id, attempt)` 唯一，INSERT 后拒绝 UPDATE/DELETE/TRUNCATE。只有专用角色通过受限 `SECURITY DEFINER` 函数才可写入。函数锁定当前 `staged_unpublished` 文件任务，复核已批准且完成的同报告五角色、人审、预算有无与固定预算行、任务绑定和尝试，调用现有数据库完整分块布局检查；重新读取最多 16 MiB 的完整 JSON 分块并计算 SHA，与紧凑清单及传入证明逐字节核对。证明保存每个卷文件的 SHA/大小/分块数、预算 proof、批准内容、人审、预算计划、拥有方复验摘要及发布栅栏摘要。相同字节的同一尝试可幂等回读，任何不同证明拒绝。

**此记录仍只是默认关闭的候选证明。** 数据库没有解析 HTML/XLSX 或重放全部店铺事实；`owningVerificationDigest` 和 `publicationFenceDigest` 由未来受保护的拥有方验证器提供，0057 仅核它们的形状与当前持久身份。角色保持 NOLOGIN，生产不能调用；隔离测试中的 `SET SESSION AUTHORIZATION` 只验证角色边界，不是正式凭据启用。未来发布须先有受保护验证器真正执行 `_verify_staged`、生成当前预算与审批栅栏，再以独立身份写证明；后续迁移另建同事务 ready 门禁和签名下载。不得仅因存在本证明就放开 ready。

验收须覆盖角色属性/成员/ACL、普通 writer 直写拒绝、伪造文件/预算摘要拒绝、同尝试冲突回放拒绝、不可修改、实际有预算与无预算的证明及两者始终 paused；0056→0057 升级与前后独立备份恢复、旧版字节/权限、空证明逆迁移。存在任一证明时逆迁移拒绝，角色留作审计。真实来源规模、原生 Office 许可和正式受保护验证器均未在本切片解决。

隔离 PostgreSQL 三项目标 `.runtime/ai-pg-35577d49eae2/tests.log` 通过（216.746 秒），SQL 静态合同一项与备份 helper 22 项通过。首轮新函数的 PL/pgSQL 局部变量 `full` 触发语法错误，改名为 `full_manifest`；下一轮测试辅助连接误把 Django 测试库当基础演练库而主动拒绝，已固定为精确测试库名与隔离端口。两次失败均不算验收。

0056→0057 独立升级与恢复 `.runtime/ai-pg-1622c6faccd4/business-promotion-budget-v10-attestation-upgrade-evidence.json` 通过：旧80张 AI 表及 renderer1—7 字节、全部旧 AI 函数OID/正文/ACL、v9 ready拒绝与v10 ready关闭保持；新第81表、NOLOGIN无成员角色、两函数两触发器精确权限、前后备份独立恢复与空逆迁移再升级通过。脚本使用冻结80表与当前81表清单，不将候选证明误报为已发布。
