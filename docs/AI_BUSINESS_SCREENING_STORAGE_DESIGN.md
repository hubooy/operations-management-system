# 全量筛查结果持久复用的最小设计

状态：**历史设计，第三十五批已实现内部存储候选；未注册工具或新报告 profile**。实际字段、摘要链、权限与容量以 [实现说明](AI_BUSINESS_SCREENING_STORAGE.md) 及候选代码为准。本文保留设计推导，不能作为模型或生产验收结果。

建议下一片新增两张专用表，保存一次完整成功筛查及其固定页。计算在事务外完成，短事务一次发布；工具读取既有页，不在每页、每个 Agent 中重新扫描事实。读取权限和每个 Agent 的阅读回执仍各自验证。

## 1. 已核对的现有边界

| 现有存储 | 当前代码与约束 | 对筛查的结论 |
| --- | --- | --- |
| `AiArtifacts` / `artifacts.py` | `kind=table`，64 KiB，最多 50 行、12 列，字符串可裁至 240 字符；`candidate()` 可截断并标 truncated。绑定 conversation/message/owner，没有 report/scope/seal 外键合同 | 不得用其承载完整覆盖或候选证明；扩展会改变旧产物语义 |
| evidence run/source/chunk | 单任务事实 64 MiB、2000 页；v2 目录/header/checkpoint 另计入共享存储。`check_quota` 以原事实加 v2 元数据计算 owner 256 MiB、global 2 GiB；最多四个 collecting/owner、全局一万任务。来源块与父版本、封存状态机绑定 | 筛查是派生结果，不是新增来源事实，不能挂到旧 source key 或封存 state |
| 固定预算表 | plan 48000 B、binding 4096 B；owner 8 MiB/200 条、global 64 MiB/2000 条。0021 使用同一个 AI revision 行锁、数据库实际 `octet_length` 聚合、不可变记录及报告精确引用 | 可借鉴发布、quota、绑定机制，但不能把筛查 JSON 填进预算 plan |
| file run / volume chunk | 文件块 512 KiB；单 run 全 attempt 累计 1 GiB，owner 2 GiB、global 8 GiB；renderer 1/2/3 与 4 各走专表，卷 0 仅 JSON、业务卷须 HTML/XLSX。0020 核块坐标/连续性/实际字节，终态与父 CAS 有专门 guard | 不能为了复用块表伪造 renderer、未复核交付或缺失 HTML/XLSX；会增加更大兼容面 |
| 第 33 批内部对象 | `VerifiedScreening` 仅在进程内，在完整表 context 和最终绑定复验成功后构造。覆盖/候选页每次轻重载真实报告/封存目录，不重新扫事实；无 HTTP JSON 恢复 | 可作为新发布函数的唯一计算输入，不能允许客户端提交其 JSON 恢复权威 |

现有预算/证据/文件额度是各存储域的额度，并非全系统磁盘、RSS 或数据库物理占用的统一上限。文件/预算不可变表的已有策略也不提供普通运行时 TTL 删除。

## 2. 推荐两表，而不是一个大 JSON 每页反复反序列化

以下为建议字段，最终迁移名由集成任务分配（当前 AI 最新为 0022）。不修改旧报告行、旧快照或旧文件 renderer。

### `AiBusinessScreeningRun` / `ai_business_screening_runs`

- id，report FK `PROTECT`，evidence FK `PROTECT`，owner_email，scope_json。
- binding_json / binding_digest：规范、严格字段绑定，建议最多 8192 UTF-8 字节。
- manifest_json / manifest_digest：紧凑成功清单，建议最多 65536 UTF-8 字节。只列各页组摘要、起止 sequence、页数/条数、完整扫描与日期语义，不内联全部候选。
- selection_plan_digest、pure_result_digest、service_result_digest、content_root_digest：分别表示固定选择、纯计算、33 服务封套、实际持久页，不混为一个摘要。
- algorithm_version、selection_policy、storage_schema / capacity_profile。
- page_count、stored_bytes、created_at。
- 唯一 `(report_id, binding_digest, selection_plan_digest, storage_schema)`。算法/容量版本同时严格进入 binding，唯一键不能忽略它们。
- **没有 building/failed/部分成功状态**。只允许成功对象一次 INSERT；没有完整页集的父行不能提交。

### `AiBusinessScreeningPage` / `ai_business_screening_pages`

- id，run FK `PROTECT`，sequence（全局从 1 连续）。
- kind=`coverage` 或 `candidates`，partition_key（coverage 固定空值，candidates 必须已列清单），offset、returned、total、next_offset。
- payload_json / payload_digest，created_at。payload 是固定页原对象，UTF-8 ≤38000 B；每页至多 20 条，允许因字节预算少于 20 条。零候选分区仍有一份 total=0、returned=0、nextOffset=null 的终页。
- 唯一 `(run, sequence)` 和 `(run, kind, partition_key, offset)`；每条不可修改/删除。

无需再存一份重复的整个 `VerifiedScreening._result_json`。完整选择计划从固定目录/checkpoint/analysisRequest/mapping 与版本化选择器重建，比较原 selectionPlanDigest；这是元数据计算，不是事实扫描。覆盖页保存完整 families/requested/table/partition 清单；候选页保存所有 retained 候选，并保留 matched/omitted 数量。不能把 retained 候选称为全部匹配事实。

清单对每页组保存条数、页数及按坐标排列的摘要链；全局 contentRoot 用长度前缀的规范元组 `(sequence,kind,partitionKey,offset,returned,total,nextOffset,payloadDigest)` 顺序折叠。不要拼接无分隔字符串，不接受自报集合摘要。持久化合同应提供确定性的 make/validate，并在有界完整页集上验证从 offset 0 连续到 null、total 恒定、条数相等及 coverage 中分区集合与候选页组集合精确相等。

`service_result_digest` 已在页生成前确定；contentRoot 另算，不把自身摘要递归放进被摘要对象。数据库验证实际页 SHA/计数/连续链和字段绑定；它不能独自证明经营数学，数学证明来自 owning service 完整计算及其封存依赖。

## 3. 发布、竞争与未知结果

建议内部接口：

```python
prepare_for_report(report_id, principal) -> VerifiedScreening  # 现有 33 接口
publish(verified, principal) -> PersistedScreeningReference
read_coverage(reference_id, principal, *, offset=0) -> 固定持久页
read_candidates(reference_id, principal, partition_key, *, offset=0) -> 固定持久页
```

`publish` 不接受 plan/candidate JSON、客户端 digest、任意报告路径或数据库 SQL。准备完整 canonical 页时先测单页/总量，超限整次拒绝。取当前 principal/report/证据绑定再进入 `mutation(principal)`；沿现有顺序先取得 `ai_data_revisions('ai-assistant')` 行锁，再检真实报告、预算轻引用、封存版本和唯一键。

事务内重建完整元数据 binding 与 selectionPlanDigest，核与 verified 相同；逐页摘要、候选/覆盖守恒及总字节重验后一次 INSERT 父和所有页，审计失败或任一 guard 失败则全回滚。不得在长事实扫描期间持有全域 mutation 锁。

已存在相同唯一键时，重验其完整清单和固定绑定：相同结果摘要返回同 ID；不同摘要冲突，不能覆盖或选较新者。两个首次调用可能各算一次，这是最小切片明确保留的成本；短事务唯一键和全域锁保证最多发布一份。首片不加 distributed lease、线程 singleton 或全局缓存。

发布响应丢失时，按固定唯一键查询确认已提交对象，不能立刻追加新任务。事务外纯扫描中断可以用户明确重试；尚无模型/外部写入，因此不需把它变成 provider unknown 重放。未来 Agent 的未知派发仍沿原 ledger 失败关闭，与派生计算可重试分开。

## 4. 新容量建议与数据库门禁

以下是**待主 Agent 确认的新固定档位**，不是已启用额度：

| 边界 | 建议 |
| --- | ---: |
| binding_json | 8192 B |
| manifest_json | 65536 B |
| 一个实际页 | 38000 B / 至多 20 项 |
| 一次结果所有页 | 至多 4096 页 |
| run 全部规范载荷 | 16 MiB |
| owner | 64 MiB / 20 个成功 run |
| global | 256 MiB / 200 个成功 run |

`stored_bytes` 明确计 `octet_length(binding_json)+octet_length(manifest_json)+SUM(octet_length(payload_json))`，包括空分区页及所有封套，不只 candidateBytes；它不是索引/行头/TOAST/WAL 或进程内存实占。以上独立新额度不会提高原 evidence/file/budget 上限；应向健康/管理页披露额外增长上界，不声称已有全局 8 GiB 自动包含它。

INSERT guard 复用 0021 的 READ COMMITTED + AI revision 行锁；计数和字节必须在等待锁后通过新快照读实际数据。父行声明总额先纳入 quota，deferred complete guard 必须核所有真实页数量、sequence、每页 UTF-8/真实 SHA、实际字节、组内 offset/terminal 和清单 digest 相等。未完成父行不能 commit，子页只能在父同一创建事务插入，不能在已提交 run 上追加；可使用可信当前事务 ID/严格 parent insert transaction 门禁并做双事务负测，不能只检查“父存在”。

JSON 字段类型须用 `IS NOT DISTINCT FROM` 等失败关闭判断；数字严格拒 bool/小数词法、重复键、额外键、NULL 绕过。新表纳入 writer epoch fence / append-only；reader 仅 SELECT，业务域 writer 无权限。父子 owner/scope/report/evidence/版本/目录/封存/分析请求必须真实核对，不只相信自报 SHA。

## 5. 读取和跨 Agent 使用

每个页读取前后都重载当前 principal 和真实 report/workflow、固定预算轻引用、evidence seal/catalog/检查点元数据，核 binding 精确一致。禁止跨 owner、scope、report 复用，即使 sealedDigest 或数学结果相同；权限不缓存。

读取只加载固定父清单和所需一页，复验页 SHA、kind/partition/offset、内容的 result/plan/binding 摘要与清单坐标一致。先前已核验且不可变的数据库行可作为持久计算结果，不必再次读取全部事实。未知 schema/算法/缺页、失权、快照变更一律拒绝，不自动现算新结果代替旧 ID。

数学结果可共享，Agent“已读证明”不可共享。五个 job 仍分别记录自己的目录/覆盖/要求的候选页回执；完整读取 retained 候选不等于读取全部原始事实。重放核验以固定存储页作为 expected，不能用模型回传 candidates/resultDigest 建立 expected。

每个新文件 attempt 需要重做一次完整计算并与固定结果摘要对账，还是只验证不可变存储证明，是后续 profile 的明确产品/性能决定；本设计推荐首版发布文件前至少一次重算并比对，块下载继续只做实时轻绑定。不得偷偷对每块下载重新完整扫描。

## 6. 创建报告的循环依赖不能靠改旧快照解决

33 目前只接受已存在报告；现 `AiReportRun` 属 APPEND_ONLY，workflow input 也固定。持久化首片可以只服务已经存在的报告，不改变创建流程。

后续新 profile 可在首次 INSERT 的不可变 snapshot/input 固定 `screeningIntent`（预分配 screeningId、selectionPlanDigest、算法/容量版本）；scheduler 在该精确 ID 尚无完整发布结果或未完成真实工具页 preflight 前禁止创建/派发模型 job。扫描成功再 INSERT screening run/pages，**不 UPDATE 报告 snapshot**。结果 digest 在该不可变子记录唯一绑定；runtime 每次按固定 ID 获得并核对它。如何为 pending 任务建模、预检全部角色最坏转义帧和失败展示，是单独 runtime 切片，不能沿用旧 profile 静默启用。

## 7. 清理与恢复

首片最小方案：成功结果不可变且不自动清理；配额满时明确拒绝新发布。失败/取消扫描没有持久页，临时对象由 finally 释放，不产生待清残片。这与现有证据/预算/文件不可变保存策略一致，但不是无限可用的服务承诺。

后续若要回收成功结果，必须另有经授权的 retention 流程：拒绝删除被报告执行、job receipts、复核或交付引用的快照；先独立备份恢复验证，再受控清理并保留不可变 tombstone/审计，使旧 ID 明确 expired 而不是自动重算。不能仅按 created_at TTL 删除，不给普通 AI writer 通用 DELETE，不通过关闭 trigger 绕过存储合同。首片没有该管理流程，文档/UI 不得承诺“自动清理”。

两新表使当前 63 张 AI 自有表成为 65 张（以最终迁移清单为准）。需同时更新 models/database_contract、table_manifest、health 的精确字段/FK/index/constraint/trigger/function定义与 readonly grants；snapshot/backup/rehearsal 必须覆盖这两张表。重点是 `tools/postgres-consistent-backup.py`、`tools/django-postgres-maintenance.ps1` 和 `tools/ai-business-evidence-upgrade-rehearsal.py` 的历史迁移/表集合。旧 63 表备份仅在无新迁移记录时合法；新迁移已应用但少任一表必须拒绝，不能用统一替换列表破坏旧恢复。

逆迁移只允许两新表都空且无后续 screeningIntent/运行回执引用时进行；有成功 run 或任何页即拒绝。升级验收比较原 63 表完整摘要不变，新两表空；植入真实 sealed→prepare→publish 的合成结果后 pg_dump 到独立库，核行/页完整摘要、受限角色和实时报表绑定后读取，不调用模型、不允许恢复过程重算并覆盖原结果。

## 8. 一批可验收范围与分工

1. 纯持久页合同：完整覆盖/候选组集合，排序/分页、链摘要、字节计算、单行超限和精确版本；不改七条规则。
2. 独立 models/migration/health/grants：两表、不可变与完整发布 guard、并发 quota、历史备份集合。
3. owning store：33 Verified→publish、固定引用读取、权限复验、幂等及审计回滚；不开放新 Agent profile。
4. 独立复审及升级恢复工具：真实 synthetic sealed 数据，不用伪造 metadata 代替 authority；PG 由主 Agent 串行运行。

必须负测：尾页/最后 context 失权零发布，单行超限不截断，缺页/重复/乱序/错误下一偏移/空分区漏页/摘要重算后绑定篡改；两连接同键只一个结果、不同键接近 owner/global 配额不能超卖；单独子页 INSERT/已提交父再追加拒绝；发布响应丢失后同键恢复；跨 owner/report/scope 拒绝；unknown 算法缺表不降级；读 20 页仅一次原始 prepare，读取阶段 owning source pages 调用为零；旧 profile/catalog/file bytes 继续回归。

本片交付只说明“完整成功筛查可安全持久并按页复用”。模型上下文能否容纳全部要求的覆盖/候选页、五 Agent 独立证明、诊断引用与完整文件整合仍属于后续新 profile 准入，不能因保存成功就宣称这些能力已完成。
