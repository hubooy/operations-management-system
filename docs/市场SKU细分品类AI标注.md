# 市场 SKU 细分品类 AI 标注

> 本文保留早期功能设计。文中的 D1、Drizzle 上线步骤和 SQLite 租约说明已经退役，不得用于当前部署或恢复；当前后端与正式采用依据 [Django 市场迁移说明](DJANGO_MARKET_MIGRATION.md)。2026-09-13 对照当前实现确认的并发、任务权限与复核缺陷及候选修复见 [可靠性修复说明](MARKET_ANNOTATION_RELIABILITY.md)，候选代码尚未采用到正式系统。

该功能位于“市场分析 → SKU AI 标注”，把市场 SKU 的视觉识别、人工复核和最终入库放在同一条可恢复、可审计链路中。云端视觉模型是默认执行器；本地 Ollama 是可选执行器和容灾方案。

## 上线准备

1. 依次应用 `drizzle/0016_market_sku_annotations.sql`、`drizzle/0017_market_annotation_reliability.sql`、`drizzle/0054_market_annotation_concurrency_settings.sql`、`drizzle/0055_market_annotation_throughput.sql`、`drizzle/0057_market_annotation_cloud_runner.sql` 与 `drizzle/0066_market_annotation_runnable_work.sql`。
2. 在 AI 助理中至少启用一个 `vision` 模型。云端模型必须使用 HTTPS 的 OpenAI-compatible 或 Anthropic 接口并配置加密 API Key。
3. 为需要标注的三级类目创建 Prompt 草稿，完成冻结抽样验证后由管理员激活。首次还没有金标时，管理员可以填写审计原因显式激活；完成首批人工复核后，应把已确认记录加入金标集并重新验证。

## 标准操作

1. 选择三级类目。页面默认选择“云端视觉”和一个已启用的 vision 模型。
2. 使用该类目当前激活的 Prompt 创建任务。任务从完整的 `market_ranking_entries` 和价格快照中选择符合条件的 SKU，不受榜单页面展示上限影响；单批最多 10,000 条。超过上限时，上一批进入 `review_ready` 后按钮会显示“创建下一批任务”，旧批次仍可继续人工复核。
3. 在独立的并发卡中设置模型并发数，再点击“开始/恢复云端识别”。并发范围为 1–50，按“三级类目 + 执行器”记忆；云端默认 10、建议 10–20，本地 Ollama 默认且建议 1。已有任务可在“当前任务并发”中直接修改，运行中点击“保存并应用”会在下一批即时更新。页面会立即触发首批，随后由 Cloudflare 每分钟的原生定时任务继续领取；运行状态、自适应并发、冷却截止时间和协调租约全部持久化在 D1，因此关闭浏览器甚至关闭电脑都不会中断云端识别。本地 Ollama 依赖本机 runner，电脑关闭时仍无法继续。每次模型请求只处理一张图片；同一任务内按“类目 + scope + SKU + 榜单维度 + 图片哈希”领取唯一推理单元，跨月同图只推理一次并将结果批量写回全部月份。只有 `queued`、`running`、`failed` 且确有可重试推理单元的兼容任务会被恢复；`review_ready` 或 `committing` 批次不会拦截新批次，无可重试项时唤醒会明确失败并提示创建下一批。单次模型或网络异常从 5 秒短冷却和温和降并发开始，同一冷却窗口不会因多路同时失败直接跌到 1，重复异常才继续阶梯降级；供应商限流仍按减半并发与有界退避处理。连接每连续成功 3 张增加一路，直至恢复该类目的配置值。页面只轮询轻量进度，用户可随时点击暂停，在途图片完成后不再领取新图。
4. 人工检查大图、实际来源、AI 细分品类、主图价格、置信度和依据。可以编辑细分品类与价格（单位为分），再勾选需要入库的候选项。
5. 管理员点击“批准并入库”。请求必须携带明确的 candidate/item IDs 和批次幂等键。最终结果写入 `market_sku_annotations`，审计前后值写入 `market_annotation_commit_receipts`。

主图识别价存放在独立字段 `image_price_cents`，不会覆盖榜单的 `market_ranking_entries.price_cents`。

## 图片安全与来源

- 只接受 HTTPS 的 `img*.360buyimg.com` 京东图床地址。
- 识别前优先按源图地址读取已经过 MIME、魔数、大小和哈希校验的 R2 图片缓存；缓存缺失、对象丢失或元数据不一致时才安全回源。
- 模型输入图最长边可缩至 1600 像素并编码为质量 88 的 WebP；只有衍生图有效且更小时才采用。旧缓存缺少该衍生图时会按需生成并持久化，转换失败仍回退原图，业务图片哈希和人工复核原图不变。
- 豆包 Seed 系列在这个严格 JSON 分类/抽取任务中显式关闭额外思考，完整分类输出最多 600 tokens，仅价格识别最多 320 tokens；每张图的取图、图片处理、模型调用、总耗时和输入大小会落库，页面展示最近 100 张的平均值以便继续定位瓶颈。
- `/imgzone/` 大图优先；输入 `/n5/` 时先尝试同路径的 `/imgzone/`，失败后才回退 n5。
- 请求使用手动重定向，仅接受 JPEG、PNG、WebP，并校验文件魔数；拒绝 SVG、MIME 伪装、3xx、超过 8 MiB 的响应、超时、凭据、fragment、非标准端口和非白名单域名。
- 每条候选保存实际使用的 `imgzone`、`n5` 或 `none`，页面明确展示。

## Prompt、验证与进化

Prompt 版本不可变。任何人工编辑、AI 生成或 AI 进化都会创建新版本，并保留父版本、来源、说明、创建人和指标。

- “冻结抽样测试”从人工金标中按细分品类分层，默认且至少 50 条；金标不足会明确阻断，不能以小样本放行。
- 创建运行时会把 SKU、商品名、品牌、图片、细分品类金标和价格金标完整快照到结果，并把 seed、模型与规范化快照共同写入 hash。后续推理与结算只读快照；已完成运行直接返回持久化指标，禁止重算。
- 每个样本的金标、预测、价格、置信度、错误和 Prompt 版本都会保留。
- 候选 Prompt 与当前激活版本使用相同冻结样本 A/B 测试。
- 自动激活门禁同时约束首版绝对准确率、宏平均、失败率、价格覆盖率、价格 MAE，以及相对当前版本的各项退化。
- 自动进化只能读取父 Prompt、类目枚举和通用规则；同一 sealed holdout 的金标、预测和错例不会进入生成上下文，避免调参泄漏。需要基于错例训练时应另建与 holdout 隔离的训练集。
- 管理员可在门禁未通过时填写原因显式确认；回滚通过重新激活旧的不可变版本完成，审计历史不会被覆盖。
- “系统和 AI 设置”不呈现也不请求完整市场 SKU 目录，避免目录检索拖慢标注、复核和 Prompt 工作台；目录检索 API 仅保留给有界的后台管理工具使用。

## 本地 Ollama（可选）

Cloudflare Worker 无法也不会回连用户电脑的 `localhost`。本地 runner 使用一次性 agent token 主动向云端领取任务，claim 带 5 分钟 lease，统一使用 SQLite `datetime()` 格式；回传必须绑定 agent、item 和未过期 lease。过期任务可重新领取，旧 token 无法覆盖新结果。

管理员先在页面创建 agent 并立即复制只显示一次的 token。服务端只保存 SHA-256 hash，撤销后不可再使用。

PowerShell 示例：

```powershell
$env:TERUISI_SITE_URL = "https://你的站点"
$env:TERUISI_ANNOTATION_AGENT_TOKEN = "创建时显示的一次性token"
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
npm run market:annotation-agent
```

runner 只允许 `OLLAMA_BASE_URL` 指向 localhost，并使用任务中的本地模型名、Prompt 明文、品类枚举和安全下载的京东图片调用 Ollama `/api/chat` 严格 JSON Schema。

## 权限与并发

- 所有已登录角色可只读查看。
- `operator` 和 `admin` 可创建任务、执行识别、编辑复核、创建/测试 Prompt。
- 只有 `admin` 可批量入库、激活/回滚 Prompt、把记录设为金标以及创建/撤销本地 agent。
- 人工复核先全量校验，再以 job mutex 与 item `version` 条件批量更新；入库使用同一互斥锁、请求摘要和 D1 原子 batch，避免复核/入库 TOCTOU。
- 云端识别和冻结验证均使用带 token、截止时间、最大三次尝试的可恢复 claim；崩溃超时会重新排队，旧执行结果不能覆盖新 claim。
- 云端与本地任务均由服务端按类目并发配置执行原子 claim 门禁；前端或本地 runner 的 worker 数不能绕过 1–50 边界。配置变更保留操作者与前后值审计，相同值重复保存不产生重复审计。
- 可运行任务使用稳定工作键防止并发重复创建；该唯一槽只覆盖 `queued`、`running`、`failed`，等待复核或入库的批次可以与下一批并存。同图推理结果使用 D1 批量写回，扩散阶段失败会把任务置为可恢复复用状态，后续请求会继续补齐而不会再次调用模型。
- 文件导入的榜单幂等、标注任务项唯一键、入库回执唯一键和 agent lease 分别处理不同层面的重复与并发。
