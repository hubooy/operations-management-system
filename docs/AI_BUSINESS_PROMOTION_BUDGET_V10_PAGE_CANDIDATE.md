# 预算 renderer 10 页面分卷入口候选

页面仅在构建时将 `NEXT_PUBLIC_AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED` **精确设为** `true` 后，对 ready、非草稿的 renderer 10 显示“查看预算候选分卷文件”。默认未配置或其他值均关闭。后端 `AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED` 也必须独立精确为 `True` 才会返回分片；本切片不设置任一开关，不启用生产凭据、创建、控制或发布流程。

打开目录会重新读取完整任务根，核版本、绑定、尝试与 v10 ready 回执字段；当前列表状态改变时清除缓存。目录显示完整交付清单和每个 HTML/XLSX 卷的 SHA-256。单文件下载调用已有 `downloadBudgetV10Volume`，前后核账号、回执和完整文件摘要；页面显示百分比与取消按钮。取消只停止本次读取，不更改已发布任务。页面明确写为内部验收候选，Office 原生复算和真实规模尚待核验。renderer 9 的入口、目录、下载函数及字节输出分支保持原样。

本地默认关闭构建、组件正反用例和旧文件下载用例是代码检查；它们不代替主任务的真实角色 PostgreSQL、签名转发、v9 页面视觉回归、有效 Excel 许可下的原生打开/复算，也不构成生产启用批准。

整合分支新增页面与旧版下载 Node 79 项、目标 ESLint 及默认关闭本地构建通过；服务端真实 reader 含撤权 PostgreSQL 四项 `.runtime/ai-pg-d53ae87c80da/tests.log`、旧 v9 五项 `.runtime/ai-pg-c35005cfb105/tests.log` 通过。未开启任一前后端开关，未做浏览器视觉或 Office 原生验收。
