# v3 财务上下文来源的内部计划目录

2026-09-24。`ai_assistant.business_evidence_v3` 仅在服务内部提供 `create/detail/directory`，没有写入公开 `views`、tool-registry、后台 collector、报告或文件入口。它把已提交的纯 `business_analysis.evidence_v3` 目录计划，使用原 AI mutation 锁和现有三张证据表保存为一个未采集任务；`ai_assistant.0028` 仍拒绝 v3 事实块、检查点推进、封存与报告。成功创建仅表明范围和身份规范可保存，不证明任何财务月份已有数据。

创建时要求真实 `access_control_users` 当前 email、role=admin、status=active、scope=NULL、version 有效；不存在本地管理员或旧 DTO 回退。每日 sales/netshop/market 来源复用 v2 计划规则，finance 来源仅为精确四字段账本 scope、连续1—24自然月及原始日区间的 `monthly_context`；来源目录/日期/月数不完整即拒绝。AI writer 事务内再验账号，使用用户与全局任务配额、客户端请求 ID 幂等及现有共享字节额度，一次写入规范 header 和所有有序来源。父任务固定 `status=collecting`、`collection_status=manual`、version=1、零字节/零证据块；不进入后台自动领取队列。

每次 `detail/directory` 从数据库读取不超过48条原始来源，重新运行纯 v3 合同并逐项核验规范 `query_json`、`query_digest`、ordinal、header/catalogDigest、父任务请求摘要、零检查点/块及当前账号。目录页按实际 nextOffset 读取，返回前再次复核账号、父版本及全部来源行。一次展示无法成为“已封存”或“Agent已读”；所有权限、持久来源及报告能力标志继续为 false。直接 SQL 在0028初始约束下若伪造一份形式正确但目录摘要不符的计划，内部 reader 仍会拒绝。

隔离 PostgreSQL 测试：`ai_assistant.test_business_evidence_v3_plan`。核验原子初始父任务+多来源、跨页完整目录、幂等碰撞、真实身份/精确月份拒绝、数据库初始内容篡改/延迟撤权，以及旧 v2 create/directory 回归。后续必须另立带真实 finance_reader 分页、独立检查点与封存证明的采集协议，不能修改这个计划片的 false 声明后直接启动 Agent。
