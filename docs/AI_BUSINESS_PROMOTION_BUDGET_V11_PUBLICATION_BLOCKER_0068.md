# renderer 11 发布门禁：0068 仅验证回执，仍不开放发布

截至 0067，`ai_business_promotion_budget_v11_attestations` 是当前暂存根的追加式声明，不是不可伪造的拥有方全字节验证回执。后续 0068 仅加入空密钥的受保护验签候选，仍不开放发布或下载函数，也不给任何角色扩大文件表权限；0066 的两处 `ready` 拒绝保持。

## 精确阻断原因

`promotion_budget_attestation_v11.compose` 接受调用方给出的 `fileByteVerificationDigest`、`htmlRowsDigest`、`xlsxOpcFormulaDigest` 和 `fresh_semantics_verified=True`，再用无密钥 SHA-256 生成 `owningVerificationDigest`。0067 的 SQL 对前三项与拥有方摘要仅检查 64 位十六进制形状，不能重算 HTML gzip/NDJSON 解压、XLSX OPC/公式检查或拥有方重新生成。`attestationSha256` 也只是明文声明的无密钥摘要。`business_promotion_budget_v11_preflight.prepare` 确实执行了这些检查，但 SQL 无法证明某条 0067 证明来自该受保护代码路径。独立 NOLOGIN attestor 限制谁可插入，不使插入的过程声明变成不可伪造的验证器回执。

纯测试以完整的合成 manifest 输入和自行选择的 `1…/2…/3…` 摘要获得结构合法但 `readyAuthorized=false` 的证明；隔离 PostgreSQL 真实角色目标进一步展示拥有全部真实来源根后，直接向 0067 函数提交替换的过程摘要能够插入证明，而 v11 `ready` 更新仍被 0066 守卫拒绝。这是现有边界的反例，不是允许发布的测试。按分工，PG 目标只编写，尚未运行。

## 发布前的最小准入条件

1. 在受保护拥有方验证器内生成**新版本**的不可伪造回执；验证器亲自重建当前五 Agent 批准内容、固定预算和完整 HTML/XLSX 字节，逐块核 SHA、解压 HTML 行、验证 XLSX OPC/公式。签发能力不得暴露给 attestor、publisher、writer 或普通 Django 请求身份，也不能成为任意正文签名接口。
2. 回执绑定 `runId/attempt/runVersion/reportId/owner/bindingDigest`、当前报告与流程根、批准内容与人审根、预算根、compact 与 full manifest SHA、每卷 HTML/XLSX 字节 SHA、0067 证明 ID/正文 SHA、验证器版本及一次性发布意图。数据库须能独立认证签名或 MAC，密钥不能落到这些角色可读表；回执要有明确轮换、撤销和未知结果处理。仅重复 0067 的无密钥摘要或另一个自报布尔位不合格。
3. 再引入单独 `NOLOGIN NOINHERIT` publisher，只能执行一个新版本 SQL 原子 staged→ready 函数和窄 `OUTCOME`。函数在同一事务内锁当前 run、证明、验证器回执及来源根，重算发布请求摘要并 CAS 更新；`OUTCOME` 对原请求给出 `committed/not_committed/conflict/unknown`，响应丢失不重发发布。普通 writer 和 attestor 直接 `ready` 始终拒绝。
4. 迁移前冻结旧 AI 表、renderer 1–10 已有文件字节、0066/0067 函数 OID/正文/ACL/owner、角色与表列权限；升级只能增加显式列出的 verifier/publisher 目录和精确替换两个 v11 文件守卫，不改旧版本分支。前后独立备份恢复、空逆迁移重装、旧 v9/v10 下载和 v11 staged-only 回归、真实独立角色正反测试均须通过；存在任意已发布 v11 行时禁止逆迁移。

0068 已分配给默认关闭的验签候选，发布须使用后续独立迁移并满足上述条件。当前不接公共路由、UI、生产、付费模型或客户数据。即使发布门禁完成，下载窄栅栏、57.5 万行来源容量、原生 Office 公式复算仍需单独验收。

## 本分支验证入口

纯测试：`cd backend; python -m unittest business_analysis.test_promotion_budget_attestation_v11.BudgetV11AttestationPureTests.test_process_digest_is_a_claim_not_an_unforgeable_verifier_receipt`

隔离 PG 目标：`python tools/ai-postgres-rehearsal.py --tests-only --preprovision-ai-runtime-roles --test-label ai_assistant.test_business_promotion_budget_v11_attestation_role.BudgetV11AttestationRoleTests.test_0067_process_claim_can_be_replaced_but_ready_stays_denied --test-timeout-seconds 1200 --port <独立端口>`。按任务边界，本分支不运行 PG；不能将目标当作已通过的结果。
