# 跨来源逐日并列的封存报告入口

`ai_assistant.business_cross_source_daily_materials.prepare` 是内部只读候选，不新增公开 API、Agent 工具或正式文件版本。它从一份当前账号可读的已封存 v2 集成报告重新读取来源目录和每个显式所选源的 Reader.info，用单店 `cross_source_kpi_plan` 重建来源、三期缺失状态与 ERP mapping pair；该计划必须与报告固定工作流中的关联计划一致。调用方还须指定比较窗口和对应 pair，不能把本期 ERP 关联用于环比或同比。

所选 ERP 销售与本期主数据通过既有内部 owning 入口重新读取并形成五层级回卷材料；本次逐日对齐再完整核对店铺/日、SKU/日、未分配/日三份 NDJSON。商智 SKU、商智原生 SPU 与推广页分别经同一封存 Reader 完整重放，逐页核对来源身份、查询窗口、版本、水位、源行内容摘要唯一性、控制总额及业务日覆盖。准备完成和返回前再次复核当前账号、报告与封存版本；晚到撤权、错误映射、容量超限和任何来源不守恒都拒绝整份材料。

结果仅含店铺/日与 SKU/日两种并列材料，保留 `{value,presentRows,missingRows}` 及部分缺值状态。ERP 退款、净销售和成本、商智原生 SKU/SPU 支付及商品日访客、推广花费及归因额依旧分域；ERP 未分配池与推广缺明确 SKU 桶保持独立。`authorityVerified=false`、`registeredAgentTool=false`、`registeredRenderer=false`；这不是订单级推广归因、店铺去重 UV、SKU 净利润或正式五维诊断。品类/日与 SPU 原生/汇卷双轨解释、真实三期业务数据和正式文件交付仍须后续版本单独验收。
