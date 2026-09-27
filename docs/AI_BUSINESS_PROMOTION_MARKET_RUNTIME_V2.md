# 市场 v2 五 Agent 运行合同候选

`business_promotion_market_runtime_v2_contract.prepare` 只接受当前账号报告准入 owning binder 生成、两个指定观察日都有封存覆盖且仍标记“未授权”的候选 DTO，固定一个新版本 profile、原五角色与第五码市场工具 `get_business_promotion_market_v2`。它从已冻结的词货 v1 图复制出 v2 图，仅为 `market_b2b`、`independent_review`、`report` 增市场职责；旧 v1 工具顺序、图字节和 renderer7 保持原样。本模块不注册工具、不创建工作流、不发模型调用或文件。

市场工具仅接受固定报告 ID 与市场上下文摘要，三种模式为 `summary`、`page`、`row`。来源键、价格带与两观察日期全部来自报告准入，模型不能在工具参数里重新选择。服务器未来须先完整重放市场价格带汇总/成员、双日进出榜三张表，再给角色一个有界的来源覆盖与表摘要；`page` 固定每页 20 行，`row` 须给行位置和完整摘要。`market_b2b` 本人必须先读摘要，并对每个非空价格带或进出榜视图至少读首页；独立复核和报告角色也须本人读摘要，凡引用数字还须自己持久读取相应页或精确行。`commerce` 与 `promotion` 不能调用市场工具。首页阅读不能声称 Agent 阅读了全量市场明细，完整页链只由服务器 owning 负责。

数值引用只接受同一角色、job、报告、价格带或双观察日来源的固定字段，人口口径必须为 `market_top_sample_only`；未来解析器须核对**本人**真实 provider 调用、工具 dispatch、成功结果、页/行回执，并调用 owning `read_row` 重新计算。当前纯函数仅给这些必须满足的条件，返回 `agentReadPersisted=false`、`authorityVerified=false`。缺日期与未进入 TOP 样本、零销量必须区分；价格带汇总与成员不可相加，市场样本金额不能并入本店、ERP 或 B 端销售。下一步仍需独立的 v2 报告 snapshot/workflow/allowedTools 持久守卫，以及第五码真实派发、完成态读取证明与人审/renderer8 接线。
