# 0078 签名发布与只读分块候选

0078 只在隔离 PostgreSQL 演练库提供一条默认关闭的侧车链。迁移建立不可变的 `protected_business_budget_v11_publications_v2` 回执，保留旧 0066 的 v11 `paused`/`ready` 硬拒和现有公开下载硬拒。它不是正式发布，也不使任何 HTML/XLSX 对 Web 用户可下载。

发布入口只授给现有 `teruisi_ai_budget_v11_publish_login`，新分块入口只授给初始 `NOLOGIN`、无密码、无成员的 `teruisi_ai_budget_v11_download_v2_login`。两个角色都不能直接读受保护签名回执、私钥表或文件块表。函数仅在精确命名的本机隔离库及限定端口接受真实非超级 LOGIN。正式安装不激活这两个角色、不装生产密钥、不接 Web 路由。

`publish_signed_v2` 锁定同一 run/attempt/version 的暂停文件行和 0076 签名回执，重算当前报告、工作流、管理员、预算、五个已完成 Agent 的账本页链、文件页链根以及活动私钥的固定目的域 MAC。请求摘要绑定 ticket、claim、owner、report、key 与这些根；单 run 只能写一条回执。发布调用结果未知时，只能以原请求摘要走只读 `publish_outcome_v2`，不得盲重发。已存在回执时写入口固定拒绝。

`read_published_chunk_v2` 要求独立的 `REPEATABLE READ READ ONLY`（或 `SERIALIZABLE READ ONLY`）事务，不调用带 `FOR SHARE` 的旧函数；它的版本化私有辅助函数在同一事务快照复核当前根与活动密钥，然后重新读取所请求文件块的实际字节、长度和 SHA。调用方每块须开新事务，使下一块能观察密钥撤销；同一个长 `REPEATABLE READ` 事务看不到开始后提交的撤销。仅靠数据库函数尚不能强制“一事务只读一块”，因此不能接正式下载。每次最多返回 524,288 原始字节；去掉 PostgreSQL base64 折行后最多 699,052 字符，另加小型 JSON 元数据。返回固定 `candidateOnly=true`、`readyAuthorized=false`、`releaseAllowed=false`。请求摘要本身不是最终用户授权，尚缺独立按报告/店铺/主体核验的下载许可。

隔离验收必须包括真实非超级发布/只读角色、read-only 事务成功与写事务拒、错 run/attempt/claim/root、密钥撤销、块字节或摘要篡改、断序/越界、重复发布和结果未知；目录测试必须拒绝表/列/函数额外授权、触发器 `WHEN false`、同类但放宽的 CHECK/FK、角色 LOGIN 意外激活和旧函数 OID/源码/ACL 漂移。0077 市场暂停五 job 只有拓扑，没有五 Agent 的已读账本和 0076 签名，必须拒绝发布。当前纯合同测试不代替这些 PostgreSQL 验收。

2026-09-26 首轮独立 55833 角色 PG 2/2 通过：真实非超级 publisher/reader、reader 只读事务要求、受保护表/私钥直接访问拒绝、精确 CHECK/触发器/ACL 事务漂移拒绝；`pg_ctl` 已停且端口无监听。证据在 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-a8c6c972827b-v11-publication-role\tests.log`，SHA-256 `734bb7ebe667ea858932aeca415f4efb7d6ebeb94fbdd342de322601e16a3c63`。这轮没有构造真实 0076 签名后发布/分块正例，也没有证明 0077 市场暂停候选的数据库级拒绝、满载容量或跨集群 owner/ACL 恢复。

下一轮 55834 运行四项，前三项通过，包括从真实合成 0076 签名到单条 publication、只读角色逐块复核并重组非空 manifest/HTML/XLSX、合成密钥撤销后拒读；第四项 0077 夹具在原 `record` 开关处提前拒绝，因此整轮标为失败，原日志保存在 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-995c588a6690-v11-publication-e2e-first\failure.log`，SHA-256 `04a7425adc34a9ecef96de4b325c0873f17e66f73ac7b999902c683c0e7cc8f4`。仅在测试局部补原有开关后，55835 单跑有效 0077 paused 报告拒绝 1/1 通过，确认五个 paused job、零模型/账本、文件行创建被原 effect guard 拒且 publication 零行；`E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-14957db7a4ad-v11-publication-market-denial\tests.log` SHA-256 `ac02c40539f0abb185439762c292d74963cacfa47c27d9ad217bcc56390a32f0`。

最终同版 55836 整类隔离 PostgreSQL **4/4 通过**，包括完整合成签发/逐块全字节、密钥撤销、目录/权限漂移与有效市场暂停负例；证据 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-c81436f99c77-v11-publication-full-role\tests.log`，SHA-256 `8fe26fe6e8c7e536e7c8f5cdd0c7cc48dd3d489573fa0bee0ba315119da88d1e`。所有 55833–55836 隔离集群已停且无端口监听；此候选始终不授 ready/下载。

55837 focused 0077→0078 升级/双独立恢复通过：54 个旧函数的 OID/源码/ACL/owner 在源库迁移前后保留，独立恢复忽略物理 OID 后源码/owner/ACL 一致；14 个旧受保护表的 owner/ACL、旧文件块字节摘要保留。旧受保护表及旧文件块在本轮均为空，不能将零行对比当成有业务文件的恢复证明。新目录 15 表、11 函数；空逆迁移后保留全局 `NOLOGIN`/无密码 reader role、无 0078 表/函数，重装成功。正式 backup 在建档前、restore 在建目标库前、PrepareApp/DeployApp 在维护动作前拒绝。证据 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-bf894ef2684c-v11-publication-upgrade\business-v11-publication-upgrade-evidence.json`，SHA-256 `88f60ef6d4c56779509ca8d334cafd5c3c65dda16ac022097acad67df1f2ef52`；迁移前后测试 dump 摘要记录于该文件。源集群已停、55837 无监听。仍缺非空受保护数据的**跨集群**加密 owner/ACL 保真恢复和最大容量测试；正式路径继续关闭。

55838 单标签再次完成真实合成 0076 签名→publication→只读 HTML/XLSX，并在一条真实、非伪造 publication 行存在时调用 0078 逆迁移，准确因非空拒绝、未删回执；1/1 通过。证据 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-05984ed20238-v11-publication-nonempty-reverse\tests.log`，SHA-256 `af91854ffb711ff597873be6f26383c97808810f7cb02e731395d5f584abba14`。该隔离集群已停、55838 无监听。

正式采用仍缺生产密钥保管与独立签发进程、单报告 attestor/下载许可、真实五 Agent/源读取与费用来源验收、容量与时限实测、受保护迁移安装/加密 owner/ACL 保真备份恢复，以及版本化的公开下载/ready 原子切换。正式 PrepareApp/DeployApp、日常备份与恢复继续在 0078 之前失败关闭。
