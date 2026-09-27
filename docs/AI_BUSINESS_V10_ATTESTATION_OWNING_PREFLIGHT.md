# renderer 10 暂存文件的拥有方预检候选

`business_promotion_budget_v10_preflight.prepare` 默认关闭。它只接受当前有效的无范围管理员和 `paused/renderer_unpublished/staged_unpublished` 的第 10 版正式文件任务；不创建文件、不调用 0057 写函数、不激活独立角色、不改 ready，也没有公开路由。

预检先通过既有 `business_promotion_budget_v10_stage.binding` 重核批准的五角色内容、报告/工作流封存根及固定预算存在性，再调用 `_verify_staged` 完整核验全部持久分块与当前拥有方重新生成的文件。该验证对持久 HTML/JSON 核原字节，对 XLSX 持久容器核其原字节摘要，并逐个核重新生成 ZIP 条目的名称与解压内容；它**不宣称**两次 XLSX ZIP 容器字节完全相同。合同限制完整 JSON 16 MiB、单文件 256 MiB、全交付 1 GiB；本预检另加 600 秒硬期限，超限整次拒绝。

从已核的同一 attempt 紧凑清单和持久完整 JSON 中构造 0057 的全部 16 个精确字段：run/attempt/binding、紧凑及完整 JSON 字节摘要、完整清单摘要、文件描述、批准内容与人审摘要、预算存在性/计划/证明摘要。`owningVerificationDigest` 绑定这次完整字节/语义复核与预算根；`publicationFenceDigest` 绑定文件版本、报告/工作流原文摘要和管理员版本。两者只是受保护进程生成的**断言**，0057 SQL 不能独立复算，更不能据此发布。返回前重新读取文件任务、当前管理员、批准内容、固定预算根和 binding；任何变化都会拒绝。

纯合同测试覆盖有/无预算正文、字段/摘要、版本与根变化、非规范或伪造内容；隔离 PostgreSQL 目标测试从真实暂存 HTML/XLSX/JSON 生成候选，验证默认关闭、任务状态变化拦截、旧分块不可改与 0057 表仍为空。正式发布还需要独立 0058 数据库门禁及后续真实业务、模型、人审和原生 Excel 验收；本预检并不授予这些权限。
