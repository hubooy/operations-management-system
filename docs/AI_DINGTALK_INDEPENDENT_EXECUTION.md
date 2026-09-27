# 企业机器人独立认证与定时任务独立执行

2026-09-20 已按本轮发布授权在本机采用，源码 `243927e`，Django fingerprint `89df0589ddbb138bc47ac327c1e7720cf9726a197ba6932cd5ce26604afc9c24`，Worker/helper 保留 `20260919T054421Z-8396938e0c741f6c`。生产证据见 [采用记录](evidence/dingtalk-independent-production-20260920.json)。用户明确将本轮限定为这两项，不新增看门狗、调度心跳、告警、崩溃自启或补发机制。原有 Stream 有界重连保留；它不负责恢复定时执行器。

## 行为

- 日常运行不再调用 DWS 的个人身份、授权刷新、应用凭据或发送接口。文字、截图、图文和文件均通过企业内部应用令牌调用固定钉钉官方接口；每次投递取得新应用令牌，不持久缓存短期令牌，不因令牌或网络错误自动重发。
- `dingtalk_ask` 只负责 Stream 入站及聊天回复；`dingtalk_schedule` 独立处理 AI 定时任务，使用独立进程回执。Stream 连接或 SDK 失败不阻塞已启动的定时执行器。
- 任务定义、周期、时间、绑定员工、创建管理员、任务版本、实时权限/范围、群审批和执行历史继续使用既有 PostgreSQL 表；无数据库迁移。发送前的持久预留、未知结果终态和五分钟过期窗口保留。
- 定时文字在应用令牌和目标核验之后、外发调用之前增加任务版本/权限回调，防止在准备发送期间停用或编辑任务后仍发送。截图继续在上传后复验。
- 定时执行器独占旧合并接收器的 PostgreSQL 锁 `(841327,1909)`，旧程序或另一个执行器不能同时消费定时队列。新版聊天接收器使用 `(841327,1910)`；两类进程仅恢复各自的执行账本。定时锁由独立数据库连接持有，每次读策略及发送前复查连接；失去锁连接即停止，不重连接管。

## 企业凭据

新 operator `ConfigureDingTalkBotCredentials` 仅用于首次采用已有应用。它使用 DWS 一次性核验既有组织、profile、应用、机器人及批准群，从现有应用读取凭据，在 Windows 当前用户下通过 DPAPI 加密后 create-only 写入：

`D:\teruisi-runtime\django-sales\secrets\dingtalk-bot.dpapi.json`

加密内容绑定 `profile/corpId/unifiedAppId/robotCode/robotName` 和 appKey，不向命令行、环境变量、仓库、日志或证据输出明文 AppSecret。运行时通过原生 CryptUnprotectData 读取；缺失、损坏、非 Windows、身份不匹配、重解析点或硬链接均失败关闭，不回退个人登录。配置和凭据文件摘要纳入进程启动指纹，父目录沿用受保护 runtime ACL。当前 operator 不提供覆盖、轮换或自动找另一个应用；轮换须单独受控实施。

## 群目标和平台权限

运行时以管理员已经批准的精确 `openConversationId` 为收件人权威。群名作为展示标签，不再每次通过个人 DWS 按群名搜索，不因同名群或重命名重新解析/替换目标。发送前直接针对该 ID 调用 `/v1.0/robot/groups/robots/query`，要求“志高助手”唯一存在且 robotCode 与加密绑定身份一致；撤销审批、移除机器人、身份不符或查询失败均不发送，也不改发本人或其他群。

这是对旧“群名称搜索”核验方式的显式替换：群显示名称变化不会把消息引向别的群，也不再仅因改名拒绝同一批准 ID。应用的组织和机器人身份仍由采用时的核验与加密绑定固定，平台令牌及发送接口会复核应用有效性。

2026-09-20 真实只读检查已取得企业应用令牌；所选群机器人查询接口明确返回 `Forbidden.AccessDenied.AccessTokenPermissionDenied`，要求 **`qyapi_chat_manage`（钉钉群基础信息管理权限）**。DWS 权限目录确认该权限存在、候选检查时 `authed=false`，包含 34 个平台接口；本实现只增加上述只读查询入口，不使用新增权限修改群。本轮已按用户发布授权开通该项权限，三个已批准群的真实企业认证查询均通过；DWS 命令契约为 `risk=high / confirmation=user_required`。不申请曾调研的 `AntDing.Read.Send` 或 `IM.Group.ReadWrite.Special`。

接口字段参考已发布官方 Python SDK `alibabacloud-dingtalk==2.2.60` 的 `robot_1_0.GetBotListInGroup`。平台权限、正式凭据读取、三个群查询、两个独立进程及 Stream connected 已在生产分别验收。最终客户端真实投递未手动触发，不能把连接成功当成已发送。

## 受控采用

生产切换须获得当轮维护和权限扩大授权，不能用本候选记录代替。步骤如下：

1. 使用当前配置实时核验原企业应用，复验上述权限状态；仅在明确授权后申请 `qyapi_chat_manage`，再用企业应用令牌只读核验全部批准群。不创建群、修改成员、变更机器人或发送消息。若平台未授予权限或查询结果不符，停止采用。
2. 完成现有发布前备份、独立恢复核验、归档和运行任务排空；临时关闭原钉钉自动启动，再复验排空，通过持久维护及唯一服务引擎切换 Django runtime 和核验 ACL。结束维护后先通过唯一引擎恢复核心服务，此时渠道启动仍关闭。不能覆盖运行中的 app，也不能绕过 runtime 摘要门禁。无业务数据迁移；n8n 无需重启。
3. 从新部署的受保护 `app\tools\django-ai.ps1` 执行 `-Action ConfigureDingTalkBotCredentials`。该命令要求已采用的 AI 钉钉配置及 PostgreSQL authority；拒绝覆盖已有凭据，不启用或派发任务。
4. 执行 `-Action DingTalkCheck` 核验企业认证、批准群及绑定账号，再恢复原自动启动批准。网页已经运行时必须调用 `tools/worker-local-service.ps1 -Action Start`；外层 `operations-system-control.ps1` 的 already-running 快速返回不会再次派发渠道启动。现有 `AutoStartDingTalk`/`StartDingTalk` 先启动独立调度，再启动接收器；每个进程独立核验回执与指纹。完整 Stop、手动 StopDingTalk 和部署门禁同时覆盖两类进程。
5. 核对原两条任务定义/版本/目标/历史保留、两类进程身份、Stream 连接及全栈就绪。旧手动队列过期只记 `missed_window`，旧定时槽跳过；不得自动补发今天的消息。
6. 如需立即发送验收，另获具体任务和目标的发送授权后入队一次，回查精确执行记录及钉钉客户端呈现。未知结果不得再次执行。

回退须先停止两类新进程，确保无正在发送或未知结果被重放，再按既有受控 runtime 回退流程恢复兼容代码。凭据文件不自动删除；不得因回退清空任务、改写历史状态或恢复旧业务数据库权威。旧代码仍依赖个人 DWS 授权，回退不保证消除原故障。

## 验证边界

候选证据见 [独立执行候选验证](evidence/dingtalk-independent-candidate-20260920.json)。隔离 PostgreSQL 245 项（243 通过、2 跳过）、真实独立锁与队列、角色边界及备份恢复通过；启动专项 6、页面 20、构建、lint 与后端依赖边界通过。全量 Node 的 `color-surface-subtle` 缺失为已独立复现的基线问题。

正式两条任务定义及原 20 条终态历史的完整摘要一致；旧 09:07 手动队列只转为 `denied/missed_window`，发送成功数未增加，下次均为 2026-09-21 09:00。生产 61 条迁移清单不变，发布前备份独立恢复及前后 E 盘归档通过。维护窗口周报检查 3586 失败，随后 3587/3590 自然成功；马思图 3588 失败交原安全重试 3589，未人工补跑。没有迁移、n8n 重启、付费模型调用或测试消息发送。
