# 吉客云 Windows DPAPI 登录配置

吉客云自动化使用当前 Windows 用户加密保存的凭据。已有有效登录态时直接复用；登录失效时，最多填写并提交一次吉客号、手机号或工号、密码。只有企业号与受控菜单同时验证通过，才算登录成功。

## 录入或更换密码

在 PowerShell 中进入已采用当前代码的项目目录，执行：

```powershell
Set-Location -LiteralPath 'D:\运营管理系统'
npm run jackyun:credential:setup
```

本机会出现“吉客云登录凭据 · Windows DPAPI”窗口。确认显示的吉客号，在窗口内填写手机号或工号和密码，点击“加密保存”。命令返回 `status=stored`、`ready=true` 表示加密保存和本机解密复验均成功。取消窗口不会覆盖已有凭据。账号密码只在这个本机窗口输入，不粘贴到聊天、工作流或命令行参数。

检查保存结果：

```powershell
npm run jackyun:credential:status
```

`ready=true` 表示当前 Windows 用户可以解密绑定的凭据；它不代表平台已接受密码。验证平台登录：

```powershell
npm run jackyun:authenticate
```

该命令只登录并检查企业号，不筛选数据、不创建导出、不导入，也不会修改 n8n 未完成运行。只有返回 `status=authenticated` 和 `tenantVerified=true` 才算平台验证成功。`authentication=windows_dpapi_credentials` 表示当次确实使用了 DPAPI 凭据，`existing_session` 表示复用了已有登录态。

## 绑定与保密边界

- 非敏感配置在 `config/jackyun-login.json`，包括吉客号、独立 Profile、调试端口及等待预算；当前绑定吉客号为 `771168`。
- 密文在 `%LOCALAPPDATA%\TERUISI\JackyunCredentials`。文件名是绑定摘要，文件不保存明文账号或密码；DPAPI 使用 `CurrentUser`，加密附加信息和内容同时绑定吉客号与规范化 Profile 路径。目录关闭权限继承，仅允许当前用户、SYSTEM 和管理员；重解析路径、所有者/ACL 异常、内容损坏或绑定不符均拒绝读取。
- n8n 继续只调用 helper，不持有账号、密码、Cookie、环境变量或解密接口。固定 DPAPI 程序随不可变 helper 编译，未放宽 release builder、verifier 或 Django 发布门禁。
- 更换 Windows 用户、吉客号或 Profile 后，需重新配置相应绑定。不得复制别人的凭据文件或将旧密文伪装为新账号。

## 失败处理

后台凭据读取及浏览器进程核验通过显式 UTF-8 标准输入输出流通信，不依赖控制台句柄。不要恢复 `[Console]::InputEncoding/OutputEncoding` 设置：无控制台进程可能在读取凭据前报“句柄无效”，终端中的 `ready=true` 不能排除此类后台兼容性问题。Node 必须流式解码 UTF-8，防止中文或其他多字节字符在分块边界损坏。初始化、绑定和实际解密分别使用 `initialize`、`binding`、`read` 诊断阶段，原始凭据和 PowerShell 错误正文不透传。

2026-09-26 已受控采用：将 `binding` 细分为输入读取、字段、路径、当前用户、路径完整性和文件查找阶段；缺少凭据文件和重解析路径单独标为不可重试。仅在尚未向登录表单填值时，针对 `binding_input`、`binding_identity`、`binding_local_path`、`binding_vault_lookup` 及旧 `binding` 做最多 3 次本地读取尝试，间隔 0.5/1 秒。解密/ACL、密码被平台拒绝、验证码、登录提交结果不明均不属于此重试。后台无控制台、标准流条件下的当前凭据 `status` 连续 10 次正常；历史午夜偶发故障尚未复现，细分诊断用于下次定位底层步骤，不能宣称已查明唯一根因。源码 `073d9619`，Worker/helper release `20260925T213040Z-e45b1332207bc4cf`；原 n8n 完整执行 4397 五表下载与导入成功，精确批次独立回查通过，销售截止 9 月 25 日。详见 [生产证据](evidence/jackyun-dpapi-retry-production-20260926.json)。

2026-09-17 修复覆盖无控制台 DPAPI 状态/读取、中文及 emoji、浏览器进程归属、复制绑定、密文损坏和暴露 ACL。已按明确维护授权采用源码 `2db37150`，正式 Worker/helper 为 `20260916T194114Z-df8da4ea6ac81cf6`。原工作流新 execution `2667` 成功完成五表，独立批次与日期回查通过；详见 [生产采用记录](evidence/jackyun-headless-dpapi-production-20260917.json)。

当天执行 `2621` 在登录初始化处停止，原计划没有导出意图，控制状态、事件、下载及演练目录均不存在。`tools/jackyun-preflight-recovery.ts plan 2621` 支持只读核验该精确失败的原始 execution 和计划摘要；`apply` 仍需复验并以 create-only 回执闭合，保留原计划、active 和失败历史。新的完整 n8n execution 才能推进 active 并按实际采集日执行。该例外不适用于其他 DPAPI 失败、存在任何业务产物或提交结果不明的运行，不放宽共享错误工作流的凭据类停止规则。

页面初始加载最多等待 30 秒，提交后最多等待 45 秒。自动化先验证浏览器进程属于当前 Windows 用户，且可执行文件、Profile 和端口匹配，再检查精确站点 `https://web.jackyun.com`。真实登录表单的控件为 `#selAccount`、`#txtUserName`、`#txtPwd` 和 `#btnLogin`；隐藏的找回密码表单不会被当成登录表单。字段句柄绑定已观察的文档，页面跳转时不得把凭据自动重试填入新页面。

验证码、短信、滑块、安全验证、密码拒绝、企业号错误、表单不唯一或提交结果不确定都会停止。不要反复点击登录或重复启动业务流程；有人工验证时使用 `npm run jackyun:login` 打开专用浏览器处理。登录维护前必须等待共享 helper 空闲，并持有相同的吉客云全局运行锁。

n8n execution `841` 在旧登录探测阶段停止的记录继续保留。配置 DPAPI 不会关闭原计划、删除 active 清单或证明五表导出导入已经完成；后续业务恢复仍需遵守五表流程的原运行证据规则。

## 验证记录

2026-09-06，操作者在本机窗口完成录入，凭据状态检查返回 `ready=true`。隔离工作树中的纯登录验证返回 `authenticated / windows_dpapi_credentials / tenantVerified=true`，证明当前用户的 DPAPI 凭据已被吉客云接受；本次未触发导出或业务导入。n8n 运行中的 helper 采用情况以受控 release 回读为准，不能用源码验证代替已发布证据。

相关 36 项登录/导出状态机检查通过，包含本地页面夹具与 Windows DPAPI 合成凭据往返、复制绑定、密文损坏和权限继承拒绝测试。最终完整回归 1,871 项通过、20 项跳过、0 失败；lint 为 0 错误、9 项既有警告，生产构建、20 项渲染检查及 Django 边界检查通过。全库 TypeScript 当前与同一 main 基线均为 142 项既有诊断，按文件、错误码和消息比较无增减，不宣称全库类型检查通过。

## 2026-09-06 本机采用记录

登录实现 `b8ab617f6f500d796980b6f1db11f84354068e35` 已快进合入 main。停止旧 Worker/helper 后，由固定干净集成工作树构建并采用 release `20260906T102947Z-22414d6a30a05dbf`，manifest SHA 为 `cfb97aba9a5f7ffd4bc4be8cc89d58dea339ca4137cb18322438de7d2bcc3172`。精确 plan SHA 为 `65cdf70ddcd57c51b55ff7a1bd71590b1c18902016c9e6ccbd299341de38bf35`，successor SHA 为 `c1a023daa3c2f462df303bee44d3561014ca95f8b79f9080b91c79293cd0d073`。

候选 helper 的编译回执包含三个 DPAPI 模块及浏览器 controller，四个输入摘要逐项与已合入源码一致；登录配置与发布快照一致，受保护 builder 和 verifier 未改变。采用后官方组合状态为 `Running / Ready / exact_release`，后端 readiness 为 `django-postgresql / ready`，helper 为 `ready` 且空闲，登录启动项回读绑定新 release。正式项目目录中的凭据状态检查再次返回 `ready=true`。n8n 使用的本机 helper 已包含新登录实现；本次没有重新运行五表导出导入，execution `841` 的失败证据与未闭合清单继续保留。

发布前一致性备份 `daily-20260906T101758Z-a1cc5328435f` 已完成并复验，manifest SHA 为 `5434b7ba95ea2d3bf96fb8d7cf4c2c10dd1ba719e655550b1e82498e6a3d6c9b`。独立恢复演练 `fbdfc0c67106` 使用端口 `55642`，原始与恢复内容 SHA 均为 `aae82efcd2f3059373a6747bfc969051a400d4e9349a1b36e7c5eb208abea52f`；演练未触及生产数据库、未改变数据库服务状态，临时数据已由受控 operator 清理。本次没有数据库结构或 Django 服务变更。

受控 Start 已启动健康服务，但外层 PowerShell 等待进程未返回。通过 PID、创建时间、精确命令、无直接子进程及官方新 release 状态核验后，只结束该等待进程；不能将其退出结果记为正常 Start 成功。服务未因此重启，随后组合状态与启动项核验通过。原始命令结果、helper 编译摘要、发布、健康、启动项与等待进程处置记录保存在隔离工作树 `D:\codex-worktrees\jackyun-dpapi-login-20260906\outputs\validation`。
