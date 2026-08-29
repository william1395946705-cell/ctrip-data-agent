# Project Handoff

更新时间：2026-08-29

## 当前分支

当前任务分支为 `codex/verify-real-endpoints`，从 `main` 基线 `d154eb84ed9debb4aa16871bbbb0c34de3cf67cd` 创建。本轮只在授权登录测试酒店的独立 Chrome/CDP 会话中做普通页无导航网络观测，没有自动进入经营报告、金字塔或违约看板。

Git 中保存的是源码、离线测试、占位接口地图和项目文档。授权会话的抓包、结果、Chrome 登录态、Profile、账号文件、日志和业务下载文件不属于仓库基线。

## 当前已完成能力

- Python 被动 Network Inspector 和 response 捕获接入。
- 请求/响应结构脱敏、敏感 header 过滤和安全错误信息处理。
- 发现候选与人工批准的只读内存模板隔离。
- 当前页同源 request replay 及 URL 不变检查。
- 经营报告字段归一化、多接口合并和四象限计算。
- 金字塔 7 天/30 天回退及加载、失败、登录失效、无数据状态区分框架。
- 违约状态归一化。
- 旧采集器结果适配和逐字段比较框架。
- Test A-D 的 CLI/runner 框架。
- `observe` 命令：在已有授权 eBooking 页面上被动监听，不导航、不刷新、不抢焦点、不点击和不输入。
- Manifest V3 扩展的 Content Script、Connector、Service Worker、本地缓存、冷却配置和调试页。
- Python 与 Node 离线测试套件。

## 2026-08-29 真实普通页观测

- 授权会话使用独立 Chrome Profile，CDP 仅监听 `127.0.0.1`，测试 Chrome 启动时已配置加载 MV3 目录。扩展的内置地图仍为 `unverified/disabled`，因此不发送业务请求；21 条观测记录来自 Python Inspector。本轮未将 Profile 或会话值读入项目。
- 在 eBooking 首页稳定停留 30 秒：页面 URL 未变，登录态和酒店身份存在，捕获 0 条 XHR/fetch。
- 在普通订单页开启 180 秒被动窗口，由用户手工刷新：页面 URL 前后一致，登录态和酒店身份保持，捕获 21 条同源请求。
- 21 条请求经修正后全部分类为 `unknown`；内容为订单列表/详情、首页消息/提醒、过滤项和微前端页面壳，未覆盖本 POC 三个目标模块。
- 已排除三个误报：`/ebkorderv3` 是订单微前端 HTML 壳，`getMultiNotifyMessage` 是通知消息，`getOrderDetail` 是订单详情；它们都不是金字塔或违约接口。
- 误报根因是旧匹配器把响应内容中的泛化业务词当成接口语义。现在只使用明确的人工模块提示与请求/触发页路由分类。
- URL、payload 和 API map 写入前增加动态会话/追踪标识脱敏，同时保留 `hotelId`/`orderId` 这类必要业务字段。

## 尚未完成或尚未证明

- 未生成经过审核的真实携程业务接口执行地图。
- 经营报告、金字塔和违约三个真实接口仍为 `NOT VERIFIED`，均未通过授权账号的完整 Test A-D。
- 尚未证明三个模块能脱离各自业务页面、从任意 eBooking 页面调用。
- 尚未完成同一酒店旧采集器与 Silent Collector 的全字段真实对照。
- 当前扩展地图为 `unverified/disabled`，不会自动发送业务接口请求。
- 因未发现任何真实目标端点，本轮没有把非目标 POST 请求标为只读，也没有进行 replay。
- 因没有目标接口返回值，日期、指标、条数、字段含义和分页完整性均无法与页面可见数据对照。

## 安全状态

- `artifacts/` 下的本机会话捕获和报告由 `.gitignore` 排除。
- Python 缓存、Node 依赖、构建产物和系统文件由 `.gitignore` 排除。
- Cookie、Token、授权状态、Chrome Profile/用户数据目录、抓包文件、日志和下载的 XLS/XLSX 由 `.gitignore` 规则覆盖。
- 含本机绝对路径的 `docs/legacy_collector_source_baseline.json` 作为本地审计元数据保留，但不进入 Git。
- 代码内存在用于验证脱敏器的伪造泄漏字符串测试样本；它们不是真实凭证。

## 基线测试

2026-08-29 基线提交前已运行：

```sh
PYTHONPATH=python python3 -m unittest discover -v
python3 -m compileall -q python
cd extension && npm test
```

- Python `unittest`：19/19 通过。
- Python `compileall`：通过。
- Extension Node tests：17/17 通过。

这些结果只证明离线代码基线通过，不代表真实携程接口或 Silent Test A-D 已验证。

2026-08-29 本分支已运行同样的三组命令：Python `unittest` 25/25 通过，Python `compileall` 通过，Extension Node tests 17/17 通过。测试通过仅证明被动监听、脱敏和 fail-closed 行为，不会把三类真实接口改写为已验证。

## 下一步建议

普通首页稳定停留和普通订单页刷新均没有产生目标报表接口，因此仅靠“等待员工普通使用页面”的纯被动监听不足以完成整套采集。下一步需要单独授权一次性发现会话：由测试人员手工进入三个目标页面，仅捕获其自然只读请求；这是开发期接口发现，不是对正式采集用户的切页要求。捕获后再于普通页做精确只读 replay 和旧/新全字段对照；如果仍依赖特定页面初始化，就应判定该模块当前无法满足正式无感目标，不用自动跳转替代。
