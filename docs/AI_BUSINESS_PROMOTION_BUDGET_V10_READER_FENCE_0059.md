# 0059：版本 10 当前态下载栅栏与 reader 窄回执

0059 只建立下载前置证明，**没有公开分卷处理器、路由或前端下载按钮**。已有 0057/0058 证明与 ready 回执包含 `publicationFenceDigest`，但此前只检查摘要形状；任意旧候选摘要不能成为下载授权。

`business-promotion-budget-v10-download-fence-v1` 由当前拥有方行计算：已完成并人审批准的报告/工作流快照和六节点、五 Agent 输出与版本、唯一人审事件，封存证据版本与原值摘要，固定预算有无、引用、计划与绑定原值摘要，当前文件任务/尝试/清单/字节及确定性证明 ID，并绑定完整清单、预算 proof 和批准内容摘要。正文按固定键的规范 JSON 计算 SHA-256。受保护验证角色可在 0057 证明写入前调用 SQL 正文函数，再由纯 `promotion_budget_v10_download_fence.fence_digest` 计算；0059 的 reader 回执函数从当前行重新构造同一正文并在数据库重算。证明 ID 由 `(run,attempt)` 确定，不把依赖自身正文的证明 SHA 放进栅栏，避免循环依赖。

只有 `teruisi_ai_budget_v10_attestor` 可直接执行正文函数；只有 `teruisi_ai_reader` 可执行 `ai_budget_v10_download_receipt`。后者固定核当前 ready、账号所有者与活动管理员、同尝试的 0057 不可变证明、0058 完整 ready 回执、发布请求摘要、预算及封存根，再比较当前栅栏。仅返回文件与证明身份、SHA、版本和预算状态，不返回正文、原始证明或客户数据；reader 继续没有证明表 SELECT。任何旧的任意 `publicationFenceDigest` 不匹配时拒绝。

此函数是未来独立 v10 分卷下载处理器**每片前后**可调用的窄门禁，不是下载功能本身。后续处理器还须复核当前签名 principal、compact 描述符、片序/长度/SHA 和最终组装 SHA；Next 客户端要显式识别 v10 多卷与完整 ready 回执，避免当前 ready v10 错落旧双文件按钮。不得给 reader 文件证明表、工作流事件或 Agent 审计表的宽 SELECT；也不得复用 v9 Python 栅栏作为 reader 权限替代。

本切片仍无专用验证角色登录/凭据，未部署生产。隔离 PostgreSQL 需用真实 reader/attestor 会话核 Python/SQL 正文同值、预算有无正例、错误账号/尝试/绑定、证明表无 SELECT、正文函数不对 reader 开放、旧任意栅栏 fail closed；还需 0058→0059 升级/备份恢复/空回退。原生 Excel 许可和参考 30 天规模验收仍是正式工程文件的独立门槛。

2026-09-25 隔离验收：纯/SQL 合同 3 项通过；真实 reader/attestor PostgreSQL 3 项 `.runtime/ai-pg-27eda3e1c066/tests.log`（324.189 秒）通过上述预算有无、权限、错误身份与旧任意摘要拒绝。0058→0059 演练 `.runtime/ai-pg-2808dd63f241/business-promotion-budget-v10-reader-fence-upgrade-evidence.json` 保持旧 81 表和行、renderer 1–7 字节、旧函数 OID/正文/ACL、v9 ready 与 v10 发布门禁；前后备份独立恢复、空逆迁移再安装通过。演练种子没有 v10 ready 行，ready 正例由前述真实角色测试覆盖；备份纯测试 22 项通过。全部仅在隔离库，无生产写入。
