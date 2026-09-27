# renderer 11 独立暂存证明：0067 候选交接

本切片依赖 `0066_business_promotion_budget_v11_durable_stage`，只为 `staged_unpublished` 增加独立校验与追加式证明；**不打开 ready、发布、下载、公开 API 或前端**。正式数据库、模型提供商、原生 Office 与客户数据均未触及。

## 固定边界

- 新角色 `teruisi_ai_budget_v11_attestor` 是独立 `NOLOGIN NOINHERIT`，无成员关系、无表或列直接权限。只有它可执行两个新窄函数 `ai_budget_v11_attestation_requirements` 与 `ai_budget_v11_attest_staged`。0066 的 writer-only 函数正文、OID 与 ACL 不变；旧 renderer 1–10 的文件行、函数和权限没有修改。
- 新 `ai_business_promotion_budget_v11_attestations` 每个 run/attempt 唯一、只准函数插入；重放相同规范正文返回同 ID，冲突正文拒绝。UPDATE/DELETE/TRUNCATE 禁止；逆迁移遇到任何证明行拒绝。数据库再次核当前已暂存行、版本/尝试/管理员/已完成报告、五 Agent 已批准报告要求、预算根、compact 与完整 JSON 的字节 SHA、文件描述符、v11 slim/budget 证明及分块门禁。
- 默认关闭的 `business_promotion_budget_v11_preflight.prepare` 在事务外重新读取当前批准内容与固定预算，重建每个持久卷；逐块核序号、大小、SHA，流式解压 HTML gzip/NDJSON 并核行摘要，重新生成当前 HTML/XLSX 后逐 HTML 字节和 ZIP 成员内容比对。额外逐 XLSX 成员读取 CRC/解析 XML、核 OPC 基本结构和试算公式存在性，输出过程摘要。前后复验任务版本、管理员与来源根。`business_promotion_budget_v11_attest_step.attest_staged` 仅接受注入的已认证 autocommit 连接，核独立 session 身份后调用 SQL 一次；响应丢失为 unknown，不盲重试。
- PostgreSQL 能独立复核清单、行/预算证明和角色，却不能独立证明 Python 确实解压了 HTML、比对了 ZIP/公式或重新生成了来源文件。`fileByteVerificationDigest`、`htmlRowsDigest`、`xlsxOpcFormulaDigest` 与 `owningVerificationDigest` 均是受保护进程声明，不能单凭其存在授予交付权限。0066 的 v11 `ready` 硬拒继续生效。

## 本分支验证与整合门槛

本分支运行纯/static 与旧 v11 候选回归 13 项、编译、Django system check 和 `makemigrations --check --dry-run`；**按主任务串行安排，未在本工作树运行 PostgreSQL**。已编写 `BudgetV11AttestationRoleTests.test_0067_owning_bytes_and_independent_role_append_only` 作为隔离真实角色目标，整合时须先验 0066 已暂存，串行执行该目标并核正反路径；再做 0066→0067 旧表/旧 1–10 文件字节与函数 OID/body/ACL/owner 冻结、前后独立备份恢复、空逆迁移重装和旧 v9/v10 回归。独立 NOLOGIN 连接的正式凭据、受保护部署与生产迁移均不在本切片授权范围。

后续仍需新的版本化 0068+ 发布门禁、reader 窄下载栅栏及保护路由、v11 真实来源 57.5 万行容量与构建时限测量、原生 Office 公式重算验收。0067 证明不能代替这些门槛。

## 显式隔离升级演练入口

`tools/ai-postgres-rehearsal.py --business-promotion-budget-v11-attestation-upgrade --upgrade-only` 将先重放并独立恢复 0065→0066 的完整链，再运行 `business-promotion-budget-v11-attestation-upgrade-rehearsal.py`。后者仅接受精确 0066 成功回执及隔离 test 数据库/端口，冻结旧 85 表、当时存在的 renderer 1–11 文件行/块字节、全部旧 AI 函数 OID/正文/ACL/owner、表/列权限、角色及成员关系；种子缺失的 renderer 8–11 会列在 `unseededRendererVersions`，不冒充字节验收。预期只新增一张 SQL-owned 证明表、其索引/两个触发器、三个窄函数及一个 NOLOGIN 角色；旧 0066 writer 函数与 v11 ready 双守卫须原样。升级前后分别备份到全新库独立恢复，空表逆迁移回 0066 后重装并比对。**该脚本已编写但尚未运行 PostgreSQL**，不能把其预期断言当成已通过的证据。
