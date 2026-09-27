# 受保护归档流式格式候选

`tools/protected_ai_archive_v2_stream.py` 是独立、未接线的 `v2-stream-v1` 格式。它与此前 64 MiB 整包 v2 格式故意不兼容；以 1 MiB AES-256-GCM 块加密，固定最高 1 TiB 明文上限，并以规范头、每块顺序/长度和认证结束帧绑定上下文、总字节数与摘要。密钥提供者默认抛 `NotConfigured`，没有正式密钥或备份操作入口。

封存仅把密文写入同目录唯一 `.incomplete-*` 临时文件；来源进程失败、超时或任意校验失败时不发布成品。打开归档先完整认证同一文件句柄，恢复前复验文件身份，再次逐块认证后只向含 `--single-transaction --exit-on-error` 的有界接收进程写入。第二遍若失败，必须**先终止并等待接收进程，再关闭 stdin**，避免部分明文遇正常 EOF 被误认为可提交。通用 sink 仅限测试并在失败时清空；正式恢复接口不向非事务性 sink 输出。

合成纯测试 11 项通过：错密钥、头/结束帧损坏、截断、乱序、旧格式误用、来源进程失败、同句柄且复原文件大小/mtime 的并发篡改均拒。TOCTOU 负例重复 30 次没有让模拟接收进程写成功摘要；80 MiB 生成流通过有界内存检查。

2026-09-26，唯一一轮真实 PostgreSQL 双集群流式演练在独立 managed worktree 的已提交 `1bd52362` 基线运行，只复制了本轮测试专用脚本；该隔离源不含并行开发中的 `finance.0005`。源端口 55898 完成 0073 前驱升级与恢复后，`pg_dump` 的 custom stdout 直接封存为 `v2-stream-v1` 密文，再完整认证并通过 `--single-transaction --exit-on-error` 管道送入第二新集群的 `pg_restore`。13 个受保护角色、9 张受保护表、1 条随机合成私钥、空 0073 证明表、原 owner/ACL 与故意漂移拒绝均通过。归档为 3 个 1 MiB 认证块；错密钥、截断、密文篡改和来源进程失败均受控拒绝。目标 `protected-cross-cluster` 目录的明文 dump 文件数为 0，只有加密归档、证据和 PostgreSQL 日志；完整前驱演练目录另含插入合成私钥**之前**生成的历史升级 dump，不能混称整个归档目录都没有明文文件。

runner 成功退出后，源和目标数据目录各自 `pg_ctl status` 均为 `no server running`，源端口 55898 与目标端口 55440 没有监听。完整隔离运行目录已受控移至 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-b6aeadfd55de-stream-v1`，D 盘源目录不存在。关键文件回读 SHA-256：`protected-cross-cluster/evidence.json` 为 `974663bb9884c2a24d6d4c8bf8da22879f4987ef6dedef0b5586da2c0ae26c0c`；`protected-cross-cluster/synthetic-source.dump.v2s1.aead` 为 `b491deebc8bcf0bd49cad68d8bddddf8cd21efdea44ba4138c4173cf08437633`。

这只证明合成双集群的流式格式及原 owner/ACL 在本机可恢复。随机测试密钥未持久化，正式特权备份身份、可轮换且可异机恢复的密钥托管、归档 ACL、版本化正式 manifest 与含 `finance.0005` 的新组合仍未验收。证据中 `formalBackupPathVerified=false`、`productionWrites=false`；正式备份、恢复和生产发布继续失败关闭。
