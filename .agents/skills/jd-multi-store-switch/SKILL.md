---
name: jd-multi-store-switch
description: 管理京东多店铺的独立 Chromium 会话、Windows DPAPI 登录守护和串行数据导出。用户要求配置店铺注册表或加密凭据、首次登录、按店铺顺序运行 SKU/SPU 导出，或排查登录失效与跨店串号时使用。
---

# 京东多店铺切换

## 核心规则

- 只读取 `config/jd-store-accounts.json` 中的非敏感配置；账号密码不得进入 n8n、注册表、环境变量、命令参数、日志、审计、Markdown、Skill 或 Git。
- 仅 `loginMode=windows_dpapi_credentials` 的受控店铺可使用当前 Windows 用户绑定的 DPAPI 密文。只通过 `tools/jd-secure-credential.ts` 在内存中读取，不输出、不回读表单字段、不跨店重试，提交后清空内存字段。
- 每个店铺使用独立 Chromium profile、CDP 端口和下载目录；可以共享 `userDataDir`，但禁止共享 profile 或下载目录，并共用全局 Chromium 所有权锁严格串行。
- 登录守护必须把页面分为 `authenticated/login/pending`：识别到受控业务 UI 才算已登录；明确登录页才进入登录处理；初始空白或加载中状态必须有界等待延迟重定向，超时失败关闭。不得仅因 URL 暂时位于业务域或离开登录页就判定成功。
- 登录失效时只允许在唯一账号密码表单、无验证码/安全验证且当前 storeKey 与执行所有权匹配时提交一次 DPAPI 凭据。验证码、短信、滑块、安全验证、凭据异常、表单歧义或登录后身份不符必须停止并要求人工处理。
- 商智页面在读取/恢复任务清单、建立下载中心基线、确认创建任务和点击下载前，都必须从页头唯一店铺链接精确核验 `shopName + shopId`；任一不一致立即失败关闭并打开当前注册店铺的可见 Chrome，不得提交、接管或下载另一店任务。
- 一次只运行一个店铺。当前店铺完成后才切换下一店铺；失败立即停止并保留审计现场。
- 商品主数据日常使用普通有头 Chromium 后台隐藏运行，并从空白页进入唯一受控商品页，导航前监听首屏商品查询，禁止重复商品页和首屏后的补点；商智 SKU/SPU 分天独立运行仍可使用 `headless=new`，同店串行时复用已启动的后台有头实例。普通恢复模式只在需要人工处理时打开当前店铺可见 Chromium；严格静默的 `--no-visible-recovery` 只失败关闭并保留证据，不得弹窗。两种模式均不自动重放业务点击。`601` 不按无头、CDP 或点击方式单因归因，也不通过反检测手段绕过。

## 首次准备

1. 检查注册表键、shopId、端口、解析后的 profileDir 和 downloadDir 全部唯一，平台字段为京东，并确认所有路径可写。
2. 对启用 DPAPI 模式的每个店铺，要求操作员运行 `npm run jd:credential:setup -- -StoreKey <店铺键>` 交互录入；不得代替用户读取、记录或转述账号密码。
3. 需要首次登录或安全验证时，只打开对应 Profile 让操作员处理；确认页面 `shopName + shopId` 与注册表一致，不一致时停止。

人工维护时显式增加 `--interactive-login`；它可能继续当前业务步骤，不是纯查看器。定时任务不传该参数，且它不能与 `--no-visible-recovery` 混用。详细操作先读 `docs/京东多店铺账号切换与串行执行手册.md`。

## 执行命令

单店 SKU：

```powershell
npm run jackyun:ware-export -- --store-key jd-yiyong-director
```

单店商智 SKU/SPU：

```powershell
npm run jdsz:product-detail-export -- --store-key jd-chudian-weizhang --dimension SKU --start-date 2026-07-01 --end-date 2026-07-19
```

全部启用店铺串行运行：

```powershell
npm run jd:all-stores -- --mode all --start-date 2026-07-01 --end-date 2026-07-19
```

单独日数据使用 `--mode sku-daily` 或 `--mode spu-daily`；失败后用 `--store-key <注册表键>` 只重跑失败店铺。执行器以单个 run audit 持久化 planned/running/completed/failed 和错误 stderr 摘要；不要使用 `Promise.all` 并行启动店铺任务。

## 故障处理

- 登录状态在有界等待后仍未确认、页头店铺名或 shopId 不匹配、任务数量歧义、下载未验证或导入失败：立即停止当前流程。
- 商智提交清单必须同时绑定 `storeKey + shopName + shopId + dimension + 日期范围`。历史清单缺少店铺绑定时只允许在当前页头身份已精确匹配后兼容恢复；已确认在错误店铺下生成的清单只能原样归档，不能拿错误店铺任务补写指纹。
- 不要通过重复点击登录/导出、刷新后盲选最新任务、绕过安全验证或换用另一店凭据来“恢复”。先检查当前 storeKey 的 Profile、凭据就绪状态、端口、审计 JSON 和下载目录。
- 修复后只重跑失败店铺；确认结果后再继续后续店铺。
- 若需要审查刚运行遇到的坑、修复程序或验证跨店数据隔离，改用 `jd-multi-store-pipeline-review` Skill，并先读 `docs/京东多店铺统一下载与导入-审查修复与稳定性手册.md`。

## 相关文件

- 注册表：`config/jd-store-accounts.json`
- 注册表加载：`lib/jd/store-registry.ts`
- 串行执行器：`tools/jd-multi-store-runner.ts`
- 店铺 SKU：`tools/jackyun-ware-export.ts`
- 商智日明细：`tools/jdsz-product-detail-export.ts`
