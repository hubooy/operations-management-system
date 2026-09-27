# v4 财报分段纯重放候选

`business_analysis.v4_sealer_finance_segment_replay.replay_finance_segment` 在内存中一次重放最多 16 页。输入须为同一来源目录身份、0042 的 claim 绑定段与逐页被动返回，以及首次为空、续段为上一候选结果的进度。每页从规范 JSON 原文核对 UTF-8 字节数和 SHA-256、响应摘要、工具名称与完整请求参数摘要；调用 `finance_collection_state_v4.consume` 核对自然月、完成批次、缺月、原始行及行 ID、分页偏移和月度指标状态。每段重新计算行/页/字节、收据链与财报检查点，并与 0036 段进度原文一致。最后一段还要求目录总数一致、财报状态已结束。

候选 schema v2 加入 `previousCandidateDigest`（首段为零摘要），为后续受保护回执固定独立重放结果链；v1 候选不重解释为 v2。

调用者必须提供真正受保护的 `verify_claim` 与 `verify_segment_mac` 回调；续段还必须提供 `verify_previous_result`，从独立受保护回执确认上一候选结果确为此前已接受的结果。缺少任一回调或回调不返回严格 `True` 均拒绝。测试中的恒真回调只验证纯算法路径，没有权限含义。0042 段返回不自带 `segment_index`；调用方应将精确请求的段号加入传入的段字典，并在 claim 回调中复核该请求。

返回的 `candidateOnly=true`、`authorityVerified=false`、`financeReplayed=true` 只表示给定字节可按财报状态机重放；`sealCommitted=false`。这不是来源授权、上游签名或封存成功。此模块不能独立证明 0042 SQL 所持 claim、0036 HMAC 秘钥、上一结果回执、审计记录的真实签名/成功状态与时间顺序、父目录的 owning 权威或末页与持久来源完成检查点的一致性。以上须由受保护集成层核验，缺失则不得接入封存路径。纯模块没有写数据库、接线、迁移、生产部署或付费调用。

纯测试覆盖 17 页跨段、缺受保护回调、错月/批次、原文更改、缺页、伪前段及真实零行缺月。零行代表没有事实行；已发布月的指标为 `missing_subject`，未发布月为 `missing_month`，不会生成虚构数值。
