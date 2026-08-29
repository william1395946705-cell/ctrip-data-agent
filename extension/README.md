# Ctrip Silent Collector POC

这是一个 Manifest V3 技术验证扩展，服务于本人或明确授权的测试酒店账号。它只匹配 `https://ebooking.ctrip.com/*`，在当前已登录标签页的 MAIN world 中使用 `fetch(credentials: "include")` 验证接口是否可以脱离特定业务页面调用。

## 明确限制

- 不导航、不点击、不输入、不调用 `bringToFront()`，也不修改当前页面表单。
- 不读取 Cookie、localStorage、Profile 或认证 header/token；浏览器只为当前页面的同源请求自动携带现有会话。
- 不处理验证码、不绕过登录/风控，不退出登录，不清理网页 Cookie/cache。
- 默认 `config/ctrip_api_map.json` 的所有模块都是 `unverified/disabled`，所以默认不会伪造采集成功。
- 请求完成前后记录 `location.href`；若页面 URL 发生变化，本次业务结果会被丢弃。

## 加载与使用

1. 在 Chrome 打开 `chrome://extensions`，开启开发者模式，选择“加载已解压的扩展程序”，选择本目录。
2. 正常登录并停留在任意 eBooking 页面。页面 `readyState=complete` 且 DOM 静默一段时间后，扩展最多按酒店键每个冷却周期执行一次。
3. 点击扩展图标打开调试页。这里可以查看当前页面状态、最近结果、模块状态、失败模块、警告和冷却配置。
4. 只有将当前授权会话捕获的接口地图整理为 JSON 并导入后，才会开启对应模块。导入会拒绝包含 cookie、authorization、token、session、csrf、password、secret 等敏感字段的地图，也只允许 `ebooking.ctrip.com` 同源端点。

## 接口地图最小形状

默认地图的 `map_kind` 是 `discovery`。Discovery 地图允许记录抓到但尚未批准的 `read_only: false` 请求，便于接口分析，但扩展永远不会调用它们。可调用地图必须是 `map_kind: "verified"`、`map_status: "verified"`，模块 `enabled: true`，并且模块及每个端点都明确 `can_call_from_any_ebooking_page: true`、`required_page_context: "any_ebooking_page"`。每个端点还须声明 `read_only: true`、`request_url`、`method` 和用于提取业务字段的 `field_paths`。端点方法仅允许 GET/POST；POST 还必须写非空 `read_only_justification`。经营报告支持 `endpoints` 数组合并多个接口；金字塔用 `periods.7d` 与 `periods.30d` 两个端点。只有声明的 ROAS/status/no_data/no_investment 字段明确为 0/暂无数据时才请求 30 天，响应中其他无关文本不会触发未投流判断。

例如（仅结构示意，不是真实携程接口）：

```json
{
  "version": 1,
  "map_kind": "verified",
  "map_status": "verified",
  "modules": {
    "operating_report": {
      "module": "operating_report",
      "enabled": true,
      "result": "verified",
      "can_call_from_any_ebooking_page": true,
      "required_page_context": "any_ebooking_page",
      "endpoints": [
        {
          "request_url": "/authorized/path",
          "method": "POST",
          "read_only": true,
          "read_only_justification": "只查询报告数据，不改变业务状态",
          "can_call_from_any_ebooking_page": true,
          "required_page_context": "any_ebooking_page",
          "payload": {"date": "declared-by-capture"},
          "field_paths": {
            "review_score": ["data.reviewScore"],
            "hotel_list_exposure": ["data.hotelExposure"],
            "comp_list_exposure": ["data.compExposure"],
            "hotel_order_conversion": ["data.hotelOrderConversion"],
            "comp_order_conversion": ["data.compOrderConversion"]
          }
        }
      ]
    }
  }
}
```

不要把真实 Cookie、Session、Authorization、Token、CSRF、密码或 Profile 文件放入地图、源码、日志或测试报告。

## 本地测试

在本目录运行：

```sh
npm test
```

测试覆盖地图敏感字段拒绝、端点限制、四象限计算、金字塔 7 天/30 天判定、登录失效状态和统一结果 schema。Chrome 的真实接口请求仍须在授权账号上按 POC Test A-D 手工验收。
