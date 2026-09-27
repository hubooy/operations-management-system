# 月度财报纯证据合同候选

2026-09-24。该片仅有纯函数、测试和文档。未建立财务公共路由、模型工具、AI 持久证据、报告接线或正式数据读写；无需数据库迁移。实际 owning reader 仍为 `finance.business_analysis_source.open_source`，须由以后受控调用路径正常离开其 context 并通过最终账号、revision 和月度发布状态复验，再考虑发布派生结果。

## 版本及输入

`business_analysis.finance_evidence.prepare(source, binding)` 接受既有 `FinanceSource` 全量内存快照和同一次 `open_source` 的 binding，产出 `business-finance-evidence-v1` 清单与 `business-finance-evidence-page-v1` 完整行页。函数完整复读旧页，校验摘要、顺序、行 ID、行引用、当前自然月及发布批次，再用 `finance_source.build` 从全部原值重建旧清单与旧页；任一变化整源拒绝。binding 必须对应相同的完整 revision、来源摘要、month→batch 摘要及 active 无范围限制管理员形状。纯层无法验证 binding 来自实际数据库，因此其摘要不是可转让的授权凭证。

来源精确查询仍为 1—24 个连续自然月及唯一 `scope_key/scope_type/scope_name/group_name`。清单保留完整来源 `query`、完整 revision、每个已发布月绑定的完成批次及三个发布摘要，并逐月照抄 `FinanceSource.coverage` 的 12 项指标状态。缺少月份为 `missing_month`；已发布月份但所选精确范围没有行或科目为 `missing_subject`；`missing_value`、`non_numeric`、`ambiguous_subject` 与真实金额零分保持不同。`allRequestedMonthsPublished` 及 `allCoreMetricsPresent` 不代表业务口径已审计。

新页从原来源行中按原顺序完整重装，每页最多 100 行且完整 JSON 不超过 38,000 UTF-8 字节；实际 `nextOffset` 是唯一后续边界。每页包含先前页摘要，清单记录末页摘要与总页数。旧来源最多 128 KiB/页，不能直接送进 38 KiB 的模型工具。新合同最多 2,000 页，清单与全部页合计最多 64 MiB；任一行、清单、页或总量超限，整体失败，不裁剪事实或缺口。空精确范围生成一页零行，保留月度缺口。清单和页读取都返回独立副本。

清单的 `sourceAuthorityVerified`、`persistentEvidenceVerified`、`businessCoverageVerified`、`agentReadVerified` 均固定为 `false`。本纯合同尚未持久化；页面摘要只证明本对象内部内容一致。以后需从真实 owning 上下文建立权限与最后复验，再在新证据协议中持续记录页、检查点与封存根，并由五个实际 Agent 分别留下完整读取回执。

## 业务解释边界

- `section=summary`、`section=kingdee` 及 `is_total`、`source_row_count` 均原样保留。合计与明细、经营汇总与金蝶科目不可直接相加；源端已合并记录不能拆回原单元格。
- 金额为分，比率为基点。`rate_bps` 是源账本值，合并过的比率也仅按原值披露，不按月累加；缺失利润不推算，财报不能反推某 SKU 或客户利润。
- `analysisPeriod` 可同时记录近 30 天等日窗口。仅当该窗口等于所选完整自然月首日至末日时为 `exact_full_months`；其他情况为 `different_or_partial_months`，不得将月金额按天摊算或冒充近 30 天财务同期。
- 源解析的店铺 `scope_key` 可能不包含组，跨组同名店可能在导入时已合并；后续财务与网店/ERP 匹配必须另外核实身份，不可从此证据反演。财务账面日期、税费和退款规则也不能直接与日销售或推广金额相加、推因果。

## 后续接入门槛

此片不改 `business-evidence-v1/v2`。后续应新增独立版本的持久财务证据入口：精确财务 scope 与自然月计划、finance reader 签名只读路径、真实管理员及最小角色授权、每页前后发布版本栅栏、AI 检查点 CAS、完整封存、五角色包、数值引用与 HTML/XLSX 同源表。现有 `AiBusinessEvidenceSource` 的 `domain` 和 JSON 字段可能容纳新域，但需要针对实际持久协议做迁移判断，不能把纯对象或 binding 摘要直接写成“已封存”。工作台可先有独立月度覆盖预览；预览也不得称为五 Agent 已读。

隔离纯测试：`PYTHONPATH=backend python -m unittest business_analysis.test_finance_evidence`。覆盖 24 月完整缺口、发布批次与 revision 绑定、空值/零值、原比率、合计/明细、部分月对齐、完整页链、副本隔离和篡改/超额拒绝。真实财务表是否覆盖目标店铺、原文件比率是否可比以及跨组同名情况均未读取正式数据，仍需真实业务对账。
