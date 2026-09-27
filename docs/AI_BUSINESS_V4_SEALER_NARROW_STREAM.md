# v4 封存身份窄流读取候选（0040）

0040 仍是默认 `NOLOGIN` 的隔离候选，没有独立进程、凭据、公开路由、Agent、报告或生产调用。它撤回0039给专用 `teruisi_ai_seal_writer` 对五张 v4 物理表（块、收据、尝试、分段、seal）的跨 run 直接 SELECT；审计与账号仍无直接 SELECT。`ai_business_v4_runs/sources` 的 SELECT 暂保留，因为0035封存后的延期父计数触发器在专用身份提交时以调用者权限读取它们。专用角色仍可枚举这两表的 run/来源元数据；没有业务财务/网店事实表直接读写权，v4账本与seal无任何直接DML。

三只固定 `search_path` 的 SECDEF 函数由数据库受信所有者执行，仅专用角色可调用：

1. `ai_v4_sealer_read_context(run,attempt,actor,actorVersion)` 读取指定 run 最新 attempt、当前无范围管理员及固定完成目录；只返回有限计划/目录与可选seal身份，声明 `runBoundCapabilityVerified=false`。
2. `ai_v4_sealer_read_segment(run,attempt,source,index,actor,actorVersion)` 返回指定同run分段的最多16页起止、规范进度原文/摘要及0036 HMAC。数据库核段范围、来源修订和存储SHA，**不能核应用HMAC**；客户端须用受保护密钥自己重算。
3. `ai_v4_sealer_read_page(run,attempt,source,sequence,actor,actorVersion)` 一次只返回一页。数据库重算当前原文UTF-8字节和SHA，绑定完成来源、同attempt覆盖段、chunk/receipt、`receipt.surface='business_collection'`、成功审计的完整 `argumentsDigest` 与响应摘要，且强制 `audit.created_at≤chunk.created_at≤receipt.created_at`；拒错店/跨run拼接、缺收据、错页序和超过财务38,000字节或推广131,072字节的页。原 `arguments_json` 与其他工具审计均不返回。PAGE 的块/收据/段及 SEGMENT 本段均用无 `LIMIT` 的 `SELECT INTO STRICT` 强制恰好一行；即使高权撤掉旧唯一约束并复制同序号行也失败关闭。调用前后重查当前账号/目录/attempt，撤权或并发改变失败关闭。

未来 CLI 可按 context→每段一次→段内每页一次的固定顺序流式消费，并逐段验0036 HMAC、原文内容/请求链、自然月与京东窗口口径；最终提交前仍须短事务锁源、CAS 与独立seal正文签名。0040没有实现CLI，不把SQL字节/身份检查当作来源权威，也不声称大运行时限已验收。0040一旦安装，旧0039直接读取测试仅作为历史升级演练，当前专属PG测试以真实 `SET SESSION AUTHORIZATION teruisi_ai_seal_writer` 验证窄流和宽读拒绝。

**正式激活硬前置：**知道其他 runId/owner 的被攻陷专用角色仍可伪造函数的 `actorVersion` 入参并读取其他已知run。`runBoundCapabilityVerified=false` 是真实边界；仅让函数检查runId或设置调用方可控GUC/RLS不能解决。未来0041必须由已认证的AI writer发行随机、短期、用途/目录/run/attempt绑定且数据库可验证的票据，专用角色按票据领取有界扫描lease；所有0040读函数及0038提交函数须改为票据/lease校验并撤旧无票据EXECUTE，才能考虑受控LOGIN+DPAPI。当前宽读撤销降低误用和原始数据暴露面，但不等于凭据泄漏后的跨run保密性。

升级与逆迁移只改函数/ACL，不增表、不改旧报告文件。隔离 PostgreSQL 演练须验证0039→0040前后各自独立备份恢复、旧74张AI表行/renderer1–7字节/普通角色ACL不变、NOLOGIN保持，逆迁移恢复0039全读候选ACL但不删已存在seal/页/段；有事实亦不得丢失。
