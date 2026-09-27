# AI 经营分析 v4 候选封存读取：续工交接（2026-09-25）

工作分支 `codex/ai-business-current-integration`，隔离工作树 `D:\.codex\worktrees\ai-business-current-integration\运营管理系统`。本检查点没有生产部署、正式数据库迁移、专用角色登录启用、付费模型或外发。主 checkout 的已有改动不属于本分支。

2026-09-25 同步主线单独新增的吉客云 4163 精确闭合修复 `c69573ac`（本分支 cherry-pick 为 `08fb752c`），相关 TypeScript 24 项通过；该同步与 AI 经营分析功能彼此独立，未触发吉客云业务执行。

## 已接与已验证

- 0049 后的 `ai_v4_sealer_replay_progress` 在实际 PostgreSQL 回读时，表列 `varchar(64)` 与函数声明 `text` 不匹配。0050 仅对该投影增加 `::text`，保留 claim 前后复验、函数身份/权限及写侧行为。错误原始日志为 `.runtime/ai-pg-9c5dae830fb1/failure.log`。
- 默认关闭的 `v4_sealer_step_core` 现以隔离受限角色、已领取 claim、合成推广及财务来源各一段执行候选写入/回读，并核重复请求和错误派生密钥拒绝；四项目标 PG 测试两轮通过，最后一次为 `.runtime/ai-pg-9b711eca6f63/tests.log`。20 项相关纯测试通过。所有返回仍为 `candidateOnly=true`、`authorityVerified=false`，无最终 seal。
- 0049→0050 完整隔离演练通过 `.runtime/ai-pg-7ddc07cd8f88/business-v4-replay-read-cast-upgrade-evidence.json`：79 张旧 AI 表及 renderer1—7 文件字节保持，所有其他 AI 函数不变，目标 READ 仅函数体变化而 OID/ACL/签名不变；迁移前后备份独立恢复、空回执逆迁移与重做通过。备份 helper 按迁移回执选取读取函数定义；健康检查固定要求当前 0050 定义。

## 接续边界

- 专用角色仍 `NOLOGIN`，无正式凭据/执行器。隔离测试的角色切换和当前 `authority_epoch`/`cutover_id` 注入只模拟受控会话，不是生产认证方案；当期权威标识须由未来受限交接核验，不能硬编码。
- 180 秒 claim 下的至少 17 页跨票据续段、未知写入结果读取恢复、最终 seal 与 0043 消费回执同事务、财务真实来源页及 575,095 行实际吞吐/磁盘峰值都未验收。候选回执不能给 Agent 或正式报告授权。
- 志高参考推广两期历史文件、七天未人审预览、来源差异和容量限制见 [真实来源续验](AI_BUSINESS_ZHIGAO_TRIAL_ACCEPTANCE_20260925.md)；UV、预算、市场/财务正式 Agent、真实模型与原生 Office 仍按 [总体状态](AI_BUSINESS_INTEGRATION_STATUS.md) 逐项推进。

下次先核分支 HEAD/工作树、0050 目标测试和升级证据，再接跨票据长段测试及默认关闭的最终封存包装。任何正式角色启用、生产维护或模型费用应以具体可审结果另行确认。

## 0051 续工补充

实际 17 页跨票据用例先暴露了 0048 写函数 `ticket_id` 列名歧义，原始 PostgreSQL 日志为 `.runtime/ai-pg-f6ae63683a08/postgres.log`；0051 仅限定前段 claim 表列。修复后三项迁移/逆迁移目标测试 `.runtime/ai-pg-5743154be602/tests.log`、完整 17 页两张票据自然到期续跑 `.runtime/ai-pg-6e739a683421/tests.log` 均通过。0050→0051 完整前驱升级、79 张旧表和 renderer1—7 字节保持、仅 RECORD 函数体变化、OID/ACL/签名保持、迁移前后独立备份恢复及空回退重做通过 `.runtime/ai-pg-7033b21c9550/business-v4-prior-claim-qualification-upgrade-evidence.json`。有候选回执时逆迁移会拒绝。

下一步最终 seal+0043 消费必须由新受保护数据库事务包装实现；0041 已撤销专用角色对旧直封函数的 EXECUTE，0043 没有消费写入入口。纯 Python 拼接两次调用无法满足原子性。独立角色仍 NOLOGIN，真实父 MAC、当前 authority 交接、正式 Agent/文件与生产采用仍待完成。

已加入默认未接线的 `commit-seal-v1` 纯请求摘要与父 MAC 校验，见 [纯合同](AI_BUSINESS_V4_FINAL_COMMIT_CONTRACT.md)；它不领取票据、不登录数据库、不写 seal。受保护事务包装需在代码与隔离角色测试通过后再考虑凭据及生产采用。

## 0052 与预算纯候选补充

0052 的[同事务包装](AI_BUSINESS_V4_COMMIT_CONSUMPTION.md)已在隔离 PostgreSQL 五项目标测试、撤权负例及 0051→0052 前后备份恢复演练通过；证据分别为 `.runtime/ai-pg-929da4f9223d/tests.log`、`.runtime/ai-pg-d5e9d4b49403/tests.log` 与 `.runtime/ai-pg-de8865ae6bd3/business-v4-commit-consumption-upgrade-evidence.json`。旧表、旧 renderer1—7 文件及旧 AI 函数目录保持，角色仍 NOLOGIN、直封撤权。只有拥有真实父 MAC 和当期权威交接的未来受保护执行器才能安全使用，当前不把合成封存当作正式报告准入。

推广预算 [renderer10 纯候选](AI_BUSINESS_PROMOTION_BUDGET_RENDERER10_CANDIDATE.md)已能对齐同报告批准绑定、固定预算引用与拥有方重算材料，投影三张预算表；无固定预算时只列缺口。预算与分卷相关38项纯测试通过。它尚无 owning 授权接线、完整 HTML/XLSX 发布、原生 Excel 复算或生产入口，v9 仍标预算未交付。

后续[renderer10 临时多卷](AI_BUSINESS_PROMOTION_BUDGET_RENDERER10_VOLUMES.md)已接 owning 前后重核与预算材料，隔离 PG 三项 `.runtime/ai-pg-ca0e72a2c458/tests.log`、预算/分卷纯回归41项通过；能生成临时 HTML/XLSX/JSON 并逐文件核摘要，但仍无持久 ready/下载路由或原生 Office 打开复算。

市场 v2 原 parked profile 的数据库禁止任何 Agent job；0053 另建[材料准入的暂停快照](AI_BUSINESS_MARKET_V2_ADMITTED_PAUSED.md)，保留旧根不变，真实 allowedTools/model 仍空且节点/job/派发/结果全被数据库拒绝。三项目标 PG `.runtime/ai-pg-86461140037a/tests.log`、目录撤权负例 `.runtime/ai-pg-7a6b5b162f07/tests.log`、旧 parked/材料/新 admitted 同库18项 `.runtime/ai-pg-edd8cc7dda38/tests.log` 与0052→0053升级/双备份恢复 `.runtime/ai-pg-f8faa8ebc693/business-market-v2-admitted-paused-upgrade-evidence.json` 通过；第五工具和多 Agent 实际运行仍要新的版本化激活门禁。

新[市场第五工具读取预览](AI_BUSINESS_MARKET_V2_FIFTH_READ_PREVIEW.md)纯3项、隔离PG3项 `.runtime/ai-pg-49dd0c30c191/tests.log` 通过，数值引用来自三张已证明材料；注入 job/provider ID 不是真实持久派发，返回始终 `persistedRead=false`。新[三期日期包络](AI_BUSINESS_PERIOD_BOUND_PLAN_V1.md)纯18项通过，本期/环比/同比日期预期已能独立固定，但尚未纳入正式计划/Agent/文件，也不把缺日推成零。

预算 0054 [持久暂存](AI_BUSINESS_PROMOTION_BUDGET_RENDERER10_STAGE.md)PG4项 `.runtime/ai-pg-74e6f194d49e/tests.log`、目录撤权负例1项 `.runtime/ai-pg-3c349136f20c/tests.log`，及0053→0054旧数据/字节/函数权限保持、双备份恢复 `.runtime/ai-pg-755537cd7e40/business-promotion-budget-v10-stage-upgrade-evidence.json` 通过；旧 v9 多卷/签名下载同库8项 `.runtime/ai-pg-1e89d4206954/tests.log` 通过。数据库双层拒绝 v10 `ready`，没有发布/下载；不能把暂存当作预算已交付。

0055 [三期日期候选侧表](AI_BUSINESS_V4_PERIOD_PLAN_SIDECAR.md)PG六项 `.runtime/ai-pg-ca5ae4f621c3/tests.log`、冻结79→当前80表和双备份恢复 `.runtime/ai-pg-1bd88d32948d/business-v4-period-plan-upgrade-evidence.json` 通过；仍无逐日零日证明或三基期正式Agent/文件。0056 [市场材料正式角色窄桥](AI_BUSINESS_MARKET_V2_ROLE_BRIDGE_0056.md)真实reader/writer组合10项 `.runtime/ai-pg-c50f832e0409/tests.log`、旧80表/两guard安全属性与双恢复 `.runtime/ai-pg-501877b11b13/business-market-v2-role-bridge-upgrade-evidence.json` 通过，0045侧表直读仍关闭且市场Agent仍未派发。

0057 [预算发布前证明](AI_BUSINESS_PROMOTION_BUDGET_V10_ATTESTATION_0057.md)目标PG3项 `.runtime/ai-pg-35577d49eae2/tests.log`，0056→0057冻结80→当前81表、旧文件/函数/ready语义保持、双备份恢复 `.runtime/ai-pg-1622c6faccd4/business-promotion-budget-v10-attestation-upgrade-evidence.json` 通过。验证角色默认NOLOGIN，证明并不开放ready；实际拥有方验证器、正式publish/下载、原生Office和业务规模仍需开发/验收。

0057 [拥有方全字节预检](AI_BUSINESS_V10_ATTESTATION_OWNING_PREFLIGHT.md)PG4项 `.runtime/ai-pg-571c024c5cbd/tests.log` 通过，仍不写证明；0058 [原子发布数据库门禁](AI_BUSINESS_PROMOTION_BUDGET_V10_PUBLISH_GATE_0058.md)真实角色PG2项 `.runtime/ai-pg-6646a50f5d94/tests.log`、0057→0058旧81表/函数/文件保持与双备份恢复 `.runtime/ai-pg-423a50ee3e05/business-promotion-budget-v10-publish-upgrade-evidence.json`、旧v9多卷/签名下载同库8项 `.runtime/ai-pg-c21f2c3f7847/tests.log` 通过。独立角色仍NOLOGIN，应用publisher与签名下载未接，原生Office/真实规模未验。市场第五工具[默认关闭目录候选](AI_BUSINESS_MARKET_V2_REGISTRY_GATE.md) Django3、Node12和本地构建通过，仍无实际Agent持久已读回执。

0058 注入连接的内部发布调用器真实角色四项 `.runtime/ai-pg-e5a8482f4cf5/tests.log` 通过，含丢失响应不重试和原票据 OUTCOME。上段“应用publisher未接”指正式受保护连接/公开入口未接，不影响这个默认关闭的内部调用器；不能宣称正式采用。

0059 [预算下载窄栅栏](AI_BUSINESS_PROMOTION_BUDGET_V10_READER_FENCE_0059.md)真实角色PG3项 `.runtime/ai-pg-27eda3e1c066/tests.log`、0058→0059旧81表/renderer1—7文件/旧函数权限与双备份恢复 `.runtime/ai-pg-2808dd63f241/business-promotion-budget-v10-reader-fence-upgrade-evidence.json` 通过。reader 无证明表 SELECT，旧任意摘要拒绝；尚无 v10 下载路由。当前小阶段的[独立交接清单](AI_BUSINESS_HANDOFF_0059_20260925.md)列出下一步与风险。

市场 v2 同 surface 五工具预览目录已合入且默认关闭，真实 reader PG3项及未发布负例1项、Node新旧24项、Django路由2项和本地构建通过。首轮误将词货v1报告喂旧screening-profile读取器导致1/2失败，改回原推广拥有方读取后通过；仍无同job/provider实际已读。见[市场五工具交接](AI_BUSINESS_HANDOFF_MARKET_V2_FIVE_TOOLS_20260925.md)。

默认关闭的[最终调用层](AI_BUSINESS_V4_FINAL_COMMIT_STEP.md)已通过两项隔离 PostgreSQL 实际角色模拟 `.runtime/ai-pg-e2bcd735e87a/tests.log`，并与前驱读取/回执/包装同库组合17项 `.runtime/ai-pg-e8139845286e/tests.log` 通过。调用层没有连接工厂或凭据、不会发行票据；结果未知只允许按原票据查询消费，不重试封存。真实业务来源、授权会话和报告采用仍需独立验收。
