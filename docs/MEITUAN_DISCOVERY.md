# 美团昨日数据接口发现

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
