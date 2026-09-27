# v3 报告准入候选（内部只读）

`business_report_candidate_v3.prepare` 只接受精确的 v3 执行画像、证据 ID、封存版本和摘要。它重新调用内部 `business_v3_seal.verify`，固定每个来源的目录身份、完整修订、收据链、日观察截止与缺行日、财报发布月与缺月，再生成不可执行的 `business-report-admission-candidate-v3`。没有 `AiReportRun`、工作流、模型请求或文件任务，也不注册公共工作台入口。

候选明确将销售/网店/市场日来源与财报 `monthly_context` 分开。财报只作自然月经营背景：不按日摊销，不计算 SKU 利润，也不把可能重叠的 ERP 净销售、B 端成交与广告归因成交相加。日来源的缺行日不是零销售；月财报的缺月、缺主体、空值不是零。各来源分别读取，跨域快照不是原子时点。

后续要启用五 Agent，必须另建持久任务根及每 Agent 本人目录/分析表阅读回执，逐项锁定 commerce、promotion、market_b2b、independent_review、report 的来源权限、引用核对和人工审核；不得借用 v2 的工具回执。HTML/XLSX 需另行接入 v3 固定引用与已验证报告，保持旧 renderer 1–7 字节和旧 v1/v2 报告不变。在这些门禁完成前，`modelDispatchSupported=false`、`reportGenerationSupported=false`、`fileGenerationSupported=false`。
