# v4 独立封存器：推广单段重放纯候选

本切片只提供 `backend/business_analysis/v4_sealer_segment_replay.py` 的纯函数。它读取已由 0042 票据化 SQL 返回的一个推广分段及最多 16 页，并输出可作为下一段输入的有限进度。`candidateOnly=true`、`authorityVerified=false`、`sealCommitted=false` 固定，未写入数据库、未改变父任务状态、未开放模型、Agent 或报告。

调用者先从固定的 run、最新 attempt、精确来源与已领取票据构造 `identity`。其中 `sourceRoot`、页行字节、来源版本、来源查询和账号版本必须由受保护路径复核；`ticket_segment` 的 `segment_index` 由本次查询参数补进记录。纯函数**要求**传入 `verify_claim` 和 `verify_segment_mac` 回调。正式调用方应在受保护 sealer 身份下验证 0041/0042 claim 当前租期、账号、来源根与源写门禁，并用 0036 的真实用途分隔密钥检查段 MAC。纯测试使用返回 `True` 的替身，仅验证重放逻辑，绝不构成真实签名或授权。

每页重算规范 UTF-8 原文字节数和 SHA-256、原文行数、固定推广行形状与日期、工具请求参数摘要、`PageReconciler` 游标与控制总额、收据链；对照 0036 段的进度原文、进度摘要与段证明摘要。0036 的 `digest(encoded)` 来自 `ai_assistant.policy.digest`，字符串按原 UTF-8 字节算 SHA-256；不能改用 `business_analysis.contracts.digest(str)` 的 JSON 字符串摘要。跨段恢复先对上一段候选做 48 KiB、深度和节点数上界检查，再要求任务、来源根、版本、密钥、段序号与摘要吻合，并**必须**传入 `verify_previous_result`，由受保护的不可变回执或真实签名核对上一段。候选 schema v2 新增 `previousCandidateDigest`，首段固定为零摘要，后续绑定前段候选摘要，供独立持久回执形成连续链；v1 候选不能重解释为 v2。候选摘要只是完整性自检，持有候选文本的人能重算；单凭它不能恢复或用于最终封存。

现阶段未独立验证 0036 段状态里的来源元数据和上游签名，也未重放财务页，因此返回 `sourceMetadataVerified=false`、`financeReplayed=false`。SQL 返回的 `run_bound_capability_verified=true` 只说明被动窄读通过，不是业务证明。`business_v4_seal_verify.verify_seal` 仍受 600 秒完整原文扫描限制；真实大来源需要把相同的逐段证明接入后续版本化的封存后验证，不可凭本候选绕过。

0038 的父封存门禁要求恰好一个真实 finance 月度背景来源和一至三个京东推广窗口。财务拥有方对精确 scope/月区间合法返回零行时，v4 财务合同允许完整空页及 `missingMonths`，但空页仍须由真实拥有方查询、真实 publication/batch 关系、工具审计和分段验证产生。拿不到该来源时，应保持封存关闭，另设计版本化的 promotion-only profile；不能构造合成空月页凑够来源数。

后续最小接线顺序：先做受保护 claim/MAC 校验和逐段候选持久回执及幂等恢复，再做财务单段重放，最后才设计核完整段根、当前账号/尝试/来源修订及真实应用 MAC 的短事务提交包装；包装必须让 0038 seal 与 0043 消费回执同事务提交。当前 NOLOGIN 角色、旧无票据读取及直接 commit 撤权保持不变。
