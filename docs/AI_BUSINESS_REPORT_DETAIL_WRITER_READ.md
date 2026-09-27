# 报告详情精确 writer 只读路径

既有推广报告的详情会重放完整 provider 与工具账本以复核内容。普通 AI reader 无权读这些包含模型/工具原文的表，原详情路径因此在模型绑定复核前失败。给 reader 广泛 SELECT 会扩大数据暴露范围。

本候选只将精确 `GET /api/ai/reports/<id>` 路由到现有签名 AI writer。Django 对原始路径做精确匹配，尾斜杠和额外段不能搭上 writer 特例；列表、预算、文件、词货子路径与 POST 均保留原有路由。此 GET 保持只读 `current_principal` 和原 `reports.detail` 拥有方、工作流、版本及内容复核，不进入 `write()`，也不创建 `AiWriteReceipt`。前端保留原筛查大包字节门禁，不放宽推广报告限制。

Node 精确路由 19 项与隔离 PostgreSQL 3 项通过：完成态五角色推广报告返回五节正文；跨账号拒绝，晚期版本变化只返回 `contentError` 而无旧正文；签名传输与 `X-AI-Revision` 保留，模型/工具调用及数据库 DML 为零。测试独立确认 AI reader 仍没有 provider 派发表整表 SELECT。其他历史报告 profile 尚未逐一做真实 writer 角色正例；正式服务未采用本分支，发布前仍须按维护、备份恢复及业务回归门禁验收。
