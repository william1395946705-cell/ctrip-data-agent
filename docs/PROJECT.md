# Ctrip Data Agent Project

## 项目定位

本仓库是“携程 eBooking 数据采集助手”的 Silent Collector 技术验证工程。核心问题是：门店员工只正常打开或刷新任意 `https://ebooking.ctrip.com/*` 页面时，程序能否利用当前合法登录会话，在不切页、不抢焦点、不控制鼠标键盘的前提下，采集现有程序所需的完整携程指标。

本项目仅用于本人或明确授权的测试酒店账号，不处理验证码，不绕过登录、安全验证、设备限制或平台风控。

## 当前真实状态

2026-09-05 增补：普通 Chrome MV3 手动测试包已经完成测试人员的首页、订单、房态、价格页面试用，价格连续六轮包含手工刷新恢复。包仅使用 controlled_test 人工触发，尚不生产启用。详细证据来源、离线测试与剩余酒店身份/日期风险以最新 HANDOFF 为准。

六个已发现查询接口已经在授权酒店完成普通页 Test B/C/D：eBooking 首页、普通订单页、订单页手工刷新后三种状态均可同页调用，页面 URL、焦点、登录态和酒店身份保持不变。端点已完成查询语义的人工只读审核，但本轮不把固定的“未观察到写操作”当成服务端无变更的运行时证明。

根目录接口地图仍是 `map_kind: discovery`、`map_status: discovered` 且全部模块 `enabled:false`，不会被扩展自动执行。四个经营报告接口、近 7 天 ROAS 与无违约查询均保留 `discovered`，另记录 B/C/D Replay PASS。旧采集器逐字段对照、30 天 ROAS 回退、无投流/异常状态和“有违约”正样本仍未验证，因此项目尚未进入生产启用。

## 目标数据范围

### 经营报告

- 经营提醒
- 昨日离店间夜竞争圈排名
- 点评分、PSI 分
- 本店与竞争圈列表页曝光
- 本店与竞争圈曝光转化率
- 本店与竞争圈下单转化率
- 根据曝光和下单转化率计算四象限分类

### 金字塔

- 近 7 天 ROAS
- 7 天明确为 0 或暂无数据时检查近 30 天 ROAS
- 严格区分真实未投流、加载中、请求失败和登录失效

### 商家违规/违约看板

- 有违约
- 无违约

## 已存在的代码组成

- `python/ctrip_silent_poc/`：被动 Network Inspector、脱敏、内存请求候选与人工批准、同页请求重放、统一模型、旧/新结果比较、Test A-D 框架和 CLI。
- `extension/`：Manifest V3 Content Script、MAIN world Connector、Service Worker、本地缓存、冷却控制和调试页。
- `ctrip_api_map.json`：逐端点记录发现和 Test B/C/D 证据的 discovery 地图；模块仍全部禁用。
- `tests/` 与 `extension/tests/`：Python 和 Node 离线测试。
- `docs/POC_REPORT.md`：当前 POC 阶段结论及真实验收门槛。

旧 Playwright/CDP 采集器、Chrome Profile 和生产运行数据不属于本仓库内容；它们只作为授权本机上的外部对照组，不能被本 POC 修改或上传。

## 已实现但仅离线验证的能力

- 监听现有 Playwright `BrowserContext` 的 response 事件并生成脱敏捕获记录。
- 将发现候选与可执行只读模板分离；进程结束后内存候选丢失。
- 对请求 URL、方法、payload schema、安全 headers、response schema/body、触发页、时间和模块做结构化记录与脱敏。
- 同页、浏览器托管同源会话的请求框架，并检查执行前后页面 URL。
- 仅允许精确 eBooking HTTPS 同源 GET/POST；POST 需要只读说明，禁止自动跟随重定向。
- 用实时 flow 返回的酒店 ID 集合、捕获集摘要和当前酒店单向摘要做本地 fail-closed 绑定，不持久化真实酒店身份。
- 经营报告多源合并和四象限计算。
- 金字塔 7 天/30 天回退状态模型，以及违约状态归一化。
- 旧采集器与 Silent Collector 统一结果的逐字段比较框架。
- 扩展页面识别、登录/酒店身份状态、页面稳定判断、本地缓存、调试页和可配置冷却时间。

上述框架中，六个目标接口的普通页同源 Replay 已有真实授权会话证据；未覆盖的 30 天回退和正/异常样本不能由此推断。

## 明确不做

- 不通过自动导航到目标业务页面冒充 Silent 成功。
- 不读取 Chrome Cookie 数据库、Profile、密码文件或 localStorage。
- 不保存认证 header、Cookie、Token、Session、CSRF 或账号密码。
- 不高频轮询，不干扰订单、价格、库存等员工操作。
- 不在本阶段接入云服务器。

## 成功判定

每个业务模块必须同时满足：

1. Test A 的自然页面请求与旧采集器结果一致。
2. Test B 在首页、Test C 在普通业务页、Test D 在普通页刷新后都能同页请求成功。
3. 全程不改变活动页 URL，不导航、不点击、不输入、不抢焦点。
4. 全部必需字段逐字段一致，错误状态不被误判为无数据或未投流。
5. 接口和 payload 经过只读审核，脱敏检查未发现认证材料。

只有达到这些条件，模块才能从 `unverified` 改为 `verified` 并进入可执行地图。

## 本地验证

```sh
PYTHONPATH=python python3 -m unittest discover -v
python3 -m compileall -q python
cd extension && npm test
```
