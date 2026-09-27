# 京东市场 v2 五工具执行档案候选（0060）

`business_market_v2_execution_snapshot.create` 是内部写入入口。它从 0053 的材料准入报告出发，复核 0044 停放报告、原封存词货报告、管理员及同店范围，重新计算市场观察选择的 context digest，并要求中央新 surface 当前返回冻结的五工具目录。目录版本为 `3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743`；两个固定图摘要分别是无预算 `6a0b655cec19328b1c1e02e78330320e04f699b5037868faf42e2ac8f6b0f40d` 和有预算 `905ae3d59bd4e9554b22c7373274d0569aa2c9e5da7182338351fbf20623f1c2`。原词货 v1 工具目录和 0044/45/53/56 的根记录不改。

创建的报告及流程是新 profile `business-agent-screening-promotion-market-execution-v2`，只保存五工具可调用目录及计划图。流程保持 `paused`、`market_v2_execution_not_activated`、空模型、零轮次；数据库触发器拒绝节点、Agent job、provider/tool dispatch/result 和任何激活或修改。**工具在中央目录可调用，不等于 Agent 已读取或任务可运行**。它没有同 job/provider 持久读取回执、数值引用证明、人工审查通过或报告/文件发布权限。默认运行旗标仍关闭，没有公开启动路由或付费模型调用。

0060 迁移新增执行档案触发器，并在保留 OID、所有者与 ACL 的条件下版本化 0044 report/workflow 两个守卫：旧 parked 行无论被改成什么 profile 均不可更新；新 profile 只在另一个固定守卫验证后可插入。子记录 UPDATE 同时检查 OLD 和 NEW 归属，防止旧子记录改挂逃离。旧停放、材料准入和 v1 行保持不可变。逆迁移只允许新 profile 零行。数据库把 selector/manifest/source 与 0045 不可变材料逐项核对；当前 0045 没有单列持久 `marketContextDigest`，所以数据库只校验其 SHA 形状，内部服务从封存源重算。真正开放执行前，应新增不可变 context 证明，并另做同 job/provider 回执、模型策略、数值引用及开关；不能从本档案推断这些能力已经具备。

纯合同测试：`python -m unittest ai_assistant.test_business_market_v2_execution_snapshot_contract`（3 项通过）。隔离 PostgreSQL 目标：`ai_assistant.test_business_market_v2_execution_snapshot`，须使用现有受控 runtime 角色预置测试环境，且不调用模型；该目标已验证真实材料根的 paused 行及防逃逸负例。0059→0060 升级/备份/空逆迁移演练使用 `tools/ai-postgres-rehearsal.py --business-market-v2-execution-snapshot-upgrade --upgrade-only`，复核旧 81 表、renderer 1–7 字节、旧函数/角色 ACL、两个 0044 精确版本化函数及新触发器。升级种子已有较高 netshop 修订，不能在该种子中重放低版本业务夹具；paused 正例与长链演练分开验收，保留单调门禁。生产未迁移或启动。

整合隔离验收已完成：真实角色目标五项 `.runtime/ai-pg-d13cc457288e/tests.log`（141.351 秒），0059→0060 演练 `.runtime/ai-pg-45ae3111b231/business-market-v2-execution-snapshot-upgrade-evidence.json` 中旧 81 表/行、renderer1–7字节、旧函数/市场根保持，0044两个守卫仅精确变更且OID/ACL/owner不变，前后备份独立恢复与空逆迁移重装通过。目标测试首轮两个负例先被通用不可变触发器拒绝，后在隔离回滚事务暂禁该精确通用触发器，独立核实 0060 专用拒绝；升级首轮的临时正例被旧网店修订单调门禁阻断，演练脚本移除该重复夹具后通过，未放宽门禁。
