# 过期来源游标：候选纯合同

2026-09-18。本片仅新增 `business_analysis/cursor_renewal.py` 与纯测试，未接入 API、采集器、签名器、持久恢复或模型调度，也没有新增迁移。当前点击恢复仍会重试原过期游标；本文不表示此故障已在运行服务中解决。

## 现状与允许恢复的条件

`netshop.analysis.read_page`、`sales.analysis.read_page`、`market.analysis.read_page` 都签名 `{binding,lastId}`，读取时使用 `max_age=3600`。三个域使用不同 salt。`binding` 是该域 owning reader 计算的 `sourceRef`，同时包含查询、页长和来源 revision；网店 master 还绑定当前选中的已完成主数据批次。三种 binding 公式不完全相同，本片不复制或统一它们。市场来源选项目录的 cursor 是另一协议，不属于本片。

`business_collection.control(resume)` 只恢复调度，不会更新游标；过期请求通常以 `invalid_cursor` 暂停。已有采集器在网络取数后，以父任务版本、来源版本和完整检查点做 CAS，保存不可变事实页。不能因过期改成首页重读再接到旧明细，也不能把 revision 变化解释成只改时间戳。

候选规则要求原固定来源的精确平台、店铺/渠道或市场条件、原始日期、窗口、页长、revision、sourceRef 全部相同；末页的 sequence、lastId、nextCursor、来源身份与检查点一致。`lastId` 是递增源主键，可能存在空洞，绝不是已读行数。只支持尚未结束、至少一页且未耗尽 2000 页额度的 v2 采集状态。未授予旧 v1 新恢复语义。

## 纯接口

```python
prepare(snapshot, current_source, expired_payload) -> proposal
validate(proposal, snapshot, current_source, expired_payload) -> rebuilt_proposal
```

`snapshot` 必须由未来 owning 服务在续签准备时新读取，字段固定：

- `runId/runVersion/sourceVersion`：当前恢复时的父/来源版本，而不是最后一页写入时的旧父版本。
- `principalDigest/scopeDigest/planDigest`：未来 owning 服务从当前真实身份、范围和不可变计划生成的摘要。
- `evidenceSchema='business-evidence-v2'`、`status='collecting'`。
- `descriptor={source:{key,domain,query},revision,sourceRef,pageSize}`：从原目录及检查点取出；规范 query 必须显式包含 window。
- `checkpointJson`：原始持久规范 JSON 字节，不只部分计数；最多 32768 UTF-8 字节、4096 节点、12 层。还校验 PageReconciler 的固定字段、未完成状态、控制总行数、累计整数和 present 计数。
- `lastPage={sequence,payloadDigest,lastId,nextCursor,sourceRef,sourceRevision}`：未来服务须从实际持久末页重新计算并复核，不能接受客户端声明的页摘要。

`current_source` 必须由该域实际 reader 从同一查询重新计算，格式与 descriptor 相同。`expired_payload` 只是调用方提供的已解码 `{binding,lastId}`；**纯函数没有秘密签名密钥，不能证明载荷来自真实签名，更不能证明它只是过期**。

返回固定 `business-cursor-renewal-proposal-v1`，包括全部 snapshot 摘要、原始 checkpoint 字节 SHA、末页摘要、逻辑游标字节 SHA、原签名载荷及 proposal 摘要。始终声明 `prepared_unpublished`、`authorityVerified=false`、`renewalAuthorized=false`、`modelReplayAllowed=false`。重建验证不接受篡改后重新签摘要的自述授权；所有输入对象均不修改，返回值无可变别名。

来源目录本身采用既有规范来源合同。所有容量超限直接拒绝，不截断 query、状态或明细。完整检查点与末页真实性、数据库并发、真实权限以及签名真实性仍须 owning 服务提供；公开 JSON 不能恢复授权。

## 最小后续 owning 接线

1. 当前真实管理员及 scope 复验，固定当前父/来源版本；读取原目录、完整 checkpoint、真实末块并核字节 SHA、来源及行尾。保留原请求日期与 window。只处理三个只读事实页的明确 `SignatureExpired`，不能把所有 `BadSignature` 都放宽。
2. 在对应原 salt 下核验原 token 的原始签名并解码严格两个字段，再独立重算当前 owning binding 与 revision；如不一致则拒绝续签，要求新建完整采集。固定 master 批次选择也必须一致。不接受客户端提供的 prior、revision 或解码载荷作为依据。
3. 续签 token 仅作为同次 owning 只读取页调用的临时 transport alias。实际查询严格从旧 lastId 之后开始；页读取结束再核 revision。响应丢失可以重新读取同一只读页，但不得提交重复页。
4. 采集器仍以**原** `checkpoint.expected_cursor` 调用 `PageReconciler.consume(page, request_cursor=原游标)`。成功新页自然产生其下一页 token，并按原事务更新 checkpoint；不先保存临时 alias，也不改旧 chunk 的 nextCursor。这样旧不可变页链能够完整重放，不需要为了续签增加持久表。
5. 落页前重做真实权限、父/来源版本和 checkpoint 字节 CAS，并确认未取消、未封存、未被其他采集者推进。过期 proposal 无效。签名续期不授予扩大页长、扩日期、换来源、跳过页面或重置额度的资格。

以上不涉及 provider/model dispatch；模型结果未知、写操作未知和付款等继续沿各自账本禁止自动重放，不能复用本方案。

## 当前验证与后续门禁

纯测试 11 项通过，日志 `.runtime/cursor-renewal-pure.log`。覆盖三域稳定候选、查询/revision/窗口/页长变化、父来源 CAS、账号/范围/计划/检查点字节变化、主键空洞、末页不一致、结束/空/容量/损坏状态、bool/float 与 int 不混同、重复键/深嵌套/UTF-8 超限、自签授权拒绝、旧 v1 拒绝。实际 PageReconciler 两页实验验证：临时新 token 不能替代逻辑旧 token，旧原始页字节不变，最终重放的完整 totals/digest 一致。

尚未验证真实 Django 签名过期及盐隔离、实际数据库末块真实性与并发、三个 owning revision 变化、master 更换、权限中途撤销、恢复页网络响应丢失及后台自动/手动重试。后续应在隔离 PostgreSQL 和真实签名器测试这些项，再单独决定 API 与调度集成；本片没有运行 PostgreSQL、正式服务或模型。
