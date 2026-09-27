# AI 模型凭据读取与 reader 列级权限

原 AI reader 对 `ai_models` 有整表 SELECT，其中包含 `api_key_encrypted`。内部 `model-runtime` 通过 reader 返回模型运行配置；视觉价格识别调用它后由 Next 服务解密。加密密钥未注入 reader，但读取密文仍超出普通目录读取需要。

本候选把精确签名的内部 `model-runtime`、`model-list` 和管理员模型设置 GET 放到 AI writer。它们依旧是读取请求，Django 直接返回，不创建 `AiWriteReceipt`，现有响应字段与视觉识别消费者保持原义。普通 reader 的聊天可用模型目录只选必要非密钥列；市场费用候选也只查询所需字段。既有推广报告读取会复核模型配置，reader 仅投影精确 19 个非密钥列，writer/provider 路径继续取得完整模型行。

`ProvisionRoles` 候选移除 reader 对 `ai_models` 的整表 SELECT，每次先撤销旧授权，再授系统数据集所需的 17 个非敏感列与传输指纹所需的 2 列。reader 健康检查核有效权限恰好为 19 列，拒绝旧整表权限、加密密钥列、后缀或诊断结果额外授权。writer 既有模型运行路径及表权限保持不变。

隔离 PostgreSQL 真实角色 9 项组合测试通过：重复 ProvisionRoles/旧授权清理、reader `SELECT *` 与密钥列拒绝、writer 三种读取直返且无写回执、reader 聊天列表/市场预检/模型指纹可用，以及既有词货报告的模型绑定在 19 列下可复核。前端模型路由与市场识别相关 76 项通过。随后又将精确报告详情 GET 独立改走现有 writer 只读路径，完整五角色词货正文在隔离 PostgreSQL 3 项中通过，reader 的 provider 账本权限未放宽，见 `docs/AI_BUSINESS_REPORT_DETAIL_WRITER_READ.md`。这些源码尚未用于正式角色或部署；采用前仍须通过备份独立恢复、进程/版本/ACL 回读及真实业务验收。
