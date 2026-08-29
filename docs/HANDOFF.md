# Project Handoff

更新时间：2026-08-29

## 当前分支与基线

当前任务分支为 `codex/replay-target-endpoints`，从 PR #1 合并后的最新
`main` 提交 `ed9a1c627684d5e62196dfff201576a854a73690` 创建。

本轮只验证上一阶段已经 DISCOVERED 的 6 个精确业务接口。没有重新发现接口，没有自动进入经营报告、金字塔或违约看板，没有导航、刷新、点击、输入、抢焦点、打开新标签或修改后台数据。Test B/C/D 的页面切换与刷新均由测试人员手工完成。

## 受控 Replay 结论

| Endpoint | 业务 | Test B 首页 | Test C 订单页 | Test D 订单页手工刷新后 |
| --- | --- | --- | --- | --- |
| `POST /datacenter/api/dataCenter/report/getHotelAdvice` | 经营提醒 | PASS | PASS | PASS |
| `POST /datacenter/api/dataCenter/sale/fetchMarketOverViewV2` | 离店间夜、竞争圈排名 | PASS | PASS | PASS |
| `POST /datacenter/api/dataCenter/report/getDayReportServerQuantity` | 点评分、PSI | PASS | PASS | PASS |
| `POST /datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1` | 曝光与转化原始计数 | PASS | PASS | PASS |
| `POST /toolcenter/api/cpc/queryCampaignReportList` | 近 7 天 ROAS 汇总与逐日记录 | PASS | PASS | PASS |
| `POST /toolcenter/api/psi/queryEbkPunlishMent` | 违约列表与数量 | PASS | PASS | PASS |

三个阶段共同满足：

- HTTP 与业务 code 成功，response schema 与发现阶段一致。
- 目标业务字段有效，并与同酒店发现阶段基线一致。
- 当前页面 `window.location.href` 前后完全一致。
- 页面焦点状态不变，没有打开新标签。
- 登录态和酒店身份保持不变。
- 未观察到 redirect、登录失效或目标模块初始化依赖。端点已做查询语义的人工只读审核，但本轮没有独立测量服务端状态差异。
- B/C/D 使用相同安全模板；手工刷新后无需重新进入目标模块，也无需重新捕获初始化信息。

## 经营报告

四个必要接口全部逐端点通过 B/C/D，并明确：

- `read_only:true`
- `required_page_context:mixed`（已测首页和订单页）
- `can_call_from_any_ebooking_page:null`（未泛化到所有普通业务页）
- Test B/C/D 全部 PASS

已验证字段覆盖经营提醒、离店间夜、竞争圈排名、点评分、PSI、本店/竞争圈曝光、曝光转化率和下单转化率。转化率继续由
`detailExposure/listExposure` 与
`orderFillingNum/detailExposure` 计算，不采用已知口径异常的竞争圈
`flowRate` 标量。经营报告模块仍为 `discovered/disabled`：已有 Replay PASS，但尚未完成旧采集器逐字段对照，不满足项目的最终 `verified` 门槛。

## 金字塔 / ROAS

近 7 天端点已逐端点通过 B/C/D，但仍保留 `discovered`：

- 汇总请求与逐日请求均通过 B/C/D。
- `startDate/endDate`、`pageIdx/pageSize` 沿用发现阶段模板。
- 完整性使用 `totalRecords + records.length` 判断，不信任异常的
  `totalPages`。
- 7 天 ROAS 与发现阶段页面基线一致。

30 天请求仍为 `null / NOT VERIFIED`。本账号近 7 天有数据，没有真实触发 30 天回退，也没有覆盖真实未投流、加载中、请求失败或登录失效样本。因此金字塔模块不整体升级，继续 `discovered/disabled`。

## 违约看板

违约查询端点已逐端点通过 B/C/D，但仍保留 `discovered`，三阶段均返回：

- `totalRecords = 0`
- `records` 为空
- 与发现阶段测试人员确认的“无违约”状态一致

“有违约”正样本和多页完整性仍为 `NOT VERIFIED`，因此模块不整体升级，继续 `discovered/disabled`。

## API Map 状态

根 `ctrip_api_map.json` 继续保持：

- `map_kind: discovery`
- `map_status: discovered`
- 所有模块 `enabled:false`

六个端点分别记录 `discovered`、人工只读审核和 B/C/D PASS 证据。上下文只记录已测首页/订单页，不直接外推为所有 eBooking 路由。扩展内置
`extension/config/ctrip_api_map.json` 仍为
`unverified/disabled`，本轮不会自动执行或生产启用。

## 新增能力

- `replay-targets` CLI：只允许 6 个精确查询 POST。
- 从 gitignored 脱敏捕获恢复非认证业务参数，并删除动态浏览器/指纹字段。
- 首页、订单页、手工刷新后页面状态门槛。
- 每端点 HTTP、业务 code、schema、业务字段、基线、分页和酒店证据核对。
- 精确 URL、焦点、新标签、登录态与酒店身份不变检查。
- 首次需要显式确认；只有当前会话实时 flow 酒店 ID 集合与发现基线完全一致时，才在 gitignored artifacts 中保存“捕获集摘要 + 当前酒店单向摘要”绑定。酒店切换或捕获变更会 fail-closed。
- 输出报告只保存布尔结论、状态和安全 hash，不保存请求 payload、response 业务原值或认证材料。

## 安全状态

- Replay 仅使用当前页面中由浏览器托管的同源会话请求能力。
- 未读取或导出 Cookie、Token、Session、Authorization、CSRF、localStorage、Chrome Profile 或 Cookie 数据库。
- 动态指纹字段从受控模板中删除；6 个接口在删除后仍全部成功。
- 本地 Test B/C/D 报告位于被 `.gitignore` 排除的
  `artifacts/replay-target-endpoints/`，敏感扫描通过。
- 捕获集酒店绑定仅保存单向摘要，不保存真实酒店 ID/名称，且位于 gitignored `artifacts/`。
- 提交前扫描发现并脱敏了旧采集器基线文档中的本机绝对路径，保留算法与哈希证据。
- 所有测试请求均为发现阶段已证明语义的报告/列表查询型 POST；未发现疑似写端点。运行报告对服务端写副作用标记为 `NOT_MEASURED`，不将固定布尔值当作无变更证明。

## 测试

完成提交前必须运行：

```sh
PYTHONPATH=python python3 -m unittest discover -v
python3 -m compileall -q python
cd extension && npm test
```

最终结果：Python 37/37 通过，Python compileall 通过，Extension 19/19 通过。

## 当前技术结论与下一步

**Silent Collector 的核心调用路线在已测首页和订单页上成立：6 个已发现接口可在后台同源调用，不需要进入三个目标模块。**

这还不是完整生产验收。下一步应先完成旧采集器逐字段对照，再补充房态/价格等其他普通页、30 天 ROAS 回退、真实无投流/异常状态和“有违约”正样本。只有这些门槛完成后才生成独立扩展执行地图。任何未覆盖场景继续 fail-closed，不得用自动切页作为 fallback。
