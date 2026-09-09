# 美团昨日数据接口发现

## 2026-09-09 受控普通页 Replay 实现（0.2.0）

已在独立 `meituan-observer/` DevTools 扩展中加入受控验证链路，但尚未把真实普通页结果标记为 VERIFIED：

- 测试人员先在数据中心“昨日”页手动启动监听并刷新；仅当三个已发现的精确 GET 路径自然返回完整卡片时，才在当前进程内保留一次性运行时模板。
- 测试人员随后手动切到同一家门店的普通页，并勾选人工确认。扩展在 inspected top page 中按原始自然 URL 顺序执行一次 `fetch(..., credentials: include, redirect: manual)`；不导航、不点击、不输入、不读取请求头、不使用隐藏 iframe 或扩展后台请求。
- 运行时 URL、查询参数、身份字段和会话值仅存在本机当前 DevTools 进程内存；发现导出和 Replay 报告均不含原始 URL、请求头、Cookie、Token、酒店身份或原始响应。
- 报告区分 HTTP/登录失效/重定向/阻断/网络或解析失败、业务状态、卡片结构变化、逐卡值比较、当前 URL 和焦点状态。失败不会转成“无数据”。值变化只记为 `mismatch_or_time_drift`，不会自动推断 `TIME_DRIFT`。
- 捕获模板 10 分钟内有效、同一 Asia/Shanghai 日期且三路身份查询键必须一致；每次捕获集只允许一轮手动 Replay。切离美团后台会清空临时模板。

目前仍需要用户在真实授权会话中完成一次完整人工验收：自然捕获三路 → 手动切到同店普通页 → Replay → 导出报告。跨域 CORS、登录态、酒店绑定、日期语义和逐卡页面同期对照未验证前，接口地图继续 `discovered/disabled`，不能用于生产自动采集。

## 2026-09-09 第二次捕获：三类接口 DISCOVERED

用户提供0.1.1脱敏导出，共188条记录。下列自然GET请求均HTTP200、业务status=0，并包含完整10/3/4张目标卡片的白名单投影。

| 实际路径（域名 eb.meituan.com） | 状态 | 证据 |
| --- | --- | --- |
| /api/v1/ebooking/home/businessData | DISCOVERED | 十张经营卡片标题与范围对应；引流价、曝光、浏览人数、支付转化率、销售和入住数据等可见值及若干排名、同行均值与此前昨日截图一致 |
| /api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/score | DISCOVERED | 三项评分及排名与此前评分区截图逐项相符；重复两次响应投影一致 |
| /api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/projectEffect/customerAcquisition | DISCOVERED | 四项获客宝指标与此前可见四张卡片数值相符；未独立确认附加比较值含义 |

证据为用户提供的页面截图与自然请求脱敏导出，不是机器同期页面采样；酒店身份被删除，当前归属依据用户操作上下文，尚无机器身份绑定。满房率在昨日截图中被遮挡，不标为已页面核对。业务原值及整个导出文件不提交Git。

已观察参数：经营与获客宝 dateRange 均为字符串1，对应用户所述昨日操作；评分没有 dateRange。经营 dataScope 值仍被脱敏省略；酒店参数名被0.1.x替换为private-field。GET无body。实际日历日期尚未从响应确认，不将采集日期等同统计日期。

响应为 data.cards 卡片数组，白名单投影完整覆盖17张卡片；这是本次卡片数量完整，不代表其他明细接口或分页已验证。extAttrs的部分方向符号被省略，不能把null解释为无变化或根据正的幅度猜涨跌；标签/键语义还需确认。评分更新时间也未保留。

发现阶段可以确认这三类接口对应目标页面。所有接口保持 enabled:false，尚未进行普通页replay。下一阶段需要在本机内存保留运行时请求模板并补齐dataScope、酒店参数名及绑定、指标方向和日期依据；不能从脱敏文件猜测恢复被删值。页面域名me.meituan.com与接口域名eb.meituan.com不同，跨域会话及iframe执行上下文必须实测。

## 2026-09-09 首次被动捕获

测试人员提供 0.1.0 导出的190条脱敏记录。以下为实际观测的 GET 候选，均返回 HTTP 200，域名为 eb.meituan.com；尚未做业务值核对或 replay，状态全部 NOT VERIFIED。

| 观测路径 | 结构证据 | 待核对用途 |
| --- | --- | --- |
| /api/[dynamic]/ebooking/home/businessData | data.cards 长度10；query 包含 dateRange、dataScope | 昨日基础经营 |
| /api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/score | data.cards 长度3 | HOS、信息分、评价分 |
| /api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/home/projectEffect/customerAcquisition | data.cards 长度4；query 包含 dateRange | 获客宝 |

这些是请求事实加响应结构支持的候选用途，不能只按名称判定 DISCOVERED。此前只筛选 me.meituan.com 因而未显示这些候选；不代表美团没有数据接口。

0.1.0 的局限：卡片 value 为字符串，已被脱敏删除；数组结构只取前三项；URL 版本段被替换为 [dynamic]；查询参数值没有保留。因此不能从此文件恢复具体业务数值、完整请求地址、昨日枚举值或实际业务状态码，也不猜测恢复值。

0.1.1 针对以上候选增加独立白名单投影：已知指标标题、最多20张卡片的有界数值字符串、单位、extAttrs 数值；仅保留 dateRange/dataScope 的有限枚举格式。常见短版本段仅在候选匹配时保留其实际值，不生成接口调用。未知标题、文本、参数值仍省略。页面统计日期和枚举含义需下轮实测核对。

下一步：用户加载0.1.1重新捕获同一数据中心昨日页；用同期页面核对卡片标题/数值、日期、排名及同行值，确认版本路径。评分更新时间尚无直接值证据。业务响应和认证值不进入 Git。当前不具备已验证的普通页 replay 条件。
