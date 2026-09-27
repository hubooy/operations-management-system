# 推广专项试用版 renderer9 文件交付（隔离候选）

本路径仅对已完成人工批准、五个 Agent 任务和六节点工作流的推广专项开放。创建请求须显式指定 `deliveryMode: "promotionTrialVolumes"`、`draft: false` 和当前账号的 `expectedPrincipalKey`。既有 `deliveryMode: "volumes"` 继续使用 renderer7，旧报告及旧文件不迁移或重写。

后台使用现有单消费者文件任务、尝试隔离、1 GiB 单任务/2 GiB 单账号/8 GiB 全局配额、每块 512 KiB、最多五次重建与原有暂停/取消语义。renderer9 从已批准内容与封存来源生成 HTML、XLSX 和完整 JSON 清单，逐块入库并持久保存紧凑清单；失败或不确定结果停在 `paused`，不会发布部分文件。完整重读所有持久字节、清单、表行、来源、人审和五 Agent 账本后，先保存 `renderer_unpublished` 暂存态，再以短事务和版本 CAS 发布 `ready`。每次下载仍复核账号、发布栅栏、封存证据、清单与本块长度及 SHA-256。

发布核验将当前来源表的 key、标题、行数、列数、完整逐行 SHA-256 与完整 JSON 清单逐项对齐，并复算来源描述与分卷计划摘要。试用版证明已升为 `business-promotion-trial-file-proof-v2`：独立 `tableSchemaDigest` 绑定所有表的标题、说明及每列 key/标签/类型/合计/比率引用，写入完整清单并随文件字节保存。发布前从当前表声明重算，标题、说明或列名漂移均拒绝；旧 renderer1—7 的清单形状与字节保持原样。

试用卷中的内嵌候选元数据保留其原有 `deliveryAuthorized: false`，以免把临时生成物本身当成发布许可；实际是否可下载只由持久文件任务的 `ready` 状态和签名下载入口判定。试用版不交付可编辑预算工作表，完整清单明确 `budgetDelivered: false`。这项交付不代表其他来源、真实业务规模、原生 Office 或生产部署已经验收。

隔离验收需覆盖：签名创建、真实五 Agent 合成批准、后台发布、每个 HTML/XLSX/JSON 卷逐块重组、renderer7 旧路由回归；缺块、伪造块、错误账号、人审/来源变化和未经批准一律拒绝。数据库 0046 迁移是本路径先决条件，生产采用另需独立授权与备份恢复验收。
