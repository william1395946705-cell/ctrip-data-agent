# Project Handoff

更新时间：2026-08-29

## 当前分支与边界

当前任务分支为 `codex/discover-target-endpoints`，基于已推送的
`codex/verify-real-endpoints` 提交
`b2eb70d2cb86cdb367b74d0f54bc8037d30fdf30` 创建，保留上一轮的
`observe`、脱敏、误分类修复和测试。

本轮只在本人明确授权的已登录测试酒店会话中做目标页面接口发现。测试人员手工进入或刷新页面；程序仅提前附加响应监听器，没有导航、自动刷新、点击、键盘输入、抢焦点或自动登录。本轮没有做普通页 replay、Test B/C/D、定时采集或生产启用。

## 三个目标模块

### 经营报告：DISCOVERED

发现 4 个真实接口，均由经营报告页面自然触发，并通过 response 字段与页面可见指标抽样核对：

1. `POST /datacenter/api/dataCenter/report/getHotelAdvice`
   - 业务：经营提醒。
   - 日期：请求中未观察到日期字段。
   - 分页：请求无分页；响应带 `totalPage/totalRecords` 元数据。
2. `POST /datacenter/api/dataCenter/sale/fetchMarketOverViewV2`
   - 业务：昨日离店间夜及竞争圈排名。
   - 日期：`startDate` 为 `YYYY-MM-DD` 单日；`startDateType` 为整数，但其枚举语义尚未独立解码。
   - 分页：未观察到。
3. `POST /datacenter/api/dataCenter/report/getDayReportServerQuantity`
   - 业务：点评分、PSI。
   - 日期、分页：请求中均未观察到。
4. `POST /datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1`
   - 业务：本店/竞争圈列表曝光、曝光转化率、下单转化率。
   - 日期：`startDate/endDate` 为 `YYYY-MM-DD`；本次自然请求是同日起止的单日样本。
   - 分页：未观察到；响应固定观察到本店与竞争圈两行。
   - 口径：页面转化率与原始分子/分母计算值一致；竞争圈行的单独 `flowRate` 标量与页面口径有差异，后续解析应按原始计数计算。

四象限继续由程序根据曝光与下单转化率比较计算，不从携程读取。

### 金字塔：DISCOVERED

发现 1 个真实接口：

- `POST /toolcenter/api/cpc/queryCampaignReportList`
  - 业务：近 7 天 ROAS 汇总及逐日数据。
  - 日期：`startDate/endDate` 为 `YYYY-MM-DD`；抽样响应同时包含首尾日期，支持本次 7 天样本为闭区间。
  - 周期/汇总：`convertPeriod`、`isSummary`。
  - 分页：`pageIdx/pageSize`，响应有 `totalRecords/totalPages`；观察到 `totalPages` 与非空 records 不一致，正式解析需以 `totalRecords` 防御性判断，最大返回量尚未验证。
  - 页面抽样：7 天汇总 ROAS 和逐日行与 response 一致。

当前账号近 7 天有投流数据，因此以下仍为 **NOT VERIFIED**：30 天回退请求、7 天无数据、真实未投流、加载中、请求失败和登录失效的真实响应语义。接口地图中的 `30d` 保持 `null`。

### 违约看板：DISCOVERED

发现 1 个真实接口：

- `POST /toolcenter/api/psi/queryEbkPunlishMent`
  - 业务：违约列表与数量；路径拼写按真实请求保留。
  - 日期：未观察到日期字段。
  - 分页：`pageIndex/pageSize`，响应有 `totalPages/totalRecords`；观察页大小为 30，多页正样本尚未获得。
  - 页面抽样：response 为零记录，测试人员明确确认页面显示“无违约”，两者一致。

“有违约”正样本和多页完整性仍为 **NOT VERIFIED**。

## 接口地图状态

根目录 `ctrip_api_map.json` 已记录上述 6 个真实接口，状态为
`map_kind: discovery`、`map_status: discovered`。全部模块继续
`enabled: false`，端点继续 `read_only: false`，
`can_call_from_any_ebooking_page: null`。这表示只证明了目标页面上的自然查询和业务对应关系，尚未完成只读批准及普通页面 replay，扩展不能执行这些接口。

`extension/config/ctrip_api_map.json` 有意继续保留默认
`unverified/disabled`，避免接口发现结果被误当成生产配置。

## 认证与安全观察

- 已确认请求依赖同源页面会话和浏览器托管凭据；本轮可见 header 中未出现 Cookie、Authorization 或 CSRF 值，但“未观察到”不等于“不需要”。
- 抓包中出现平台动态浏览器/指纹字段。脱敏器已补强并对本地历史捕获重新脱敏；真实值没有写入接口地图、文档、测试或 Git。
- API Map 只保存 URL path、字段类型、业务字段路径和核对结论，不保存 query 值、Cookie、Token、Session、Authorization、CSRF、追踪标识、设备标识、酒店账号或本机 Profile 路径。
- 六个候选均表现为查询用途，未观察到写操作；但本轮不把这个观察提升为正式 `read_only:true`。
- 原始授权会话捕获仅位于被 `.gitignore` 排除的本地 `artifacts/`。

## 代码能力变化

- `observe --until-enter` 支持监听器先就绪，再等待测试人员手工操作并以回车结束。
- endpoint matcher 收窄金字塔判断，普通 CPC 支持接口不会再被泛化归类。
- 捕获记录增加只含类型、不含值的 `request_context_types`。
- Python 与扩展脱敏规则覆盖动态会话、追踪和指纹字段。
- API Map schema 支持 `discovered`，发现地图允许记录尚未批准的
  `read_only:false`，但扩展执行门槛仍要求独立的
  `verified + enabled + read_only + any_ebooking_page`。

## 测试

完成提交前运行：

```sh
PYTHONPATH=python python3 -m unittest discover -v
python3 -m compileall -q python
cd extension && npm test
```

- Python `unittest`：29/29 通过。
- Python `compileall`：通过。
- Extension Node tests：19/19 通过。

测试只证明分类、脱敏、API Map 状态和 fail-closed 行为，不代表普通页面 replay 已成功。

## 尚存疑点与下一步

下一轮已经具备对 4 个经营接口、7 天 ROAS 接口和无违约查询做精确普通页面 replay 的候选条件，但所有端点仍须逐项人工批准为只读。随后应在首页、订单页和刷新后的普通页执行 Test B/C/D，验证会话复用、日期、连续调用、分页及当前 URL 不变，并与旧采集器逐字段对照。

在真实 7 天无数据账号或时间窗出现前，30 天回退及“未投流/加载/失败/登录失效”不能宣称完成；在有违约账号样本出现前，违约正样本和多页完整性也不能宣称完成。若 replay 证明某接口必须依赖目标页面初始化，应将该模块判为当前无法满足正式无感目标，不得用自动切页替代。
