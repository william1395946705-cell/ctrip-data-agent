# Ctrip Silent Collector POC 阶段报告

报告日期：2026-09-03
阶段：同酒店同数据日逐字段对照

## 核心结论

2026-09-03 使用测试人员提供的同酒店、同日三张真实页面截图作为人工对照，在普通 eBooking 首页执行两次 Silent Replay。经营数据日为 2026-09-02，7 天 ROAS 窗口为 2026-08-27 至 2026-09-02。

结果为 **13/13 完全一致，TIME_DRIFT 0，真正不一致 0**。两次 Silent 快照本身也完全一致，页面 URL、焦点状态、登录态和酒店身份均未变。

本轮新增了精确日期重定向：只修改已审核请求体中的 `startDate/endDate`，不改请求路径、method、字段结构或只读限制。跨日期执行时不再强制业务值等于 8 月发现样本，而是与当前同日对照值逐字段比较。

业务原值仅位于 `.gitignore` 覆盖的本地 artifacts，本报告只记录字段范围和聚合结论。

### 本轮覆盖的 13 字段

经营提醒、昨日离店间夜、竞争圈排名、点评分、PSI、本店/竞争圈曝光、本店/竞争圈曝光转化率、本店/竞争圈下单转化率、7 天 ROAS 和无违约状态。

### 证据限制

旧采集器自动运行因该酒店未建立旧 Profile 映射而未生成机器输出；本轮接受测试人员提供的三个真实业务页面作为 `manual_page_control`。这足以证明 Silent 数据与页面实际值一致，但不伪称为已完成“旧采集器机器输出”证据。根地图仍保持 `discovered/disabled`。

本轮最终验证：Python 45/45 通过，`compileall` 通过，Extension 19/19 通过。

## 上一阶段结论

在授权测试酒店的合法登录会话中，6 个上一阶段已发现的精确查询接口分别完成：

- Test B：停留 eBooking 首页。
- Test C：停留普通订单页。
- Test D：测试人员手工刷新订单页并等待初始化。

三个阶段均为 **6/6 PASS**。程序没有进入经营报告、金字塔或违约页面，没有导航、自动刷新、点击、输入、抢焦点或打开新标签。

因此本轮已经证明：**这 6 个接口可以从已测试的 eBooking 首页和普通订单页后台调用，不依赖目标业务模块初始化。**

## 逐端点结果

| Endpoint | B | C | D | 当前状态 |
| --- | --- | --- | --- | --- |
| `/datacenter/api/dataCenter/report/getHotelAdvice` | PASS | PASS | PASS | DISCOVERED / REPLAY PASS |
| `/datacenter/api/dataCenter/sale/fetchMarketOverViewV2` | PASS | PASS | PASS | DISCOVERED / REPLAY PASS |
| `/datacenter/api/dataCenter/report/getDayReportServerQuantity` | PASS | PASS | PASS | DISCOVERED / REPLAY PASS |
| `/datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1` | PASS | PASS | PASS | DISCOVERED / REPLAY PASS |
| `/toolcenter/api/cpc/queryCampaignReportList`（7d） | PASS | PASS | PASS | DISCOVERED / REPLAY PASS |
| `/toolcenter/api/psi/queryEbkPunlishMent` | PASS | PASS | PASS | DISCOVERED / REPLAY PASS |

每次 Replay 均确认 HTTP、业务 code、response schema、目标字段、发现阶段同酒店基线、页面 URL、登录态和酒店身份。未发生 redirect，未出现验证码/登录失效。端点已完成查询语义的人工只读审核；本轮没有独立测量服务端状态差异，不把固定布尔值当成无写副作用证明。

## 经营报告完整性

四个经营接口共同覆盖：

- 经营提醒。
- 昨日离店间夜。
- 竞争圈排名。
- 点评分。
- PSI。
- 本店与竞争圈列表页曝光。
- 本店与竞争圈曝光转化率。
- 本店与竞争圈下单转化率。

流量接口的本店/竞争圈原始计数在 B/C/D 均与发现阶段基线一致。转化率按原始分子/分母计算，不使用竞争圈行口径异常的 `flowRate` 标量。四象限继续由程序本地计算。

经营报告必要接口已全部通过 Replay，但因旧采集器逐字段对照尚未完成，模块仍保留 `discovered/disabled`。

## 近 7 天 ROAS

`queryCampaignReportList` 的汇总与逐日两个请求变体均通过 B/C/D：

- 日期范围与发现阶段 7 天样本一致。
- 汇总 ROAS 和逐日记录与页面发现基线一致。
- `pageIdx/pageSize` 有效。
- 分页完整性按 `totalRecords` 与实际 records 数量确认。
- 未依赖异常的 `totalPages`。

30 天回退继续为 **NOT VERIFIED**。当前样本 7 天有数据，不能制造无投流、30 天、加载、请求失败或登录失效结果，因此金字塔模块不整体升级。

## 无违约状态

`queryEbkPunlishMent` 在 B/C/D 均返回零条：

- `totalRecords = 0`
- `records` 为空
- 与目标页发现阶段人工确认的“无违约”一致

有违约正样本与多页数据继续为 **NOT VERIFIED**，违约模块不整体升级。

## 页面无感与刷新稳定性

三个阶段均确认：

- `window.location.href` 前后完全一致。
- 当前页面焦点状态不变。
- 未创建新 target/tab。
- 酒店身份和登录态保持不变。
- B/C/D 使用相同的安全模板 hash。
- Test D 刷新后无需重新进入目标页面，也无需重新捕获动态初始化信息。

发现记录中的动态浏览器/指纹字段在模板编译时被删除；删除后六个接口仍正常返回，说明本轮请求不依赖持久化这些值。

## 安全与副作用

- 只允许 6 个精确路径和 POST 方法。
- 每个 POST 均有查询语义、发现阶段自然请求证据和独立只读说明。
- Replay 仅使用当前页面中由浏览器托管的同源会话请求能力。
- 不读取、导出或保存 Cookie、Authorization、Token、Session、CSRF、localStorage 或 Profile。
- 报告不保存请求 payload、response 业务值、酒店账号或认证 header。
- 捕获集只在“当前会话实时 flow 酒店 ID 集合 = 发现基线 ID 集合”时允许绑定；本地只保存捕获集和酒店身份的单向摘要，捕获变更或酒店切换均拒绝继续。
- 未发现疑似写请求；运行报告将独立服务端副作用测量标记为 `NOT_MEASURED`。
- 原始本地结果位于 gitignored `artifacts/`，提交前继续执行敏感扫描。

## API Map 与产品化边界

根接口地图仍保持 `map_kind:discovery`、`map_status:discovered`，
全部模块 `enabled:false`。六个端点逐项记录：

- `result:discovered`
- `read_only:true`
- `required_page_context:mixed`（已测首页/订单页）
- `can_call_from_any_ebooking_page:null`
- Test B/C/D PASS

没有把端点或整个地图直接改为 VERIFIED：旧采集器逐字段对照、30 天 ROAS 和有违约正样本仍缺证据，扩展内置地图继续 `unverified/disabled`。

## 对核心问题的回答

> 门店员工只正常打开或刷新携程后台，我们能否在不干扰其工作的前提下，把现有采集程序里的整套携程数据完整采回来？

**对于已经发现的 6 个接口、当前测试酒店以及已测首页/订单页，答案是可以。Silent Collector 的核心技术路线已经成立。**

经营报告目标字段、近 7 天 ROAS 和当前“无违约”状态均已从首页、订单页和刷新后的订单页成功取得。完整生产结论仍需补齐旧采集器逐字段对照、其他普通页样本、30 天 ROAS 回退与有违约正样本。

## 建议下一步

1. 用旧采集器对经营报告、7 天 ROAS 和无违约做同酒店逐字段对照。
2. 补充房态/价格等其他普通页样本，再决定是否将上下文升级为任意 eBooking 页。
3. 补充真实 7 天为零/无数据场景，验证 30 天回退及未投流、加载、失败、登录失效的区分。
4. 补充有违约和多页样本。
5. 全部门槛满足后再生成独立扩展执行地图，接入冷却、缓存和长时间稳定性测试；继续禁止自动切页 fallback。
