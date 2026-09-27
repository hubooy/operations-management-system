# 市场 v2 第五工具未注册读取候选

此切片在 0053 已证明材料、但仍暂停的市场 v2 新报告上提供内部 `business_market_v2_fifth_read_preview.read`。它不注册 transport 工具，不创建节点、Agent job、provider/tool 派发或结果，不调用模型，也不发布文件。0044 停放报告、0045 不可变市场材料和旧词货 v1 四工具保持原样。

调用者必须提供新报告 ID、同账号无范围管理员、精确市场 selector、0045 manifest 摘要及市场上下文摘要，以及一组注入的 job ID、provider dispatch ID、provider call ID 和允许角色。注入身份只用于把本次预览结果与未来待创建的同一调用**声明**绑定；适配层确认这些 ID 当前不属于已持久化任务或派发，不把它们当作授权。新报告必须保持 `paused`、`allowedTools=[]`、模型为空且无节点/job；读前后重新核验报告、停放根、材料侧表与封存来源。

summary/page/row 继续调用现有拥有方市场预览，每次重放并核对三张完整类型表，再返回对应有界结果。页/行带服务端生成的市场 TOP 样本引用基础身份；精确行可额外指定一个指标与字段，由 `market_numeric_claims.number` 从拥有方行复算，缺值或不具可比观察日则拒绝。返回始终标记 `sameJobProviderPersisted=false`、`persistedRead=false`、`registeredTool=false`、`authorityVerified=false`，引用数值不能加到本店、ERP、B 端或利润。

0053 数据库当前硬拒真实 job 和第五工具派发，因此**持久同 Agent/provider 读取回执不可能在此切片产生**。下一激活版本须先固定真实第五工具 transport 目录、模型策略与角色权限，再有版本化迁移开放新 profile 的节点/job/provider/dispatch/result，依同一 job 的模型调用 ID、参数、结果和拥有方逐页/逐行重算生成回执；通过前不能声称五 Agent 市场分析完成。

2026-09-25 纯合同测试 `ai_assistant.test_business_market_v2_fifth_read_contract` **3/3** 通过。隔离 PostgreSQL 目标 `ai_assistant.test_business_market_v2_fifth_read_preview` **3/3** 通过，证据 `.runtime/ai-pg-49dd0c30c191/tests.log`；覆盖同账号材料摘要、三表 summary、页/精确行、数值候选、跨账号/角色/selector/报告和迟到撤权拒绝，并核查没有新增 job/provider/tool 派发或结果。该合成验收仍不是实际 Agent 阅读；真实模型、市场业务复算、HTML/XLSX、生产均未验收。
