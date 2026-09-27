# 市场 v2 材料权限窄桥（0056 候选）

0045 市场材料侧表故意不给 `teruisi_ai_reader` 或 `teruisi_ai_writer` 表/列权限。0053 创建触发器原先以调用者权限读取侧表，而未注册第五工具预览也直接 ORM 读取；开发角色隔离测试能通过，但不能证明正式角色可用。

0056 保留 0044/45/0053 的报告、材料与触发器语义，只把 0053 **report/workflow 两个已冻结函数**在核实原正文、owner、OID、ACL、固定搜索路径和侧表权限后，用 `CREATE OR REPLACE` 改为 `SECURITY DEFINER`。函数仍执行原全部身份、同账号、selector、材料摘要及停放状态检查，由 0045 侧表的原数据库 owner 完成只读查询；`ai_reader`/`ai_writer` 仍不能直接 SELECT 侧表。新报告/流程继续 `paused`，工具列表空、模型空，原 0053 节点/job/第五工具派发/结果硬拒保持。

新 `ai_market_v2_admitted_material_metadata` 只给真实 `teruisi_ai_reader` 执行。它要求六项精确声明：新报告 ID、当前账号邮箱和版本、停放报告 ID、selector 摘要、材料清单摘要；数据库再次核账号 active/admin/无范围、两报告与原来源、0045 不可变材料原文字节 SHA、指定双观察日及零派发状态。只返回来源报告 ID、准入摘要、selector/manifest 摘要、双观察日覆盖和 summary 摘要，不返回原始 manifest/summary、明细或任意侧表行。当前账号授权仍由 Django `current_principal` 和报告 owner 检查负责；数据库函数不接受目录遍历，也不能替代应用层身份认证。

写入服务不再用 ai_writer 直接查侧表：先在事务外由原拥有方完整重算三张市场材料，并以 PostgreSQL 的 `jsonb::text` 口径计算 selector 摘要；在短事务中复验停放报告后插入新行，0053 definer trigger 才与 0045 的真实侧表精确比对。读取适配层不再 ORM SELECT 侧表，转而用新函数的有界元数据，并继续每次调用原 owning preview 重放三张完整表；`persistedRead=false` 不变。

测试必须在**真实 session_user** 两角色下做正反探针：writer 可创建但不能直读侧表或执行 reader 函数；reader 可经窄函数与第五预览读取本人精确报告，但不能直读侧表、跨账号/selector/manifest/报告取数；普通 SQL/伪版本/撤权拒绝。目录、health、备份恢复要核两原函数 OID/ACL 不变且只有 `prosecdef` 从 false 到 true，新增函数精确正文/owner/search_path/ACL，0045 侧表及列 ACL 仍关闭，0053 其余五个触发器不变。空逆迁移可恢复原 `prosecdef=false`；已有 admitted 报告时拒绝撤窄桥。此前开发角色 0053 测试不替代这项正式角色验收。

本候选依赖 `ai_assistant.0055_business_v4_period_plan_candidate`。真实 `ai_reader`/`ai_writer` 角色、旧 admitted 与第五预览的十项隔离 PostgreSQL 组合 `.runtime/ai-pg-c50f832e0409/tests.log` 通过（257.656 秒）。首轮 writer 正例因隔离夹具未设置当期 authority GUC/记录而失败，已在测试中建立精确 `postgres` authority 并复测；未改变生产权限。升级/备份恢复仍单独验收，0056 不注册第五工具、不调用模型，不使市场 v2 成为已完成 Agent 分析。

0055→0056 完整隔离升级 `.runtime/ai-pg-501877b11b13/business-market-v2-role-bridge-upgrade-evidence.json` 通过：旧80张 AI 表、renderer1—7字节、0044/45/53 原行和所有非目标函数保持；两只 guard 仅 `prosecdef` 由 false 变 true，OID/正文/ACL/owner 不变，新窄读函数的同 owner、精确ACL、0045侧表与列的运行时角色撤权、前后备份独立恢复及空回退再升级均通过。真实 Agent/job/派发仍未启用。
