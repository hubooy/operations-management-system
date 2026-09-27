# v4 独立封存身份的最小账本读取候选（0039）

0039 只给已由受保护 Provision 预建、默认 `NOLOGIN` 的 `teruisi_ai_seal_writer` 增加读取封存候选所需的权限。该身份仍没有独立凭据、常驻进程、公开路由、Agent、报告或文件发布。0038 的封存状态转移、seal 表、普通 writer 不可封存边界不变。

专用身份可只读 v4 的 run/source/chunk/receipt/attempt/segment/seal；`ai_write_authority` 仅可读 `id/status/authority_epoch/cutover_id`，`ai_data_revisions` 仅可读 `domain/revision/source_digest`。它对财务和网店业务事实/修订/marker 无直接 SELECT 或写权，对这些 v4 表无 INSERT、UPDATE、DELETE、TRUNCATE。`ai_tool_audit_logs` 与 `access_control_users` **完全不授直接 SELECT**：SECDEF `ai_v4_sealer_ledger_read_gate` 只按固定 run/attempt/当前无范围管理员校验并返回有限身份；`ai_v4_sealer_receipt_audit_segment` 只按同一 run/attempt/source 的一个最多16页分段，返回成功 `business_collection` 审计的请求身份、完整 `argumentsDigest` 和响应摘要，不返回 `arguments_json` 原文，更不返回其他 surface 的参数。上述函数要求真实 `session_user` 为专用身份并复验权限漂移；reader 和普通 writer 均无 EXECUTE。

**残余可见面：**专用身份对五张 v4 物理表的全表 SELECT 可直接读到其他 run 的 `payload_json` 原文；窄审计/账号函数并不能限制这几张表的直接 SELECT。默认 `NOLOGIN` 下它不可作为常驻进程连接，但这仍**不是行级隔离**。正式激活 LOGIN/独立 CLI 前必须换为同 run 的 SECDEF 有界分段流或 RLS，并证明专用身份不能直接读其他 run 原文；不能只凭应用传入 runId 声称数据库隔离。未来 CLI 还必须通过绑定 run/attempt 的窄函数核验当前账号，并逐块逐收据核 `run_id/source_id/sequence`、0036 HMAC 与真实原文字节。当前 `admission.inspect` 固定 writer 进程与 collecting 候选，不可通过只换数据库 URL 用作独立 sealer。正式 CLI 仍须抽不依赖普通 writer 进程权限的验证核心、受保护独立 DPAPI 凭据及同一角色的受控 LOGIN 激活，并在提交前完成真实全链验签和最终短事务 CAS。0039 的窄审计返回值与可读账本都不等于来源权威或可发布报告。

升级/回退必须在 55440–55999 隔离 PostgreSQL 中验证：0038→0039 不新增 AI 表，旧74表行、renderer1–7字节与旧角色 ACL 保持；新角色仅增加上述读权与两函数 EXECUTE；前后备份可独立恢复。逆迁移只撤 0039 权限/函数，不删 seal、页、段或审计事实，恢复0038 ACL。迁移和测试均不连接生产数据库。
