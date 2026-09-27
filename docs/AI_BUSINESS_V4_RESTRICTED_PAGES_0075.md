# 0075 同报告受限 sealed-v4 页读取：隔离候选

`0075_business_v4_report_restricted_page` 新增只读版 BINDINGS/READ 与 `ai_v4_read_report_promotion_page`。后者在独立隔离测试库、回环端口、`teruisi_ai_reader` 会话及只读事务之外拒绝运行，返回前再核只读状态；每页先后核对 0071 创建时同报告链接，限定三期中一项推广来源，再核原始 chunk、工具 receipt、审计、字节数与 SHA-256。reader 仅能执行报告绑定的 RO READ 和 PAGE，不能单独调用无报告约束的 RO BINDINGS；也无直接读取受保护链接、chunk、receipt 或工具审计表的权限。旧 0040 sealer 函数与 0071 链接函数的正文、OID 和 ACL 不改；新函数无业务写入。

`business_v4_report_restricted_page_owning.inspect_candidate` 在隔离测试中复用现有 v2 报告与 v4 HMAC 拥有方核验，并以**另一个真实 reader 连接且 `transaction_read_only=on`** 逐页双遍读取三期。调用方 stub、开发 ORM 的同一连接或关闭只读事务均拒绝；它不使用开发连接直接读取物理页。返回结果仍是 `blocked_legacy_v4_report_authority`：旧 seal 固定 `reportGenerationSupported=false`，财报店铺映射未获独立权威证明；不签发报告可用 seal、不注册 Agent、renderer 或下载，也不生成文件。

验收先跑纯测试与静态正式门禁；真实角色、篡改页、跨报告/店/日期、修订漂移、三期来源完整性、旧函数目录摘要及独立备份恢复须在**隔离 PostgreSQL** 串行复核。独立升级演练脚本逐项冻结旧 0040 和 0071 函数的 OID、正文、ACL、owner，覆盖升级、空逆迁移和重装；尚未运行。正式 PrepareApp、备份、恢复的现行格式识别 0075 并继续提前拒绝；本迁移不具备正式安装或发布资格。575,095 行合成物理页压力和真实业务来源全量验收均未由此代码自动成立。

隔离 PostgreSQL 首轮已复现旧 0071 READ 的硬阻断：真实 `transaction_read_only=on` 时，旧 BINDINGS 的网店修订 `SELECT FOR SHARE` 报 `ReadOnlySqlTransaction`，证明简单复用旧函数不可行。0075 现从冻结的 0071 SQL 定义生成**新名字**的 RO BINDINGS/RO READ，精确仅去两处 `FOR SHARE`；PAGE 与拥有方改用 RO 版，旧 0040/0071 对象原样保留。无锁读取不能声称锁内线性化，必须靠逐页首尾当前态、双遍相同摘要与最后 HMAC/链接重核，任一漂移则拒绝。

55792 单一隔离 PostgreSQL 角色套件 6/6 通过，含合成 reader **真实 LOGIN**、`transaction_read_only=on`、三期各一页、另一个有效同 owner 报告/来源错配、finance 错源、直接表读取拒绝和 owner/第三角色 ACL 漂移回滚。该夹具的页是合成空页，证明 SQL 身份和权限，不证明真实 v4 HMAC/全期逐页双遍、业务 575,095 行或文件。0074→0075 双独立恢复/旧函数冻结演练及真实全量仍待验收。

最初的全历史升级链在 0075 前因当前 finance.0005 与旧 AI0036 逆迁移形成 Django 混向计划而受控停止，未触及 0075，也未产 0075 归档。新的**仅隔离测试** focused 演练从当前全量迁移完成态将空 0075 逆至 0074，保持 finance.0005 应用，再执行 0074→0075。它先后将前驱和升级后数据库各恢复到独立新库，比较旧 0040/0071 函数源码、ACL、owner（同源还比 OID），并复验 0075 目录；空逆迁移、重装及正式备份对 0075 的精确早拒也在同一演练中检查。每次明文测试 dump 前要求 11 张受保护表精确齐备且全空，注入合成私钥的负例必须在归档文件产生前拒绝并回滚。此 focused 演练不能替代正式加密备份或生产恢复。

55793 focused 演练现已 **exit0**：旧 0040/0071 共 9 个函数的 OID/正文/ACL/owner 在源集群保持不变；前后两个独立恢复、非空合成私钥提前拒绝、正式备份按受保护迁移的精确原因拒绝、0075 空逆迁移及重装全部通过。隔离源集群已停机；合成证据和两个空受保护表 dump 逐文件 SHA 核验后保存在 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\0075-v4-restricted-pages-focused-20260926`，`manifest.json` SHA-256 为 `951c37f91a0d422ef745c9465f84ea49fff96841e3f52f06d57d6148785d06a5`。这仍不证明真实全量来源、财报店铺权威映射或可报告 v4 seal。

为避免与其他隔离任务的历史端口窗口混淆，最终角色套件又在**单独空集群 55794**、固定 E 盘 Python 环境中重跑，6/6 通过且源停机/端口无监听；`tests.log` SHA-256 `84399fd9e02ce1cf038d23ab4050e8614ee1a6037e95f2b959ac0cd727359ed2`，归档于 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\0075-v4-restricted-pages-role-final-20260926`。本结果仅限合成角色与空页身份/ACL 验收。
