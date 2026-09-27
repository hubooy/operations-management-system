# 词货五 Agent 必读内容容量预检

截至 2026-09-24，新词货报告可以持久保存固定五角色图与四工具目录，规则筛查和角色包读取已接入。新独立 `business_promotion_preflight.prepare` 能对已发布报告的**必读内容**返回 `capacityVerified=true`，但始终返回 `runtimeAdmissionGranted=false`，不能据此允许模型执行。

## 为什么不能复用旧容量预检

`backend/ai_assistant/business_screening_preflight.py` 的 `measure` 是旧筛查 profile 的完整协议验证器，而不是可替换工具名的通用估算器：

1. `_inputs` 只接受旧工作流输入字段，明确拒绝新报告固定的 `promotionRef`；同时用完整输入摘要核对已发布角色包，删除该字段以通过检查会破坏固定来源绑定。
2. `_measure` 要求目录恰好有旧 profile 的三个工具，并对旧固定图、工具名和执行面组装实际模型消息。新目录含第四个词货工具，且第五角色图的指令和输入字节均不同；把三工具测算结果标记成新 profile 容量通过会漏算消息中的第四工具声明及新图。
3. 新词货工具允许单次返回 38,000 字符，并有分页与精确行两种调用形态。即使首轮只要求完整角色包和可选预算，也必须在结果中写明未预留词货及原生分析的可选调用；实际每次读取仍须检查调用次数、轮次、字节和上下文，不能拿初始预检代替运行期限制。
4. 旧预算读取入口 `business_budget_store.load` / `binding_for_report` 的 profile 检查尚不包含词货 profile。新报告带预算时，不能将预算页省略、伪造为空成功，或拿公开预算参数充当已核对的完整预算页。

## 已接入的边界与仍需完成的条件

新模块应从当前持久报告、已封存证据、已发布筛查结果和当前无范围管理员身份开始；读取并验证五个角色从 `offset=0` 至 `nextOffset=null` 的完整页链，并在有预算时通过新 profile 的独立预算绑定重算并完整读取预算页。模型版本、执行指引、四工具全部 JSON Schema 与执行限制、五角色图、快照和工作流输入均须和当前根复验。

容量测算需对 OpenAI 兼容和 Anthropic 两种消息格式分别组装新图的六个节点输入、五角色完整包及必读预算页，连同四工具声明交给现有 `estimate_tokens` 和 `fit_context`。完整输入、转录、单次工具响应、总调用次数、轮次及模型上下文全部通过后，才可返回 **仅限必读内容** 的 `capacityVerified=true`；返回值仍为 `runtimeAdmissionGranted=false`、`modelDispatched=false`、`agentReadVerified=false`。若任一页、绑定、容量或当前身份变化，应拒绝或返回具体容量失败，不裁剪页或工具声明来制造通过。

`business_promotion_preflight.prepare` 已按上述合同读取五角色完整页与带预算时的完整预算页，并用当前模型配置分别衡量两种协议的消息。隔离 PostgreSQL 测试 4 项通过，见 `.runtime/ai-pg-93245b143cbc/tests.log`。结果明确标记 `requiredContentOnly=true`，没有为可选词货和原生分析调用预留页数；运行期仍须逐次复核调用次数、轮次和上下文。

运行期派发仍须另行具备状态租约、模型调用前的当前权限检查、真实工具调用和角色读取回执。现有 `business_promotion_admission.require_permission` 保持关闭；容量预检不是 Agent 派发、完整诊断或文件交付授权。
