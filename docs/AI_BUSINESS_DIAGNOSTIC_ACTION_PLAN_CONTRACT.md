# 深度诊断调整计划纯合同（候选，默认关闭）

`business_analysis.diagnostic_action_plan_v1.prepare_candidate` 为现有 `report_composition_v1` 的后置、纯校验层。它不注册 Agent、报告版本、文件 writer、路由或执行器，必须由调用方显式 `enabled=True` 且提供**拥有方当前封存报告重读回调**才能返回候选。组合摘要和调用方重算的 SHA-256 本身不是授权。输出固定 `reportRoot`（reportId、planDigest、owningBindingDigest、compositionDigest），每条动作只引用该组合报告的表级来源摘要；表级引用不等于商品行级数字引用。

每条候选动作必须有：店铺/品类/SPU/SKU/关键词维度、来源表及状态、枚举化方向、责任角色、阶段、预算原则与上限来源、KPI 定义、观察天数和启动门槛、停止条件、人工回退方式。由于现有组合目录没有可独立复验的实体行和数值，目标身份固定为 `dimension_only_no_row_citation`，KPI 基线与数值目标均为 `null`。缺关键词等来源只允许补源，不可生成匹配、投放效率或销量判断；ERP 历史品类/SPU/SKU 身份未证实时，不能声称历史增长。

预算只允许零新增花费（上限 0，来源为本合同的“不新增花费”原则）或“待批准预算计划”（上限未知）；不接受 Agent 估计的正数上限。观察期 1–30 天只在来源复核和人工批准之后开始。停止条件覆盖来源修订、覆盖缺口、KPI 恶化、预算上限和人工叫停；回退为放弃未批准方案或人工恢复上一批准配置。候选、每条动作均明示不可自动投放、调价或执行；没有发布权限。

市场样本、财报自然月、B 端日事实仅可作为外部背景。若引用市场样本，还要求独立核验的五 Agent **候选**结果和回调，但该候选本身仍声明未独立重读持久来源、未核验散文数字、未批准或发布。它不能证明市场 TOP 样本等于本店销售，也不能把 B 端当作 ERP/平台销售的增量、把自然月财报摊到每日，或跨域金额相加。外部材料与主报告同根状态仍为 false。

接线前还需要：将本合同与真实五 Agent 完成态及持久回执逐条关联；由拥有方签出同报告行级事实指针、完整关键词分页和市场样本来源，核验结构化数值及散文数字；获得真实预算方案和人工批准；把获批行动与 HTML/XLSX 同一表流及文件发布栅栏接通；按真实店铺三期数据、B 端包含关系和原生 Excel 验收。当前纯测试不能替代这些步骤，也不改变现有 `action_plan` 旧表及 renderer 字节。

验证：`cd backend && python -m unittest business_analysis.test_diagnostic_action_plan_v1 business_analysis.test_report_composition_v1 -v`。负例覆盖缺源却建议优化、报告/表摘要漂移、具体 SKU 身份冒认、历史增长冒认、市场/B 端作为自有销售或增量、虚构预算/KPI、缺停止回退、五角色不齐和无拥有方回调。

## 额外 HTML/XLSX 表候选

`report_composition_tables_v1.prepare(..., diagnostic_action_candidate=...)` 才启用第 14 张 `diagnostic_action_plan` 表，并返回版本化 `business-report-composition-table-delivery-candidate-v2` 目录；默认调用继续返回原有 13 表及 v1 元数据。它先从同一来源和拥有方复核构造报告组合，再用 `check_candidate` 全量重算动作合同并比较候选，拒绝旧报告根、改写引用/预算/KPI、伪造执行许可及无市场回调的五 Agent 候选。新表在末尾审计表之前，审计清单纳入其行数、列数、规范行摘要、候选摘要和来源状态，原 13 表顺序与内容不变。

新表逐行保留同报告根、主证据表/摘要/状态、外部背景引用、维度与建议方向、责任角色、预算原则/已知零或待批准上限、KPI 定义与缺值、观察期、停止和回退条件。它和其他表共用 `report_files.write_pair` 的同一 `Table` 流，合成测试比对 HTML 行、XLSX 内置清单、工作表实际行数和审计 SHA-256；未知预算与 KPI 值在两份文件中保持空值，不变成零。市场五角色材料只能经额外拥有方回调成为外部样本引用，不能写成本店销售事实。

此项仍是小规模未注册预览：没有正式 renderer/下载、真实 Agent 已读、具体行级事实或数值引用、获批投放预算、生产采用和原生 Excel 验收。现有 v2 来源行数上限与大规模 v4 同报告桥仍需独立打通。相关测试：`cd backend && python -m unittest business_analysis.test_diagnostic_action_plan_table_v1 business_analysis.test_diagnostic_action_plan_v1 business_analysis.test_report_composition_tables_v1 -q`。
