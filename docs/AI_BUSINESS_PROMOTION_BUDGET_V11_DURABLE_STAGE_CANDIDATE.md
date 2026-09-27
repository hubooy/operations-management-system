# renderer 11 持久暂存但不发布：0066 候选交接

本切片基于整合 `91f47e07`。已将市场 `0065_business_market_v2_model_cost_reservation` 精确提交合入本独立分支，并创建 `0066_business_promotion_budget_v11_durable_stage.py`，其唯一迁移前驱正是 0065，调用本候选模块的 `install/uninstall`。后续已由整合分支完成隔离 PostgreSQL 验收；未对生产执行迁移。

`business_promotion_budget_v11_stage_sql.py` 从已冻结 0058 文件函数正文作精确一次替换：原文件版本 CHECK 增加 11；卷块 guard 仍只准 **building 且同 attempt** 写入；compact 清单 guard 新增 11，保持完整 HTML/XLSX/JSON 坐标、分片大小/SHA、1 GiB 总量门禁。run guard 对 v11 初始 queued、非草稿、64 位绑定和当前同报告批准五角色/预算根加门禁；身份字段不可变，原状态 CAS、容量和 terminal 规则保留。只有 `paused + error_code=renderer_unpublished + progress.stage=staged_unpublished` 才能调用新 `ai_budget_v11_stage_requirements`。该窄 SECURITY DEFINER 函数只在 `session_user=teruisi_ai_writer` 下工作，固定 search_path、PUBLIC 撤权、仅 writer 可执行，不给 reader 或 writer 宽表新授权；它拼接完整 JSON 清单原字节，核 compact SHA、当前管理员/报告/预算根、v11 full/预算/slim 证明及每卷表片 gzip/NDJSON 长度与摘要。deferred complete guard 提交时再核整卷块链与暂存证明。run 和 complete 两层均对 **任何 v11 ready 无条件抛错**；没有独立 attestor、publish、reader、签名下载或 UI。

内部 `business_promotion_budget_v11_durable_stage.py` 的 create/control/binding/build 只有 `AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED is True` 才执行；公开文件创建、控制和单文件/多卷下载均不注册 v11。文件 builder 只把 v11 分派给此 stage-only 模块，从不调用发布器。拥有方从同报告已批准 DTO、封存来源与固定预算重新生成 v11 临时文件，逐片持久写入；随后重建所有持久卷和清单，核块顺序/长度/SHA、v11 完整证明、实际 HTML 压缩字节和全部规范行摘要，并用当前来源再次渲染逐 HTML 原字节和 XLSX ZIP 成员内容比较。前后复验身份/预算；只有成功才更新 paused stage。任何不明结果保留原任务与分块，不把异常当成功，也不直接 ready。SQL 对自洽 JSON 证明只作结构与摘要门禁；真实 HTML/XLSX/拥有方语义必须由受保护 Python 验证，未来发布还需独立验证角色。

新守卫、唯一 writer 执行权限及精确正文已接健康检查和一致性备份回读；旧 1–10 检查按迁移版本保留。纯/static SQL 与 HTML 目标 11 项通过，且迁移检查显示 ai_assistant 无模型状态差异；隔离 PostgreSQL 正反测试 `ai_assistant.test_business_promotion_budget_v11_durable_stage.BudgetV11DurableStageTests` 真实 writer 暂存三项 `.runtime/ai-pg-8b34c361d408/tests.log`（172.186 秒）通过。旧 v10 分卷下载/撤权四项 `.runtime/ai-pg-718e1d69e665/tests.log`（319.196 秒）、v9 多卷五项 `.runtime/ai-pg-2215c3d8ef06/tests.log`（503.833 秒）通过。独立升级恢复另见下段，仍无生产采用。

已新增显式 harness 入口 `--business-promotion-budget-v11-stage-upgrade --upgrade-only`，串行重放旧链与 0065 的双恢复证据后，执行 `business-promotion-budget-v11-stage-upgrade-rehearsal.py`。脚本只准独立工作树、test 环境、隔离端口与精确 `0064->0065` 成功回执。最终完整演练 `.runtime/ai-pg-2555a7bf63a8/business-promotion-budget-v11-stage-upgrade-evidence.json` 通过；它核验：

前三轮分别在升级前恢复副本 raw ACL 比较、误要求独立 stage 函数含状态字面量、升级后恢复副本 raw OID 比较处停止；恢复副本现按 grantee/grantor/privilege/grantable 与规范化关系身份比较，主库仍要求旧函数原 OID/ACL 不变；状态字面量由 run/complete 双守卫精确核验。三次失败均未计成功，第四轮完整通过，不输出原始业务行。

1. 在精确 0065 隔离库上冻结 AI 表清单、旧 1–10 文件行/块 SHA、五个旧文件函数 OID/正文/ACL/owner 与 v10 ready 回执；先做独立 `pg_dump` 并恢复到另一个一次性库逐项回读。
2. 安装 0066，要求无新增表或角色；只允许五个既有函数中的卷块、清单、run、complete 四个正文版本变化，原单文件 chunk 函数不变。验证 CHECK 精确加 11、新 SECDEF 函数仅 writer EXECUTE、无 PUBLIC/reader、旧函数 OID/ACL/owner 与 complete guard 的 SECDEF 位不变。
3. 升级演练是**空 v11 行**，只校验数据库中精确 ready 拒绝守卫正文、窄函数 ACL、85 表数据和当时存在的旧 1–10 文件。真实 ai_writer 按同批准报告创建与完成 v11 `staged_unpublished`、重读 chunk/slim proof 以及跨报告/预算根、伪造 gzip 或行摘要、缺卷/晚到多块、错 attempt、缺批准/管理员撤权、direct writer ready 与 reader 写入/执行拒绝，须由独立目标 PG 测试 `BudgetV11DurableStageTests` 执行；v10 既有证明角色与 ready/下载也须另作目标回归。旧升级种子固定包含 renderer 1–7，8–10 若不存在，演练会明确列出而不会冒充已有旧字节已覆盖。
4. 0066 后再次独立备份/恢复核同摘要；空 v11 库逆迁移回 0065 并重装，原旧函数正文/权限回原值；只要存在任何 v11 行，逆迁移必须拒绝且保持现状。未知提交结果按精确迁移与行回执查询，禁止盲重放。

尚有真实规模阻塞：现有 `files.BUILD_SECONDS` 单轮 600 秒。57.5 万行合成 v10 瘦身生成与静态全验约 472 秒，v11 持久暂存还需分片写库和从批准来源**重新渲染一次**，不能声称在 600 秒内可完成；本切片没有放宽或绕过超时。需要独立规模/持久容量测量及受控续跑设计，或明确失败并保留未发布块。Office 原生公式重算、真实三窗口及市场/B端/销售源授权与多 Agent 报告仍未由此验证。
