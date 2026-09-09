# Project Handoff

## 2026-09-09 美团首页独立入口 0.3.0

- 用户确认第二份 Replay 报告来自另一门店，并确认首页可重放。两份报告各 17/17 匹配，仍属于先捕获再重放，不证明独立初始化。
- 从最新 main 建立 `codex/meituan-direct-home`，保留未合并的 `codex/meituan-controlled-replay` 作为依赖。本轮不修改携程目录。
- 新增首页直接测试入口：无需模板、无需数据中心自然请求，当前页执行三个精确已发现 GET 路径。昨日请求只附带已知 dateRange=1；评分无日期参数。浏览器自行带当前凭据。
- 这是参数删减的受控实验，不是完整请求已还原。脱敏文件删除了酒店参数名/值、dataScope 值及运行时值；代码没有证据说明服务器一定可补齐，故显式 omitted_parameters，整体始终 NOT_VERIFIED，返回数据亦需核对门店/日期。
- 错误停止后续请求，无认证/验证绕过、无失败自动重试。一次最多3请求，单请求15秒、手动测试间隔30秒。原捕获对照折叠保留。
- 新增隔离页面上下文测试：无捕获启动、参数集合、401/403/429、业务失败、HTML、网络异常、导航中断、空值和不可信报告脱敏。美团15/15、Python45/45、携程扩展24/24、compileall、JS语法和diff检查通过。
- 下一步用户在已登录首页直接点击新入口并导出 meituan-direct-report.json；无需先进入数据中心。实机结果仍待回传，尚未实现可确认门店身份的正式自动采集。

## 2026-09-09 美团普通页 Replay 实测回传

- 测试人员回传 `meituan-replay-report.json`，整体 `PASS`。三个真实已发现接口均 HTTP 200、业务码 0：获客宝 4/4、评分 3/3、经营 10/10 张卡片逐值 `match`，合计 17/17。
- 三个端点均报告 `current_page_unchanged:true`、`focus_unchanged:true`；没有自动切页或抢焦点。报告未包含原始 URL、认证参数、请求头或酒店身份。
- 这是普通页复用当前浏览器会话的受控 Replay 证据；仍缺同一时刻页面可见值截图/人工逐字段核对和机器酒店身份绑定。接口地图继续 `discovered/disabled`，不得据此开启正式自动采集。
- 下一步应在另一家授权门店重复“数据中心昨日自然捕获 → 同店普通页一次 Replay”，并观察刷新/跨日与 CORS、登录失效、结构变化的 fail-closed 行为；不扩大请求范围。

## 2026-09-09 美团受控普通页 Replay（开发中）

- 分支 `codex/meituan-controlled-replay` 在被动发现分支上增加了 0.2.0 DevTools 面板：先捕获数据中心“昨日”页面自然产生的三个已发现 GET 接口，再由测试人员手动确认同店普通页面并执行一次只读 Replay。
- 目前没有真实普通页 Replay 结果，因此三个接口状态仍是 `DISCOVERED / NOT VERIFIED`。不能把离线测试或界面代码存在当成接口已验证。
- 运行时模板仅保存于当前 DevTools 面板内存，原始 URL（含动态会话查询值）不写入发现导出、Replay 报告、Git 或日志；重放不读取请求头，不导航、不点击、不输入、不抢焦点。
- Replay 报告只包含安全的路径、HTTP/业务状态、卡片数量、有限 old/replay 值比较、URL/焦点是否保持；网络失败、CORS、登录失效、重定向和结构变化均 fail-closed。
- 必须由测试人员手动完成：数据中心昨日页开始捕获并刷新 → 三路齐全 → 手动切同一家普通页 → 勾选同店确认 → 执行一次 Replay → 导出 `meituan-replay-report.json`。在这一步完成前不要宣称普通页可用。

## 2026-09-09 美团三类目标接口发现

- 0.1.1用户导出188条自然请求，经营businessData（真实版本段v1）、score、customerAcquisition三类GET接口均HTTP200/business status=0，返回10/3/4张卡片。字段标题及可见数值与用户此前截图抽样相符，标记DISCOVERED，非VERIFIED。
- 明确经营/获客宝dateRange字符串1；dataScope及酒店参数名仍被脱敏省略。未恢复删除值、未执行replay。经营涨跌方向部分被省略、满房率截图遮挡、评分更新时间及机器酒店身份绑定尚待验证。
- 本次只更新证据文档，未改代码；既有监听器5/5脱敏测试通过记录仍适用。具体证据和限制见docs/MEITUAN_DISCOVERY.md。用户导出及业务原值不进入Git。

## 2026-09-09 美团首次捕获分析及监听器0.1.1

- 收到190条用户提供的脱敏记录，识别 eb.meituan.com 上 businessData、score、projectEffect/customerAcquisition 三类 GET 候选，HTTP均为200，分别返回10/3/4张卡片。真实用途及昨日数据仍 NOT VERIFIED，详见 docs/MEITUAN_DISCOVERY.md。
- 0.1.0 删除了字符串类型业务数值，无法完成页面抽样比对。0.1.1仅针对候选路径保留白名单标题、有限数值字符串和单位；保留有限 dateRange/dataScope 查询值、实际短版本段。不扩大为自动请求，不保留凭据或真实业务样本到仓库。
- 新增白名单投影与10张卡片测试，未知字符串及身份继续省略。下一步需要0.1.1人工再捕获；不能把候选名称或HTTP200当作完整采集成功。

## 2026-09-09 美团昨日数据被动监听准备

- 分支 codex/meituan-passive-observer 从最新 main 创建，使用独立工作树，不覆盖正在使用的携程 0.3.3 目录或尚未合并的修复分支。
- 新增独立 meituan-observer/ DevTools 扩展，无 host permissions、storage 或 debugger 权限；用户启动后只读取当前美团标签自然请求，不发起业务请求、不导航。此为用户授权的美团发现阶段，不扩大携程执行地图。
- 仅考虑 meituan.com/dianping.com HTTPS 请求，当前被检查页面必须为 me.meituan.com。请求头不读取；URL 查询值、身份字段和响应原始文本不导出，独立 JS 脱敏边界保留有限数值指标、ISO 日期及有界结构。非 JSON、超限和编码内容明确标记遗漏，不将其当无数据。
- 用户指定范围为数据中心昨日经营、评分与获客宝可见字段。当前真实接口均 NOT VERIFIED；截图和请求名称不足以确认接口，安装后仍需自然请求捕获和页面抽样核对。无任何 replay 或自动采集实现。
- 离线测试覆盖域名边界、凭证和身份删除、原始字符串不导出、数值样本及超限内容。真实 Chrome 安装/监听/导出尚待用户验收。
- 本轮 main 基线 Python 45/45、Extension 24/24、新监听器脱敏测试 3/3、compileall 通过。仅打包监听器运行文件，不打包测试或业务记录。已知非 JSON/text 响应不取正文；因此 HTML 页面壳不会消耗捕获额度。

## 2026-09-05 打包与浏览器测试（Draft 交付）

- 普通 Chrome 扩展 0.2.0 经测试人员重新加载后，于 09-05 完成首页一次采集；用户提供的截图与本地结果显示 13 个核心字段非空、failed_modules/warnings 为空、URL 未改变。此为用户提供的真实扩展证据，不代替同日逐字段页面对照。
- 前次 unverified 阻塞在重新加载扩展后消失；未取得旧运行实例的完整状态，不能进一步断言缓存或具体版本是唯一根因。
- 0.2.1 弹窗新增版本、地图状态和直接可见的警告，并将三个模块失败标为全部失败。酒店 ID 为空和 flow 行归属验证仍是未解决风险。
- 普通 Chrome 人工验收：首页、订单页、房态页、价格页均成功。价格页连续 6/6 成功，测试人员确认包含手动刷新后恢复，且全程无自动切页或抢焦点。首页/订单/房态/价格末轮共四份用户提供结果中的 13 个核心字段一致、失败模块和警告为空；未保存全部六轮逐请求证据，不将用户确认提升为自动化监测证据。
- 结果时间（UTC）：首页 07:51:58、订单 07:59:14、房态 08:00:02、价格末轮 08:01:47。采集约数秒为用户观察，未做延迟基准。数据日由模板按本机日期推算，结果本身尚未输出统计窗口。

- 分支 `codex/package-and-browser-test`，基于 PR #3 合并提交 `72d20d2e84f3100d2b00951bce3f0451ed39caef`。
- Python Silent Replay：首页三轮成功（09-03）；订单一轮、房态一轮、价格三轮、手工刷新价格页后一轮均成功（09-05）。每轮六个端点，URL、焦点、酒店身份保持不变。09-05 数据日为 09-04。
- 房价与房态共享 `/ebkovsroom/inventory/calendar`；价格标签由测试人员人工确认，不能仅凭 URL 区分。
- MV3 包新增手动入口、最近状态/时间、动态日期和真实响应适配；内置地图明确为 `controlled_test`，模块仍 `discovered`，禁止自动启动。根地图不变。
- 新增业务码/字段/完整性检查；手动操作重新读取当前标签状态，不选择其他缓存标签。
- 扩展实际安装与手动采集已有用户实测证据；Service Worker 长时间休眠恢复、同日页面逐字段对照、旧采集器机器输出对照仍 NOT VERIFIED。Python Replay 的成功不能替代扩展实测。
- 本地打包目录 `dist/ctrip-silent-collector/` 与 ZIP 均被 Git 忽略；通过 `cd extension && npm run package` 从分支源码重建。此为受控人工试用包，不是生产验收。
- 提交前安全收紧：受控测试地图必须精确匹配内置审核模板，拒绝修改 URL、payload 或 headers；脚本执行异常时不再声称 URL 已测量不变。此收紧在人工六轮测试之后完成，经过离线回归但未重复整套人工测试。
- 最终离线检查：Python 45/45、compileall、Extension 24/24、git diff --check、打包及 ZIP 完整性检查通过。源码/测试/文档未检出真实酒店名、本机绝对路径或常见真实凭据格式；dist 和 artifacts 均保持忽略。该扫描不等于对所有潜在秘密的数学保证。

以下为上一轮交接证据，保留其阶段边界。

更新时间：2026-09-03

## 当前分支与基线

当前任务分支为 `codex/compare-legacy-vs-silent`，从 PR #2 合并后的
`main` 提交 `8b46a32640b9a05678685f3931bb05355a2a02a9` 创建。

本轮对同一授权测试酒店、同一数据日的页面人工对照与 Silent Replay 进行 13 字段对比。程序仅停留在普通 eBooking 首页，没有导航、刷新、点击、输入、抢焦点或打开新标签。

## 本轮真实对照结论

- 对照字段：13。
- 完全一致：13。
- `TIME_DRIFT`：0。
- 真正不一致：0。
- Silent 前后两次快照一致，当前页 URL、焦点状态、登录态和酒店身份保持不变。
- 经营数据日为 2026-09-02；7 天 ROAS 窗口为 2026-08-27 至 2026-09-02。只更换已审核 payload 中的日期值，路径、method、字段集合和只读限制不变。

逐字段覆盖：经营提醒、昨日离店间夜、竞争圈排名、点评分、PSI、本店/竞争圈曝光、本店/竞争圈曝光转化率、本店/竞争圈下单转化率、7 天 ROAS 和无违约状态。业务原值仅保存在 gitignored `artifacts/legacy-vs-silent/` 中，不进入 Git 或文档。

`TIME_DRIFT` 不由时间差自动推断；只有 Silent 前后值确实变化，且对照值等于某个快照或被两个数值包围时才能标记。排名按完整 `rank / total` 比较，不得只比较第一个数字。

## 对照证据边界

测试人员提供了同日经营报告、金字塔和违约看板截图，作为 `manual_page_control` 真实页面对照。旧采集器自动对照尝试在启动前被 `hotel_profile_mapping_missing` 拦截，没有伪造旧采集器机器输出。因此：

- 可确认 Silent 返回值与同日真实页面 13/13 一致。
- 严格的“旧采集器机器输出”对照仍未完成；根 API Map 继续 `discovered/disabled`，不因本轮截图对照而升级为生产可执行。

## 上一阶段受控 Replay 结论

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

最终结果：Python 45/45 通过，Python compileall 通过，Extension 19/19 通过。

## 当前技术结论与下一步

**Silent Collector 的核心调用路线在已测首页和订单页上成立：6 个已发现接口可在后台同源调用，不需要进入三个目标模块。**

这还不是完整生产验收。下一步应先完成旧采集器逐字段对照，再补充房态/价格等其他普通页、30 天 ROAS 回退、真实无投流/异常状态和“有违约”正样本。只有这些门槛完成后才生成独立扩展执行地图。任何未覆盖场景继续 fail-closed，不得用自动切页作为 fallback。
