# 0069 市场 v2 逐轮预留独立静态审查

审查对象：`dc9af14e`。本次仅检查代码和测试，未运行隔离 PostgreSQL、未修改迁移或生产环境。

## 必须修复的测试阻断

- `test_business_market_v2_paid_round_role.py:16,87,99,123,165` 复用 `test_business_market_v2_material_role_bridge.session_role`，但该 helper 在 `:24-26` 只接受 `teruisi_ai_reader` 与 `teruisi_ai_writer`。三种新 NOLOGIN 角色会在执行 SQL 前触发 `ValueError`，所以现有“真实角色”目标测试无法证明 0069 的采纳、预留和开始派发。应在新测试里使用独立、精确白名单的会话授权 helper，不放宽旧 helper。

## 安全边界与上线阻断

- `0069...py:99-177` 只能证明调用者给出的费率、FX、摘要和审批字段形状/算术相符；`rateEvidenceDigest` 与 `approvalEvidenceDigest` 仍绑定 0065 调用者候选，管理员邮箱存在且有效也不能证明本人批准。合成者可同步构造候选和这些摘要。当前 `:70-72` 强制 `syntheticOnly=true`、`authorityIndependentlyVerified=false`、`providerCallsAllowed=false`，因此不能把这个演练缺口表述为已有付费通道；正式付费采纳必须有独立可信的来源、人审和撤销记录。
- `0069...py:193-207,242-262,356-372` 强制隔离数据库/端口，以及无登录、无成员角色的 `session_user`。这让普通应用无法调用；只有隔离超级用户切换会话身份才能运行测试。正是当前关闭边界，不能把三函数直接当成可上线运行时接口。
- `0069...py:263-265,320-344` 用同一 authority 行 `FOR UPDATE` 串行预留，按同槽 ID 检查重放并汇总上界；`START :373-406` 锁 slot，追加唯一事件后拒绝重派。静态检查未见并发重复扣额的明显路径，但目标测试 `test_business_market_v2_paid_round_role.py:82-147` 只有顺序调用，未证明两连接交叉竞争、回滚或崩溃后的状态。当前 SQL 回执全为 `providerCallsAllowed=false`。

## 健康与升级验收缺口

- `0069...py:586-610` 健康检查只比对触发器名称/函数/启用状态、约束类型数量；未检查触发事件位、约束表达式或外键目标，也未检查 `:462` 的 held 索引。等量替换约束或同名但少事件的触发器可以让 `verify_catalog` 通过。发布前应核完整定义与索引，并增加漂移负例。
- `tools/business-market-v2-paid-round-upgrade-rehearsal.py:118-130,186-203` 冻结旧表的 reader/writer 有效权限、旧表行、文件字节及旧 AI 函数元数据，但不是所有旧表 ACL/owner/约束的完整快照。迁移本身未改旧表；应把验收声明限定为实际比对范围。
- `0069...py:503-521` 逆迁移仅在三张表均为空时成功，并刻意保留三个 NOLOGIN 角色；脚本 `:232-243` 验证空逆迁移再应用，却没有恢复迁移前完全相同的角色集合。该部分可称为“空数据可逆”，不能称为数据库身份状态完全逆转。

结论：当前 0069 只能作为**关闭付费入口的合成演练候选**。先修测试 helper，再串行运行真实角色测试和 0068→0069 独立升级/备份恢复；修复健康漂移核验后再据证据评价数据库约束。即便测试通过，仍缺独立费率和人审权威、真实 provider 派发/结算，不能开启付费调用。
