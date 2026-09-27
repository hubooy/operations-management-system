# 0058：renderer 10 原子发布门禁（默认不可用）

0058 只增加版本 10 的数据库发布路径，不注册公开创建、控制、下载或 UI，不修改版本 9 ready 分支。已暂存且已完整核验的同一 `(run, attempt)` 必须先有 0057 不可变证明；普通 AI writer 直接把文件任务改为 `ready` 仍由数据库拒绝。专用 `teruisi_ai_budget_v10_attestor` 继续 NOLOGIN、无成员且无普通文件表 DML，因此生产尚无法调用发布函数。

受限 `ai_budget_v10_publish` 接受同一任务、尝试、预期版本、证明 ID/SHA 和逻辑请求摘要。摘要必须是 `business-budget-v10-publish-request-v1` 固定字段的规范 JSON SHA-256，绑定任务、尝试、**原始预期版本**、证明 ID/SHA、报告绑定、完整清单摘要及完整 JSON 文件 SHA；纯 Python 与 PostgreSQL 使用同一有序规范正文。函数锁定任务，按当前完整审批、人审、封存证据、预算存在性/计划、证明及紧凑清单与所有分块核对；在同一事务内精确 CAS `paused/staged_unpublished` → `ready`，把请求摘要、证明身份、完整 JSON SHA、预算拥有方复验摘要和发布栅栏写入 ready 回执。ready 回放只接受 `ready.version - 1` 所代表的原始预期版本和同一摘要。现有文件触发器只为版本 10 增加 `session_user` 与同尝试证明检查，延迟完整性触发器提交时再次重算摘要；版本 9 的函数 OID、ACL 与 ready 条件须保持。网络结果不明时，用仅限专用角色的 `ai_budget_v10_publish_outcome` 以原请求摘要和原证明查询 `committed / not_committed / conflict / unknown`；它从 paused 当前版本或 ready 版本减一重算摘要，不接受任意64位字符串。不得生成新请求盲重试。

数据库能独立核证明行、当前审批结构、预算与封存根、JSON SHA、紧凑卷描述符及不可变分块布局；它**不能独立重放店铺事实或解析 XLSX 公式**。0057 的 `owningVerificationDigest` 由未来受保护的外部验证器生成，0058 不将任意摘要当作语义真实性。验证器必须先执行完整 `_verify_staged`，复核人审与固定预算、HTML 字节和 XLSX 各 ZIP 条目，之后才用受保护的专用身份写证明并发布。角色登录/凭据启用、实际应用 publisher 和下载栅栏另行受控开发；本迁移本身不授予它们。

延迟分块触发器在 `SECURITY DEFINER` 发布函数返回后的提交阶段，以专用 NOLOGIN 角色作为 invoker 执行；该角色没有文件/分块表 SELECT。0058 仅把 `ai_business_volume_complete_guard()` 这一固定目标、只读完整性触发器改为受保护数据库所有者执行，并精确保持其 OID、正文、ACL 和所有者。没有向验证角色授予宽泛表读取；v9 分支和旧函数继续核原有条件。

隔离 PostgreSQL 验收须包含：无预算和真实固定预算两路同尝试成功、普通 writer 及角色直表 UPDATE 拒绝、错证明/错尝试/错版本/预算变化拒绝、相同请求幂等、网络结果未知后的精确查询、旧 v9 ready 回归，及 0057→0058 前后独立备份恢复、原函数 OID/ACL、空 ready 回退再安装。存在 v10 ready 行则 0058 禁止逆迁移。正式发布还需真实规模耗时及持证 Microsoft Excel 原生重算验收；现行参考 30 天 575,095 行超过 v2 证据整任务容量，不得截断冒充通过。

整合分支已完成真实角色隔离 PG 两项 `.runtime/ai-pg-6646a50f5d94/tests.log`（215.891 秒）、纯请求/SQL合同三项及0057→0058升级恢复 `.runtime/ai-pg-423a50ee3e05/business-promotion-budget-v10-publish-upgrade-evidence.json`。首次运行发现旧延期完整性触发器以调用者权限读取文件表，独立角色无SELECT导致提交失败；现仅把这只固定只读触发器改为受保护所有者执行，OID/ACL/owner保持，并将发布请求摘要改成绑定任务、尝试、原始预期版本和证明的版本化规范摘要。最新0058数据库上的旧 v9 多卷与签名下载八项回归 `.runtime/ai-pg-c21f2c3f7847/tests.log` 通过（592.822 秒）；首轮忘记预置0056要求的reader/writer角色、用例未开始，不计失败业务路径。

应用层默认关闭的调用器在同一隔离库以真实 NOLOGIN 角色完成四项端到端测试 `.runtime/ai-pg-e5a8482f4cf5/tests.log`（806.728 秒）：一次预检、一次证明、一次发布和精确 OUTCOME；禁用及预检版本漂移零调用；0057 或 0058 响应丢失时均不重试写入。0058 响应丢失后的显式恢复只查询原请求摘要。此测试不代表正式凭据、生产采用或下载已启用。
