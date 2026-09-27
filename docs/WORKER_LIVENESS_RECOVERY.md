# 不可变 Worker 存活自愈

状态：2026-09-15 已合并到受控发布候选，待本机生产采用。

## 问题与修复

原不可变 supervisor 只在 Wrangler 外层进程退出时恢复。如果外层 Node/Wrangler 仍在，但内层 workerd 已卡死或无法提供 HTTP，supervisor 不会收到进程退出事件。

候选将既有 `monitorLocalWorkerLiveness` 接入正式不可变 supervisor：

- 只请求 `127.0.0.1:3000/_teruisi/local/health/live`，该端点不依赖 Django/D1；Django readiness 降级不会触发 Worker 重启。
- 首次观察延迟 10 秒，每 10 秒探测一次，连续 14 次失败才接管；最早接管晚于既有 120 秒有界导入请求。
- 接管时终止受管的 Wrangler/workerd 进程树，等待子进程和 3000 端口释放后才恢复；身份、manifest、D1 退役证据或端口发生偏离时失败关闭。
- 每次恢复前继续复验不可变 release 和 3072 MiB workerd 堆适配；10 分钟内超过 5 次重启即停止自愈，避免重启风暴。
- 正常停机也终止所属进程树，不让内层 workerd 脱离 supervisor 继续占用端口。

## 验证边界

组合测试覆盖单次瞬时失败不重启、连续失败替换实际子进程、进程自然退出恢复、停机不重启、退役证据篡改拒绝下一次恢复、堆参数不回退和维护竞态。候选测试使用独立端口与合成子进程，不通过破坏正式 workerd 做验收。

此修复只覆盖 Worker HTTP 存活自愈，不等于长期内存增长原因已解决，也不将 Django readiness 异常误判为 Worker 存活失败。
