# v3 来源页的签名工具审计收据（0032）

财务和日来源的内部采集现在只在中央工具成功返回、写入成功审计后，才可在同一 CAS 事务中追加事实块、不可变收据、来源检查点和父计数。收据一对一绑定工具审计 ID、请求与调用 ID、管理员身份、`business_collection` 入口、工具名、来源身份及修订、页序号和完整响应原始 UTF-8 SHA-256。数据库独立校验这些字段及对应事实块的原始字节；两个采集器从真实审计表回查唯一成功记录，不接受调用方传入的审计 DTO。

这证明可信 Worker/HMAC 内部边界内的工具响应与持久事实块相同，不等于上游财务、京东或 ERP 提供了独立数字签名。收据齐全后，`require_complete` 还会以目录查询重新执行拥有方的全页身份与检查点核验，拒绝将另一店铺的真实响应借给当前来源。0030/0031 直接写入的既有测试事实保持原样、无收据，不能升级成可信来源。当前 `sourceAuthorityVerified=false`、`persistentEvidenceVerified=false`；父 v3 始终 `collecting/manual`，封存、报告、Agent 和公开入口仍关闭。

`0032_business_source_tool_receipts` 只新增一张表及不可变/写围栏/绑定触发器，不改 0030/0031 原表事实或守卫字节。AI 表清单从 65 变为 66；历史 0025–0031 演练固定检查原 65 张表。独立 `0031→0032` 演练检查原表行、renderer 1–7 字节和原 ACL 不变、旧/新备份恢复、空收据可逆迁移及有收据时拒绝逆迁移。演练中的审计与页是合成数据，不代表真实拥有方取数成功。

隔离验证：`python tools/ai-postgres-rehearsal.py --tests-only --test-label ai_assistant.test_business_v3_tool_receipts --test-label ai_assistant.test_business_finance_collect_transport_v3 --test-label ai_assistant.test_business_daily_signed_v3 --port 55485`；升级恢复：`python tools/ai-postgres-rehearsal.py --business-v3-tool-receipts-upgrade --upgrade-only --port 55485`。同一端口必须串行使用。
