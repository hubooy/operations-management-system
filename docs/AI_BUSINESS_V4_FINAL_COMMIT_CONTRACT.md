# v4 最终封存请求纯契约候选

`business_analysis.v4_final_commit_contract` 只计算一次 `commit-seal-v1` 意图的规范 SHA-256 摘要，不发行票据、不激活数据库角色、不写封存行，也不授予报告或 Agent 权限。当前没有生产调用入口。

调用方提供已有 `business-v4-parent-seal-internal-v1` **完整规范 `body_json` 原字节**、其 MAC、32 字节派生父封存密钥，以及受保护上下文中的 run/attempt、实际 actor email/version、父版本、计划/目录摘要与 keyId。模块先复用 `evidence_seal_v4.read` 校验正文结构和唯一规范 JSON，按 `SHA256(body_json UTF-8)` 计算正文摘要，再用 `HMAC-SHA256(derived_parent_key, body_json UTF-8)` 核验 MAC；父密钥派生目的字节与现有 `business_v4_seal_hmac` 完全相同：`teruisi:business-v4:parent-seal:v1\x00`。`derive_parent_seal_key` 仅供测试或受保护配发流程使用，正式运行时不应向 sealer 提供主密钥。

通过校验后，摘要对象严格为 `operation=commit-seal-v1`、`runId`、`attemptId`、`actorEmail`、`actorVersion`、`parentVersion`、`planDigest`、`directoryDigest`、`bodyDigest`、`bodyMac`、`keyId`；按项目 `contracts.canonical` 序列化并 SHA-256。函数只返回十六进制请求摘要，不返回正文、MAC、密钥或邮箱。正文中的 run/attempt、actorVersion、`parentVersion + 1`、计划/目录摘要、keyId 必须与请求字段一致。

`keyId` 在这里仅与正文比较；纯模块不能证明它来自当前 0036 段密钥版本，也不能证明 actor、claim、目录、来源修订或账本仍然有效。未来受保护执行器必须从当前权威重新核验这些值，并完成 0038 封存与 0043 消费的同事务门禁。此摘要本身不是授权票据，亦不是成功封存证明。
