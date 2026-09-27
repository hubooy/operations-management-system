# renderer 10 默认关闭的签名分卷读取候选

此切片仅在现有签名转发的 `/api/ai/business-files/{run}/volumes/{index}/chunks/{format}` 增加版本 10 专用分派。后端 `AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED` 默认不是 `True`，版本 10 请求在读取证明前失败；未增加文件创建、公开发布或 UI 下载入口，也未激活 0057/0058 的 NOLOGIN 验证角色。版本 9 路由、文件字节和权限分支不改。

开关仅供隔离验收。每个 v10 分片先后重核当前签名 principal 为活动、无范围管理员且是文件所有者，任务精确版本/尝试/绑定和完整 compact 清单，并调用 0059 `ai_budget_v10_download_receipt`。此 SECURITY DEFINER 函数在数据库核同尝试不可变证明、原子 ready 回执、当前审批/预算/证据栅栏；reader 仍无证明表 SELECT。服务只按描述符读取指定卷和片序，核 512 KiB 片上限、实际长度及 SHA-256，第二次回执或清单变化则不返回内容。响应带当前回执摘要、ready 版本和文件 SHA；签名认证仍来自既有 Next→Django HMAC 转发，不把普通 SHA 误称为新的签名。

客户端新增独立 `downloadBudgetV10Volume` 候选，每片请求前重新核账号，再核版本 10 回执摘要一致、任务/卷/序号/大小/SHA，并在整卷重组后核文件 SHA、重取任务与账号才创建 Blob。最终任务根回执中的 `publishRequestDigest` 或 `owningVerificationDigest` 变化也拒绝释放文件。既有 `downloadBusinessVolume` 显式拒绝 v10。当前页面即使看到内部已 ready 的 v10，也只显示“页面下载入口尚未开放”，不再误落旧双文件按钮；实际 UI 启用、正式路由发布和生产开关另需受控工作。

隔离验收须跑有预算 v10 的真实 reader 角色逐卷重组、默认关闭、错误角色、假分片、第一次回执后管理员撤权和读取中栅栏变化，以及 Node 正反向组卷与 Blob 不释放测试；v9 既有下载回归和签名转发测试必须不变。当前实现不做原生 Excel 公式重算或真实 30 天规模验收；Office 许可、真实固定预算审批与 v2 来源容量仍是正式交付的独立门槛。

整合验证：新旧下载及签名转发 Node 75 项、变更文件 ESLint、本地首次 `npm run build` 通过；隔离 PostgreSQL 真实 reader 原三项 `.runtime/ai-pg-7ca6e4df73da/tests.log`（215.198 秒）、补管理员撤权后的四项 `.runtime/ai-pg-d53ae87c80da/tests.log`（331.362 秒）和旧 v9 五项 `.runtime/ai-pg-c35005cfb105/tests.log`（514.994 秒）通过。首轮 v10 测试两项因隔离角色只预创建而未执行正式 `database_contract.provision`，缺已有的账号/文件/分片 SELECT 授权；测试夹具补正式精确授权后重测通过，并断言 reader 对 0057 证明表仍无 SELECT。生产代码未因该夹具失败扩大权限。新增客户端负例验证账号中途切换及最终发布/拥有方摘要改变均无 Blob；追加代码后的构建仍须复核。
