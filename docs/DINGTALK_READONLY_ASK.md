# 志高助手：钉钉只读问数

2026-09-10 已按用户“合并后一起发布并清理分支”的授权在本机生产采用：应用迁移 AI 0007、安装可选依赖、启用受保护配置及 Stream 监听。用户在本人单聊和测试群 @ 验收请求后确认“已经收到私聊回复”。此确认不等于销售、库存、网店三类真实模型分析均已验收。

## 2026-09-10 群对话增强（已发布）

用户要求群内 @ 原群回复、系统设置提供 AI 对话群配置，并开放全板块只读查询。本节能力已随 Worker `20260910T083226Z-2c53e5a99e7232d9` 在本机生产采用；下面首版记录保留为历史口径。

### 设置与回复

- 入口：系统设置 → 钉钉机器人 → AI 双向对话。与图片周报的目标群、时间和开关独立。
- 只有无数据范围限制的管理员可读取和保存 AI 群设置；已有系统账号绑定、组织、应用身份及 DWS 凭据仍通过受保护 runtime 配置管理，网页不能修改身份或接触密钥。
- 支持总开关与最多 30 个群的名称、精确 openConversationId、独立开关和移除。新加群默认停用。群必须安装已绑定的“志高助手”；发送前动态唯一核验群名称/ID、机器人 Code 和在线入群状态。保存配置本身不发送消息，也不等于平台核验或接收器在线。
- 群内 @ 的“正在查询”、结果及分析失败提示均由同一机器人回复原群；私聊仍回复提问者。群身份不符、机器人移除、授权变化或回执不明时，禁止改投私聊或其他群，禁止自动重发。
- 群回复对群成员可见，仅已绑定系统账号的提问者可触发，使用其现有权限和数据范围。不同群、同群不同提问者、私聊的上下文隔离；“新话题”保持原语义。
- 配置带 expectedVersion 并发校验与请求防重，成功保存递增版本并进入 AI 变更审计。接收器在接收、执行和发送时读取 PostgreSQL 配置；版本变化使所有排队/运行中的旧配置请求失效，阻止停用后迟到发送。下一次新提问建立新配置上下文。
- 受控接收器首次启动，将 runtime 中已经批准的应用身份和群采用到 `ai_dingtalk_settings`。后续启动只检查身份，不覆盖管理员保存的群和开关。runtime enabled 继续作为总控制门禁；网页开关不能绕过它。网页停用不会结束监听进程，因此运行中的接收器可在网页重新启用后继续工作；进程退出后仍需受控启动。

### 查询覆盖

中央注册表逐项开放 27 个 `dingtalk_chat` 只读工具，包括原七项查询，以及商品经营、补货、客服摘要、市场、财务、运营事务、导入、自动化状态、运营设置、本人 Agent 任务和通用数据集工具。

数据集入口覆盖销售、ERP、财务、网店、市场、商品、库存、运营事务、客服、权限、AI、BI 共 12 个数据领域。先用 `describe_system_datasets` 发现目录和字段 schema，再用 `query_system_dataset` / `get_system_dataset_records` 查询、筛选和连续分页。嵌套查询保留 `dingtalk_chat` surface，不借用网页入口扩大权限。所有已有角色、scope、owner、字段排除、结果大小、超时及审计约束继续生效；未来新工具不会自动开放。

逐行数据集仍仅允许无范围限制的管理员，AI 私有记录仍限本人。凭据、原始客户聊天、文件字节和其他用户私有内容不进入通用查询。不自动注入个人记忆或知识库，不执行导入、修改、删除、浏览器或脚本任务。销售/库存水位不能冒充其他领域截止日期；跨领域数据按来源分别披露，不能把分页或截断结果解释为完整全量。

### 数据库与受控采用

AI `0008_dingtalk_settings` 新增一个单例设置表，AI 表数由 48 增至 49。表有 authority 写入保护、不可变应用身份、版本和大小约束；reader 只读，writer 仅 SELECT/INSERT/UPDATE，不能删除或修改其他业务事实。接收会话与投递表保持原状。新表进入 readiness、角色与备份清单，从通用数据集目录排除。45/46/48 表旧备份继续根据自身迁移记录验证，不能要求旧备份凭空包含新表。

本次已按以下受控流程采用：

1. 确认候选测试、构建与隔离 PostgreSQL 演练通过，完成页面效果验收，与最新 main 合并检查。
2. 制作正式 PostgreSQL 一致性备份并在独立 cluster 恢复验证，受控停止接收器和需更新的服务。
3. 部署 Django 候选，应用 AI 0008，执行 AI ProvisionRoles，核验 reader/writer readiness。按现有 successor 流程发布 Worker 并回读启动绑定；不得仅更新数据库或只更新网页。
4. 使用已经批准的 runtime 配置受控启动接收器，首次采用已有机器人与群；在设置页确认 AI 开关及群。重启不覆盖设置。
5. 只读上线探针已核验设置、27 个工具、243 个数据集/12 个领域、私有工具排除及 reader/writer readiness，外发消息和付费模型调用均为 0；真实单聊、测试群 @、回复位置和模型答案仍需人工端到端验收。
6. 发布后备份并独立恢复验证。失败先停用 AI 对话并受控停止接收器，保留表、权限和审计，使用兼容或前向修复；不删除表或恢复 D1。

开发验证见 [群对话候选证据](evidence/dingtalk-group-ai-candidate-20260910.json)，生产采用、备份恢复和只读上线验收见 [统一发布证据](evidence/inventory-dingtalk-unified-release-20260910.json)。当前模型响应时间和稳定性限制仍见 [AI 故障复盘](AI_CHAT_RELIABILITY_REVIEW_20260910.md)，本次接入范围扩大不等于所有自然语言分析场景已验收。

## 首版已发布行为与历史记录

### 首版使用范围

- 使用现有“志高助手”企业内部应用机器人，Stream 接收消息，复用系统现有模型、中央只读工具、用户权限及 AI 审计。
- 首批只支持销售、库存和网店；单聊直接提问，“测试群聊”内需 @ 机器人。仅显式绑定的钉钉用户可以触发。
- 首版结果和“正在查询”回执均私聊给提问者。群内 @ 只是提问入口，不把个人账号权限扩大为全群数据披露权限。没有群内经营结果广播。
- 单聊、群聊、群内各用户各自保留上下文；发送“新话题”清空后续上下文，历史仍用于审计。网页可以查看本人对应 AI 会话，但不能向钉钉会话追加消息或删除其投递审计。
- 首批只处理文本，不接图片/语音/文件。较长答案明确截断；图片、外链和 @ 指令不会外发。不自动读取个人记忆、知识库或其他网页会话。

示例：“帮我看一下志高昨天的销售”“广东仓有哪些缺货风险”“京东某店昨天的商品表现”。网店仍须使用实际平台、店铺和 SKU/SPU 口径；访客数不是店铺去重 UV。

## 数据与身份

销售品牌由 ERP **当前**主数据的品牌字段精确筛选，以货品编码关联已发布销售事实，保留退款负数和原数据范围。空品牌或未映射货品不计入品牌结果；不是历史成交时品牌归属，也不是名称关键词匹配。API 响应披露品牌来源和排除规则，缓存继续采用销售/ERP 组合 revision。

每个正常分析请求先执行数据水位查询，成功后才调用模型。交互式群聊和个人私聊不套用模型配置的工具调用总数上限；仍保留 260 秒渠道总时限、模型工具轮数、各工具单次提问上限、权限、审计与取消门禁；定时任务仍遵守模型总数预算。相对日期按钉钉消息创建时间和 Asia/Shanghai 解释，队列跨日不改变“昨天”的基准。销售、库存水位不冒充网店完整覆盖，网店仍检查自己的查询结果。

配置中的 senderId 是钉钉企业内部 staffId；与系统账号、角色和 scope 快照显式绑定，不能依据昵称、模型输出或消息正文推断身份。所有接收、执行及发送阶段重新核验权限；配置变化使旧请求失效。范围受限账号仍可能没有库存总览等工具，禁止借用管理员绕过。

可复用经本机原门禁验证的 local-admin 绑定，但仅限明确绑定的本人；禁用本机直连身份后机器人同步失效。首批不自动绑定新成员。

## 接收与投递

入口是 Django 管理命令 `dingtalk_ask`，使用现有 AI writer 权限及 authority。没有新增公共回调接口、数据库业务写权限、D1 路径或新的模型执行器。Stream 主动出站连接，不需要开放入站公网端口。

新增 `dingtalk_chat` 工具 surface，在唯一中央注册表逐项开放七个工具：数据水位、销售汇总、销售品类/品牌分析、库存健康、库存子页、网店表现、网店子页。其他工具（通用数据集、个人记忆、知识库、财务、客服、写入、脚本、浏览器）不获得此 surface。网页请求体不能自行选择该内部入口。

新增 `ai_dingtalk_sessions` 和 `ai_dingtalk_receipts` 两张 AI 表，由迁移 `0007_dingtalk_readonly` 创建，AI 总表数从 46 增至 48。表有 authority fencing、不可变身份和状态/大小约束，读写授权和备份清单同步更新。两表不进入通用系统数据集或历史 D1 迁移摘要。

消息按企业、机器人、平台 msgId 防重；同一 ID 不同内容拒绝。待处理上限 24 条、单用户每分钟最多 6 条，原 AI 日额度、调用次数和总时长上限继续生效。

接收回调只做身份检查与持久入队，模型在独立工作线程运行。单实例 PostgreSQL advisory lock 持有到所有线程结束。发送前先持久化 sending；回执不明不重发。重启后 running/sending 变为 unknown，关联未完成 AI 回执也置为 unknown，不重复付费分析；已完成 ready 结果可继续受控发送。unknown 状态需操作员检查，用户可另发新问题。这里采用“至多一次发送尝试”，不承诺分布式网络上的恰好一次送达。

DWS 管理平台授权和机器人发送；应用凭据仅在内存中取得。每次核验机器人前调用当前用户只读接口，使 DWS 可以用既有 refresh grant 自动续期；refresh grant 本身失效时仍失败关闭，不扩大授权。发送必须收到 `success=true`、有效 `processQueryKey` 且无无效/限流接收者才记为平台接受；这不是用户已读证明。消息中的 sessionWebhook、access token、Stream ticket 和原始外部回执不写入业务表或日志。Stream 建联失败最多连续五次后退出，保留脱敏错误码，不无限重试认证。

## 配置与本机发布

模板是 `config/dingtalk-ask.example.json`。复制到受保护 runtime 的 `config/dingtalk-ask.json` 后，填入通过 DWS 查询得到的精确组织/profile、统一应用 ID、robotCode、群 ID 和本人账号绑定；不要把 AppKey/AppSecret/token 放进该 JSON。初始 `enabled=false`。

受控发布需要：

1. 完成隔离候选检查，核验最新 main 和变更范围，按项目合并流程处理；用户明确授权生产发布。
2. 制作并独立恢复验证正式 PostgreSQL 备份；受控停止相关本机服务并 DeployApp，在受控 runtime 安装 `backend/requirements-dingtalk.txt` 中的可选依赖。
3. 应用 AI 0007，执行 AI ProvisionRoles，重新核验 AI reader/writer readiness 和全部域状态。按现有 Worker successor 流程发布包含新工具 surface、品牌筛选适配的版本并重绑启动入口。销售 Django 读取代码也需采用。
4. 在 runtime 受保护配置目录放入精确配置，并使用既有 ACL 工具保护文件。先保持 disabled 执行只读核验，再启用配置并显式启动接收器。
5. 用本人单聊和测试群 @ 各发一条真实问题，确认原消息、AI 工具审计、回复及投递回执一致；这一步是上线验收，隔离夹具不能替代。
6. 发布后再次备份并独立恢复验证。

命令必须从已采用的受保护 runtime app 控制器执行：

```powershell
& "D:\teruisi-runtime\django-sales\app\tools\django-ai.ps1" -Action DingTalkCheck
& "D:\teruisi-runtime\django-sales\app\tools\django-ai.ps1" -Action StartDingTalk
& "D:\teruisi-runtime\django-sales\app\tools\django-ai.ps1" -Action StopDingTalk
```

StartDingTalk 使用现有 DPAPI AI writer 凭据与精确进程回执，后台启动接收器；不调用 Codex、DWS 的任意 agent 命令或个人账号自动回复。日志出现 `connected` 才代表已建立 Stream 连接，进程启动本身不代表端到端可用。停止 AI 栈会先停止接收器。

首版没有自动加入登录启动或外层 supervisor 的常驻监控。2026-09-13 本机已发布并启用 [接收器随系统自动启动](AI_DINGTALK_SCHEDULES.md#接收器随系统自动启动)：由既有系统启动引擎在 Worker 和各域服务就绪后调用受控 AI operator；手动 StopDingTalk 同时关闭自动启动，整套系统停止保留配置。仍要求登录原 Windows 用户，DWS refresh 授权需保持有效；接收器连续建联失败退出后的独立自动恢复尚未启用。

回退优先关闭问数配置并 StopDingTalk，网页 AI 与销售事实不受影响。保留两张新表、迁移、权限和投递审计；使用兼容修复或 PostgreSQL 前向恢复，禁止删除表后恢复旧 D1。恢复旧 45/46 表备份时，先在隔离环境按迁移版本验证，再前向补齐迁移；备份校验不能按最新表数错误拒绝合法旧备份。

## 验证入口与限制

### 聊天无回执诊断（2026-09-13 已生产采用）

定时任务发送成功、进程 running、历史 connected 日志均不能证明聊天入队正常。排障应对照平台消息时间、`ai_dingtalk_receipts` 与 `ai_dingtalk_schedule_runs` 的有界状态查询，区分入队前失败和分析/投递失败；不重放旧消息或未知结果。

接收器新增带时间的 `callback_received`、`callback_accepted`、`callback_rejected` 和 `callback_unavailable` 日志。拒绝原因区分组织/应用、未绑定发送人、不支持消息类型、未批准群或未 @、文本结构、时间有效期及其他权限/输入问题；配置读取与数据库上下文故障独立标明。只记录固定标签，不记录消息正文、人员/群/消息 ID、Webhook、凭据、ticket 或原始异常。永久拒绝仍 ACK 200，暂时不可用仍 ACK 503，防重、权限和发送行为保持原契约。

源码 `c14f8090` 已合入 main 并受控采用，Django manifest SHA 为 `eb6aeed6c8ad45a4335bd05dfc04c2b2267af643f14c765c261cf5dbba9d2e63`。本机通过整栈启动恢复，接收器自动连接；配置与自动启动文件摘要保持一致，无迁移、未知结果重放或人工外发。发布记录见 [诊断补丁采用证据](evidence/dingtalk-ingress-diagnostics-production-20260913.json)。补丁上线本身不代表真实聊天故障已修复；必须对照一条新提问的接收、入队、处理与投递证据。采用前的旧日志没有 callback 记录，也不能据此推断平台未投递。

同日 15:15 用户发送的新私聊已通过接收、持久入队、AI 生成及原私聊投递，约 15 秒后回执和结果均为 `sent`，错误码为空。用户随后提供的截图确认终端已收到该回复。此证据说明重启后该次私聊链路恢复；群聊与长期稳定性仍须分别验证。中午无响应的具体原因因缺少旧接收日志仍未确定，不把诊断补丁称为已确认的根因修复。

同日按用户要求，将当前 5 个模型的 `generationOptions.systemPrompt` 在线设置为使用“运营管理系统 AI 助理”中文称呼，不添加英文品牌前缀，并通过版本校验保存与回读验证其他配置保留。新请求读取该配置，历史消息不改写。默认源码文案同步清理，但该源码清理尚未执行生产部署；在线配置修改不需要重启接收器。见 [中文称呼配置记录](evidence/ai-chinese-identity-20260913.json)。

- `python backend/manage.py test ai_assistant system_datasets sales.tests.test_api --noinput`
- `python tools/dingtalk-postgres-rehearsal.py`：独立 55457 端口，合成数据，验证 0006→0007、旧会话保留、真实最小权限角色、AI readiness、错误 epoch/身份/状态拒绝和 48 表 dump/restore 一致；结束停止独立 cluster。
- `npm run build`、`npm run test:unit`、`npm run lint`、`npm run check:backend-boundary`。

库存 GuangdongMonitorItem 的六个已有 override 字段原先漏记于通用清单，本次明确列为 excludedFields，保持不对通用数据集开放并补齐覆盖检查。部分原数据集测试使用简化 authority 夹具，缺少正式 PostgreSQL 要求的字段；这些测试在 SQLite 通过，PostgreSQL 本次采用相关聊天/问数/品牌测试和真实角色门禁，未放宽生产约束。

初始候选验证见 [候选验证记录](evidence/dingtalk-readonly-candidate-20260909.json)。统一候选进一步通过 Django 145 项（144 通过、1 跳过）、Node 2073 项（2053 通过、20 跳过）、构建及 20 项构建产物测试，lint 无错误（9 条已有警告）；最后的 DWS 续期/回执修复通过 50 项针对性测试（49 通过、1 跳过），新增京东修复及 AI 控制器测试全部通过。最终部署与备份恢复摘要见 [统一发布记录](evidence/unified-release-20260910.json)。

官方参考：[应用机器人](https://open-dingtalk.github.io/developerpedia/docs/learn/bot/overview/)、[Stream 模式](https://open-dingtalk.github.io/developerpedia/docs/learn/stream/overview/)、[Python SDK](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)。
