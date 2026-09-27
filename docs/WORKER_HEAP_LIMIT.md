# 本机 workerd 内存堆配置

2026-09-14 用户授权将约 1.4 GB 崩溃水位对应的本机 workerd JavaScript 堆限制提高到 3 GiB。此变更设置 V8 `--max-old-space-size=3072`（MiB），控制老生代空间，并非整个进程 RSS 或整机内存总上限；实际总堆及外部缓冲区可超过该数值，也不会预先占满 3 GiB。

## 实现与边界

- 保留 Wrangler 4.92.0、Miniflare 4.20260515.0 与 workerd 1.20260515.1，不升级整个依赖链。
- `tools/install-workerd-heap-patch.mjs` 在 `npm ci` 的 postinstall 阶段对 Miniflare 配置组装处做最小回移，使固定 `TERUISI_WORKERD_HEAP_MB=3072` 转为 `v8Flags: ["--max-old-space-size=3072"]`。参考上游 https://github.com/cloudflare/workers-sdk/pull/14702 。这里只接受固定值，不提供任意 V8 参数入口。
- 只接受原始包文件 SHA-256 `9584409d464f6c21d720b20da3c5dc6e5bdab0393f9cb19b36cb960beaf0958b`，适配后为 `2b2a89fb96a270e678b4aa87e65aa1282049b18d28a1e30ff7fe7f2736b648c7`。重复安装幂等，未知字节拒绝。以后升级依赖须明确移除此回移或重新验证。
- 不可变 supervisor 在每次启动/子进程重启时清除大小写变体的继承值，固定写入 3072，并复验适配后的文件摘要。未安装 postinstall 适配时拒绝启动，不静默退回默认堆。
- 参数随该 Wrangler 启动的主 workerd 及辅助 workerd 生效，不修改其他独立进程或系统级 `NODE_OPTIONS`。3 GiB 是各 V8 实例老生代的上限，不是多个进程共享的内存预算。
- 修改仅进入独立构建与新的不可变 release，不原地修改正在运行的 release 或依赖。生效需要受控切换 Worker/helper；Django、PostgreSQL 和 n8n 不需要重启。

## 验证

`tests/workerd-heap.test.ts` 验证原始包摘要、重复安装、篡改拒绝、继承变量覆盖，并启动无生产凭据/存储的隔离 Miniflare 实例，截获送入其 workerd 子进程的二进制配置，确认包含精确 3072 参数并成功执行合成 HTTP 请求。测试不附加生产调试器、不生成生产堆快照、不强制 GC 或执行生产任务。

workerd 当前 `node:v8.getHeapStatistics()` 返回占位零值，不能据此声称测得真实堆上限；采用证据以精确配置序列化、适配摘要、固定 supervisor 环境和运行版本绑定为准。未以大内存压力测试证明 3 GiB 全部可分配，也未证明长时内存增长已解决。

提高上限不修复内存保留原因，也不修复外层进程存活时未触发恢复的既有缺口。本次不顺带合并其他生命周期修改。生产状态和采用记录见对应 `docs/evidence/workerd-heap-3gb-*` 文件。
