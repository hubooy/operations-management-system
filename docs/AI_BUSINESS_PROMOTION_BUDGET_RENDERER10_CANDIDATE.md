# 推广预算 renderer 10 纯候选

此切片在独立开发工作树新增 `business_analysis.promotion_budget_v10.project`，没有注册 renderer、文件任务、网页或下载入口，也没有数据库写入、模型调用和投放动作。renderer 9 与 renderer 1—7 的行为不变。`Candidate.proof.candidateOnly=true`，不能作为正式 ready 或来源授权凭据。

调用方须在同一次拥有方流程中重新取得：已核 v9 `promotionTrialProof` v2、已人审批准的 DTO 绑定及其实际摘要、同报告 `reportBinding`、以及可选的固定预算材料。固定预算材料必须来自 `business_promotion_budget._roots` 的当前账号和封存 v2 证据重算结果，携带 `binding/reference/result`；纯模块只检查这些材料之间的一致性，不能替代 `_roots` 的数据库授权、当前修订和逐事实证明。若报告绑定声明有固定预算，但材料丢失，必须失败，不能降级成“无预算”。

有固定预算时，纯模块复核报告、账号、证据 ID/版本、封存摘要、预算计划摘要、预算引用及完整预算结果的逐项重算，生成以下三张 source-bound 数据表：

- `promotion-budget-allocation-v1`：来源键、维度、行 ID、基期指标、输入权重/上下限、分配初值、观察与复盘阈值；金额以分表示，缺值保持 `null`。
- `promotion-budget-scenarios-v1`：每个已选对象 × 每个输入情景的乘数、假设结果、状态和人工回退条件；归因金额不当作 ERP 净销售、利润或增量效果。
- `promotion-budget-scenario-summary-v1`：每个情景一行，保留归因口径分组；混合口径或缺值时完整合计为 `null`，不补零。

候选返回已有 `budget_offline.payload` 所用的离线计算输入，并用 `budget_excel.build(..., formula_version=2)` 预检 3 张可编辑试算页的初值；实际工作表名称、公式与字节仍由完整文件 writer 再验。`volume_plan` 应以 rendererVersion 10、`native_budget_sheets=3` 规划完整报告，且首卷至少保留一张数据表片段；现有 writer 会拒绝纯预算首卷。所有源表仍须逐行写入后核对摘要和实际字节容量。编辑只影响本地未复核试算，不回写原报告或执行广告调整。

若已批准报告没有固定预算引用，则只生成一行 `promotion-budget-gap-v1`，`nativeBudgetSheets=0`、`offlineBudgetEnabled=false`、`editableAllocation=false`；不补造金额、投放额度或公式。批准正文中的预算措辞本身不能成为固定预算证据。

`Candidate.proof` 绑定 v9 证明摘要、批准内容、人审、封存摘要、预算绑定/引用/结果摘要、表声明及完整行摘要，并明确预算状态。它只是 renderer 10 完整清单的待集成片段。正式接入仍须在拥有方前后复验和最终发布事务中，把新增表与原 v9 的全部来源表、批准内容、v10 文件字节/卷清单及预算三页的实际内容绑定；不能直接把本片段写为文件 ready。原生 Excel 重算、真实业务容量、浏览器与人审验收仍待进行。

定向纯测试覆盖缺预算、跨报告/账号/证据/审批篡改、预算结果与引用漂移、缺指标与混合口径，以及既有 volume planner 和 Excel 公式构建。没有运行 PostgreSQL 或正式服务。
