# renderer 11 的 0069 发布与下载设计门槛

状态：**阻断设计，不分配 0069 迁移，不开放 `ready`、下载或公开路由**。0068 只是候选受保护验签：迁移后密钥表为空，真实独立验证器进程与角色尚未采用；其真实角色 PostgreSQL 和升级恢复也仍需整合任务串行验收。只有解决以下事务边界后，才能编写发布函数。

## 当前已确认的竞态与缺口

1. `0068` 的私有 HMAC 验证函数从活动密钥表读取 key 时没有行锁。验证后、发布提交前若 key 被撤销或轮换，仅调用现有验证函数无法证明**提交时**签名仍有效。0069 必须新增受保护的锁定验签路径，在**同一发布事务**锁住精确 key 行直到提交；publisher 仍不得读取密钥或得到任意正文签名能力。若活动 key 不存在、已撤销或多个 key 状态异常，必须拒绝。
2. `0067` 的追加式证明与 `0068` 的回执覆盖逐卷 SHA 和过程摘要；`ai_business_volume_manifest_check` 在 SQL 端主要核卷目录、分块数量与总字节，不能单凭它证明 HTML gzip 行和 XLSX OPC/公式被重验。受保护验证器已在事务外完成全字节检查，但 0069 必须在锁定父任务后复验 0067 正文、回执 MAC、原有 chunk 不可变触发器/约束及全部当前卷目录；若不能证明从签发到提交不存在字节变动，应在同事务内逐卷重算文件 SHA。不可把自报 `fileByteVerificationDigest` 或 `fresh_semantics_verified=True` 当作替代。
3. 当前 `ai_business_volume_chunk_guard` 的插入读取父任务 `FOR UPDATE`，已暂存任务不允许追加；分块表有逐块 SHA CHECK 和 UPDATE/DELETE 不可变触发器。这提供可复用的**父锁 + 持久不可变字节**证明，但 0069 要核验这些触发器、约束、权限及 0066/0067 版本并实际做并发反例。触发器目录不匹配时失败关闭；不能默默回落到仅看文件清单的路径。
4. `0068` 对报告快照、流程输入与管理员版本做了当前检查，尚未在一个发布事务中同时锁住封存来源修订、五 Agent 完成账、人审行、固定预算行和报告绑定。需精确定义“来源新导入后旧封存报告还能否发布”：若要求当前来源不变，则锁并比对 finance/netshop 修订标记和对应 sealed 来源；若允许历史封存快照，则必须在回执中明确报告的历史 as-of 与不可变封存根，页面不得称其为当前实时数据。不能把 B 端/ERP 未知归属或缺失的京东店铺区间 UV 填成已验证数值。
5. 现有 v11 下载路径在 `business_volume_files.chunk` 明确拒绝；v10 的 reader 栅栏只处理 renderer 10。先有 0069 原子发布与只读 OUTCOME，再单独建立 v11 reader 的当前审批/预算/封存/发布请求/活动密钥栅栏；下载每个分块需前后各重读该窄回执及任务版本，并核本块 SHA、长度和所属文件描述符。发布成功不自动开放路由。

## 0069 最小事务协议

发布请求应是规范 JSON v1，至少绑定 `runId/attempt/expectedVersion/reportId/ownerEmail/bindingDigest`、0067 ID 与正文 SHA、0068 receipt 正文 SHA、key ID 与 MAC、当前报告/流程/人审/预算/封存根、compact/full manifest SHA、所有卷描述符摘要及固定发布用途。数据库自己重算 `publishRequestDigest`，不接受客户端自选摘要。签名正文与请求正文的字段不同步、未知字段、非规范 JSON 或任一大小上限超出，直接拒绝。

受保护 NOLOGIN publisher 调用单一 `PUBLISH`，开启一个短事务，按固定次序锁：父文件任务 `FOR UPDATE` → 0067 证明 `FOR SHARE` → 当前报告、流程、人审、五 Agent 完成账/封存与固定预算根 → 活动 key 行 `FOR SHARE` → 文件目录/分块证明。必要的来源修订标记按固定表序加锁，避免死锁。函数必须核精确 `paused/staged_unpublished`、原 `attempt/version`、同报告和 owner、完整批准证据及预算存在性，调用锁定验签，复验所有卷与不可变目录，然后以 `WHERE id/renderer_version/status/attempt/version/binding/manifest/stored_bytes` 做唯一 CAS。更新 `ready` 的 `progress_json` 只保存版本化发布请求摘要和有限回执 ID，不保存原密钥/MAC 或 HTML/XLSX 正文。更新后在**同一事务**复验新的 v11 ready 守卫；任何异常回滚整笔更新。必须只窄改两个 v11 文件守卫，renderer 1–10 分支和函数 OID/ACL/owner 冻结。

如果调用在数据库提交后丢失响应，调用方记录 `unknown` 并**只用原始请求**调用 `OUTCOME`，不得重发 `PUBLISH`。`OUTCOME` 以持久 `publishRequestDigest`、原 attempt/version 和回执根区分 `committed`、原状态仍精确未变的 `not_committed`、明确冲突和无法判定的 `unknown`；其中 `not_committed` 也不隐式授权重试。签名 key 后续被撤销时，`OUTCOME` 可报告历史提交事实，但 v11 新下载必须重新核当前访问政策并失败关闭，不能把旧提交事实等同当前交付权。

## 必须先通过的反例与演练

- 两个 publisher 同时争同一 run，恰一笔提交；原请求重放只读同一结果，异请求或旧 version 均拒绝。提交前注入后置校验错误，验证父任务/审计/发布账无部分写入；模拟驱动丢回复，证明只调用 `OUTCOME`，不再执行写入。
- 验签与撤销 key 并发，轮换事务被发布锁阻塞或发布看到 revoked 并拒绝；读角色、writer、attestor 和 publisher 不能读私钥、写验收证明或直接改 ready。
- 在签发后篡改卷块、插入未列块、删除块、改变 compact/full manifest、预算或人审、封存来源修订，均不得发布。对未锁定或无法定位的 source marker 返回 unknown/拒绝。真实规模下验证事务锁持有时限与内存上界；当前 v2 2000 页/64 MiB 仍不足 575095 推广行，不能宣称全量工程报告可发布。
- 先冻结旧 86 张 AI 表、已有 renderer 1–11 文件字节、0066–0068 函数 OID/正文/ACL/owner、角色/表列权限；再做 0068→0069 前后独立备份恢复、无已发布 v11 时空逆迁移重装、v9/v10 下载回归、v11 下载默认拒绝。若已有任意 v11 `ready` 行，逆迁移必须拒绝。

现阶段已有的阻断静态测试固定了 v11 ready 双拒、v11 chunk 下载拒绝，以及 0068 验签未持 key 行锁这一事实。它们是开发阻断断言，不是可上线证明；实现锁定发布后应替换为上述隔离并发行为测试。
