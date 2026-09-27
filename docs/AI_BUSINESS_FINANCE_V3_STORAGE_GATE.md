# 财务 v3 目录存储门禁候选

2026-09-24。`ai_assistant.0028_business_finance_v3_gate` 仅使一个严格的 `business-evidence-v3` **待采集计划及初始目录**能写入现有三张证据表。它没有公共创建入口、财务 HTTP 读取、采集器、续读、封存、报告、模型或文件授权；v3 事实块插入、检查点推进、sealed/cancelled 更新都在 PostgreSQL 拒绝。当前 `business_evidence_store.is_v2` 也会拒绝此版本。该阶段不能向用户提供可运行的财务分析任务。

迁移依赖已存在的 `0027_business_promotion_file_guard`，不新增表、字段、角色或权限。保留旧 v1/v2 表内字节；扩展 `ai_business_source_bound` 的域枚举包含 finance。新的 `ai_business_v3_header` 严格校验已定的 header 字段、固定容量及所有 false 能力标志。`ai_business_source_guard` 的 v2 分支保持原逻辑，v3 分支只允许父任务版本1、当前账号仍为 active 无范围管理员时插入零计数目录；finance 要求连续1—24自然月、完整四字段 scope 和与日区间匹配的 `analysisPeriod`。独立延迟约束核验日/财务来源至少各一个、连续 ordinal、精确来源数量、日期一致、空证据块与零计数。v2 父任务不能借新域写 finance；旧两域事实链触发器仍在。

此门禁故意没有把 v3 `catalogDigest` 当成数据库独立重建的事实证明。应用侧 `business_analysis.evidence_v3` 会从可信来源重新构造规范目录与摘要；在真正的 collector、Reader 和 DB 二次守卫能证明这一点之前，v3 块写入始终被禁止。管理员身份只核验真实 `access_control_users` 的 email/role/status/scope/version；这不替代正式运行时当前 principal、报告 owner、身份版本及签名请求核验。数据库创建的 v3 行也没有来源数据、账号永久授权或财报已发布的含义。

逆迁移仅在没有任何 v3 计划或 finance 来源时允许；有未采集/取消的 v3 行也拒绝逆迁移。空逆迁移恢复原域约束、原 source guard 与旧触发器；重新安装后旧 v2 插入仍正常。备份清单仍是原三张证据表，无新增表或权限。正式采用时要让独立 PostgreSQL 备份及恢复核验旧表内容摘要和数据库迁移图，不能用纯合同或当前隔离测试替代正式恢复证据。

后续单独迁移才能实现：finance reader 的签名分页、每页真实管理员与完整 revision/month→batch 前后复验；AI owning 的独立 finance 检查点、CAS 追加、完整页链与容量、末块封存/独立 Reader；最后才是五 Agent 报告 guard、读取回执、HTML/XLSX。原财务内存源和纯证据对象都固定 `persistentEvidenceVerified=false`，不能因目录存储就改成 true。月财务仍按完整自然月与日经营窗口并列，缺月是缺口、部分月不分摊，合计/明细与源比率不相加，也不反推 SKU 利润。

隔离验证：`tools/ai-postgres-rehearsal.py --tests-only --test-label ai_assistant.test_business_finance_v3_gate --test-label ai_assistant.test_business_source_directory --port <隔离端口>`。测试包括 v3 初始目录、错账号/范围/月份/日期/域、块和封存拒绝、空逆迁移及含 v3 行逆迁移拒绝、旧 v1/v2 插入、权限 ACL 未变。未在正式库迁移或写入任何数据。

2026-09-24 隔离实测：上述新旧门禁共21项通过，日志 `.runtime/ai-pg-47d701ab8c96/tests.log`；包含已有不规范旧计划仍按旧数据库语义处理，而不规范 v3 计划拒绝。`--business-finance-v3-upgrade --upgrade-only` 独立演练先建 0027 合成 v1/v2 行，升级前备份并恢复，再升0028核对全部65张 AI 表旧行摘要及ACL、空逆迁移后重装、建无事实块 v3 目录、升级后备份独立恢复、最后证明带 v3 行逆迁移拒绝；最终结果 `.runtime/ai-pg-12bebf172e47/business-finance-v3-upgrade-evidence.json`。演练未使用真实财务行，不证明未来 v3 收集/报告已经可运行。
