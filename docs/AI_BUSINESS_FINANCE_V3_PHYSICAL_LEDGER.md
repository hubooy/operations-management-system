# v3 财务来源物理页账本候选

2026-09-24。`ai_assistant.0030_business_finance_source_pages` 只放开现有 v3 证据表中 `domain=finance` 的一页一检查点递增，日来源仍必须保持零页。新 SQL 守卫要求真实 active 无范围管理员、父任务 `collecting/manual`、父及来源版本各推进一次、先追加不可变块再更新来源检查点和父字节计数；来源只可在完整页后 `finished=true`。每块上限38,000字节，最多1,999个数据块，为未来终结清单预留一块和38,000字节。父任务不能 sealed/cancelled，也不能生成报告。

该迁移不新增表、列、角色或权限。v1/v2 源守卫原分支与既有事实块语义保留；反向迁移遇到任何 v3 财务事实或推进过的财务检查点时拒绝。新守卫校验块属于同一 finance source、序号连续、范围与来源查询对应、基本页协议/摘要形状、检查点计数与末页/父任务一致。完整逐行摘要和业务来源真伪超出 SQL 结构约束：只有内部只读 `business_finance_collection_v3.inspect` 会从实际不可变块从首页顺序重新运行 `finance_collection_state.consume`，核对规范 JSON、每行原值与 rowId、页 SHA、来源/版本/批次、偏移、月度覆盖、行链、累计字节和检查点原文，再提供下一页参数或当前来源完成结果。

这一片最初**没有可信 append API**。当时签名财务页在 Worker 的独立 TS 内部适配器中，尚未注册 AI Django writer 可以调用的 `business_collection` 工具或配置直接签名的 finance_reader 通道。若接受调用方上传一页，页 SHA 可由上传者自行重算，无法证明其来自真实财务来源。因此本模块不接收页作为可发布输入，不自动调用模型，也不把物理块或纯重建结果标为 `persistentEvidenceVerified=true`。后续仅内部 signed-tool 单步 append 见 `AI_BUSINESS_FINANCE_V3_SIGNED_COLLECTION.md`；在日来源具备同等持久采集和全部来源独立核验前，整份 v3 任务仍保持 `collecting/manual`，五 Agent 与 HTML/XLSX 入口继续关闭。

隔离 PostgreSQL 已验收：新财务物理账本、既有 v3 门禁和旧来源目录共 24 项通过，见 `.runtime/ai-pg-367b8977b718/tests.log`。0029→0030 的独立备份/恢复演练通过：65 张 AI 表摘要、旧 renderer 1–7 文件字节和 AI/finance 权限保持不变；空账本可逆迁移再升级，带财务事实时逆迁移拒绝，父任务仍为 `collecting`，见 `.runtime/ai-pg-0d3fe3822511/business-finance-v3-pages-upgrade-evidence.json`。这些是隔离合成证明，不是业务来源真伪验收。真实签名跨进程调用、跨页续读超过一小时、完整 v3 混合来源封存及业务数据对账待后续专门接线与验收。
