# Ctrip Silent Collector POC 阶段报告

报告日期：2026-08-29
阶段：目标业务页面真实接口发现

## 本轮结论

在授权测试酒店的已登录 eBooking 会话中，由测试人员手工进入目标业务页面，Python Inspector 仅被动监听页面自然产生的请求。程序没有导航、自动刷新、点击、键盘输入、抢焦点、自动登录或修改后台数据。

本轮发现并完成页面抽样核对：

- 经营报告：**DISCOVERED**，4 个真实接口。
- 金字塔：**DISCOVERED**，1 个近 7 天 ROAS 接口；30 天回退和异常状态仍为 **NOT VERIFIED**。
- 违约看板：**DISCOVERED**，1 个列表/数量接口；已核对“无违约”，正样本仍为 **NOT VERIFIED**。

`DISCOVERED` 只表示“目标页面自然请求、response 业务字段、页面显示”三者已经对应，不表示已证明可以从任意 eBooking 页面调用。本轮没有执行 replay，也没有把任何端点标记为 `VERIFIED`。

## 经营报告

| 接口 | 承担数据 | 日期 | 分页 | 页面抽样 |
| --- | --- | --- | --- | --- |
| `POST /datacenter/api/dataCenter/report/getHotelAdvice` | 经营提醒 | 未观察到 | 请求无分页 | 一致 |
| `POST /datacenter/api/dataCenter/sale/fetchMarketOverViewV2` | 昨日离店间夜、竞争圈排名 | `startDate` 单日；`startDateType` 语义待解码 | 无 | 一致 |
| `POST /datacenter/api/dataCenter/report/getDayReportServerQuantity` | 点评分、PSI | 未观察到 | 无 | 一致 |
| `POST /datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1` | 本店/竞争圈曝光、曝光转化率、下单转化率 | `startDate/endDate`，本次为同日样本 | 无；观察到两行 | 一致 |

流量转化接口中，页面比例可由 response 的曝光、详情及下单原始计数准确计算。竞争圈行单独返回的 `flowRate` 标量与页面口径存在差异，因此后续解析应使用原始分子/分母，不应盲信该标量。

四象限仍由程序计算，不从携程接口读取。

## 金字塔 / ROAS

真实接口：

`POST /toolcenter/api/cpc/queryCampaignReportList`

- 请求包含 `startDate`、`endDate`、`convertPeriod`、`isSummary`、`pageIdx`、`pageSize`。
- response 的 `data.records` 提供 ROAS、消耗、订单金额和日期，另有 `totalRecords/totalPages`。
- 近 7 天汇总和逐日页面数据均完成一次抽样核对。
- 抽样支持日期首尾为闭区间。
- 观察到 `totalPages` 与非空 records 不一致；下一轮必须以 `totalRecords` 和实际 records 做防御性分页校验。

未验证事项：

- 本账号近 7 天有数据，没有自然触发 30 天回退。
- 无法用本次样本证明“真实未投流、页面仍加载、请求失败、登录失效”的响应差异。
- 最大 pageSize 和多页完整性未验证。

## 商家违规 / 违约看板

真实接口：

`POST /toolcenter/api/psi/queryEbkPunlishMent`

- 请求包含分类/状态筛选以及 `pageIndex/pageSize`。
- response 的 `data.totalRecords` 与 `data.records` 可用于判断违约数量和读取列表。
- 当前样本返回零记录；测试人员明确确认页面显示“无违约”，抽样一致。
- 未观察到日期参数。
- 有违约正样本和多页数据仍未验证。

## 接口真实性与排除项

六个接口均满足本轮 `DISCOVERED` 门槛：由对应业务页面自然产生、返回非 HTML 业务结构、存在页面对应字段、抽样一致、不是消息通知/订单详情，也未观察到修改业务数据的效果。

被排除的候选包括页面 HTML 壳、通知/二维码/优惠券、普通 CPC 支持接口、诊断与活动明细接口，以及与页面所示近 7 天汇总不一致的当前日直连调用。排除后没有以泛化 URL 关键词代替业务证据。

## API Map 与执行安全

根目录 `ctrip_api_map.json` 保存 6 个已发现端点的脱敏结构：

- `map_kind: discovery`
- `map_status: discovered`
- 所有模块 `enabled: false`
- 所有端点 `read_only: false`
- `can_call_from_any_ebooking_page: null`

因此它不能触发扩展采集。扩展内置地图仍保持
`unverified/disabled`。只有下一轮普通页面 replay 和 Test A-D 完成后，才可单独生成经过审核的可执行地图。

平台动态会话、追踪、设备和指纹字段已按 fail-closed 规则脱敏。接口地图、文档和测试不包含真实 Cookie、Authorization、Token、Session、CSRF、query 动态值、酒店账号、Profile 路径或业务原始 response。六个接口未观察到疑似写操作，但在未完成独立只读审核前不会标记 `read_only:true`。

## 对 POC 核心问题的当前回答

> 门店员工只正常打开/刷新携程后台，我们能否在不干扰其工作的前提下，把现有采集程序里的整套携程数据完整采回来？

**本轮仍不能宣称可以，但已经从“未知接口”推进到“有 6 个精确、已完成业务对应的 replay 候选”。**

开发期人工进入目标页面只用于一次性发现，不是正式产品要求。是否能在首页、订单页、房态页或价格页复用当前登录会话调用这些接口，必须由下一轮普通页面 replay 证明。任何仍依赖特定页面初始化的模块，都应判为当前无法完全无感，不能以自动切页替代。

## 下一步建议

1. 逐个审核六个候选的只读性质，仅在当前授权进程内构造无敏感值 replay 模板。
2. 从首页和普通业务页执行 Test B/C，并在测试人员手工刷新后执行 Test D；全过程校验 URL 未变和未抢焦点。
3. 对经营报告全部字段、7 天 ROAS、30 天回退和违约状态与旧采集器逐字段比较。
4. 补充近 7 天无数据/未投流样本与有违约/多页样本。
5. 只有普通页 replay、异常状态、分页和逐字段对照均通过，才将对应模块提升为 `VERIFIED` 并考虑启用。
