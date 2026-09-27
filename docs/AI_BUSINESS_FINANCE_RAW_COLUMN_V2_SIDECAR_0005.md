# finance.0005 原始列证据摘要侧车候选

`finance.0005_raw_column_evidence_v2` 只新增三张 `finance_raw_column_evidence_*` 表，不修改或回填 `FinanceLine`、`FinanceMonth`、`FinanceImportBatch` 和旧 v1 导入。月份主行绑定**当前已完成**的月→批次、原文件自报 SHA、规范内容摘要、发布 token、完整财报 revision；子表按列索引和 `(section,rowIndex,columnIndex)` 保存 SHA-256。数据库触发器禁止三表 UPDATE、DELETE、TRUNCATE，直接 finance reader/writer 不获新表 DML。仅测试环境、显式 `enabled=True` 的内部 `finance.raw_column_evidence_v2.stage/read` 可用，没有公共路由或真实业务数据接入。

第一版只接受**完整单月候选且当前批次也仅发布该月**。它重新核 Worker 候选与逐月证据摘要、列/格数量和唯一坐标、跨组同名碰撞；将格金额、比率、来源行数、首格原文及金蝶费用父行的来源值回卷到原 v1 规范行，并同当前已发布 `FinanceLine` 全字段逐行对照。相同月/批次/候选摘要重复提交可返回旧回执；不同摘要或不完整输入全部拒绝。读取时再次核当前批次及全局 finance revision，后续任何财报重导或版本变化使旧侧车不可作为当前材料。所有输出固定 `rawWorkbookBytesIndependentlyVerified=false`、`stableNetshopShopIdentityVerified=false`、`mappingAuthorityVerified=false`；Worker 给出的原 XLSX 哈希仍是自报，后端没有看到原始字节。

正式 PrepareApp、DeployApp 和普通 `migrate` 在源码发现精确 `finance.0005` 时先拒绝，隔离测试目录不受影响。正式备份/恢复在 `finance.0005` 迁移收据或任一侧车表出现时继续提前拒绝：备份在 `pg_dump`/文件工作前、helper 恢复在实际 `pg_restore` 前、维护操作员在隔离集群 `initdb` 前拒。旧无 `finance.0005` 的 v1 备份路径不变。新表目录、专用恢复身份/ACL 与异集群恢复尚未受控验收，**不能发布本迁移**。多月批次、历史原文件缺失、跨组同名源列本身的独立真实性、网店稳定店铺 ID 和 v4 可报告财报映射均未解决。

2026-09-26 隔离真实 PostgreSQL 终验：唯一源端口 55772 的 7 项测试全部通过，日志 `.runtime/ai-pg-df8f201373e3/tests.log`；测试库销毁、`pg_ctl` 回读 `no server running`，端口无监听。测试覆盖新表三类 UPDATE/DELETE/TRUNCATE 触发器、触发事件与函数正文/owner/PUBLIC 和四个应用角色的表/列 ACL 漂移、独立连接以 `teruisi_finance_writer` 实际 INSERT 获 SQLSTATE 42501。完整合成 Worker 候选的表头继承、首格原文与金蝶父行金额回卷、缺格/改声明、附加子格后的幂等重放、财报 revision 与月→批次变化均拒；正例写前后 `FinanceLine`、`FinanceMonth`、`FinanceImportBatch` **全部字段** SHA 一致。旧正式备份 Python 29 项、维护/生命周期 Node 55 项、正式目录 finance.0005-only 动态拒绝及 Django `check`/迁移清单也通过。所有客户原始值仅存在于测试合成候选，不在新增侧车表中保存；原 XLSX 字节、真实多月批次、稳定店铺映射、正式备份恢复和生产发布仍未证明。

隔离角色测试日志另已归档至 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-df8f201373e3-finance-raw-v2-tests\tests.log`，SHA-256 `a7107ded1528aff8c4726f1bcbdd27803973c89a648c575a89534a28a08693dd`；同目录 `status.json` 只含无凭据命令、7 项结果及停机摘要。复制前后日志 SHA 一致。

整合分支加入 `ai_assistant.0074` 后，现行隔离 PostgreSQL 测试 runner 的全库清理会碰到 finance.0005 无条件 TRUNCATE 守卫。仅在精确 `test_teruisi_ai_rehearsal`/回环端口的测试 runner 中，清理事务先核三表触发器均启用，再暂时关闭它们的 TRUNCATE 事件、执行原清理并重新启用；失败由 PostgreSQL 事务回滚，生产迁移函数及正式连接不变。使用原始 0074 角色用例 5 项、财务侧车 7 项和目录共存 1 项在**同一隔离测试运行** 13/13 通过，源端口 55792 已停。最终日志 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-business-0074-finance-0005-combined-runner-20260926\tests.log` SHA-256 `0ae130b2c86421b547a4b5b4b2a499d92af42b3da7a892254993f96166040cac`。这证明合成测试共存，不证明正式 finance.0005 备份/恢复。
