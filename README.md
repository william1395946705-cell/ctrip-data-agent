# Ctrip Silent Collector POC

本目录是独立技术验证工程。它不会修改、复制或替代现有“携程 eBooking 数据采集助手”、Chrome Profile、账号表、Excel、SQLite 或运行日志。

当前结论是：**代码框架与离线安全测试已完成，但真实接口地图和 Test A-D 尚未在授权登录酒店上执行，因此还不能回答“整套数据已经可以完全无感采回”。** 根目录与扩展内置的接口地图均保持 `unverified/disabled`，默认不会发起携程业务请求。

## 组成

- `python/ctrip_silent_poc/`：可附加到现有 Playwright `BrowserContext` 的被动 Network Inspector、受审核的同页 `fetch` replay、内存请求候选、Test A-D 框架、发现地图生成与旧/新逐字段比较。
- `extension/`：最小 Manifest V3 扩展，包含 Content Script、MAIN world Connector、Service Worker、本地缓存和调试页。
- `ctrip_api_map.json`：当前安全占位地图；没有真实 URL/Payload，也没有任何成功声明。
- `docs/POC_REPORT.md`：按要求整理的阶段报告和下一步验收门槛。

## 安全边界

- 只针对本人或明确授权的 eBooking 测试账号。
- 不导航、不刷新、不抢焦点、不点击、不输入、不修改表单。
- 不读取 Cookie 数据库、Profile、localStorage 或密码文件。
- 不处理验证码，不绕过登录、安全验证、设备限制或平台风控。
- Cookie、Authorization、Token、CSRF、Session 等认证值不写入源码、JSONL、接口地图、插件 storage、测试报告或终端输出。
- 被动捕获只会生成当前进程内的“待审核候选”，不会自动进入可执行 vault。只有人工逐项确认精确 URL、GET/POST、`read_only:true`，且为 POST 写明只读理由后，候选才可在本次进程中执行；进程结束即丢失。
- Python 与插件均只允许精确 `https://ebooking.ctrip.com` 同源端点，禁止 PUT/PATCH/DELETE，禁止自动跟随重定向。
- 插件只接受不含认证敏感值的地图；模块还必须经过单独的可执行地图审核并明确标为已验证、启用和只读。

## 离线验证

```sh
PYTHONPATH=python python3 -m unittest discover -v
python3 -m compileall -q python
cd extension && npm test
```

## 接入现有 BrowserManager

现有程序完成 `connect_over_cdp()` 并得到 `_context` 后，可附加被动监听：

```python
from ctrip_silent_poc import InMemoryRequestVault, NetworkInspector, attach_inspector

vault = InMemoryRequestVault()
inspector = attach_inspector(
    browser_manager,
    inspector=NetworkInspector(request_vault=vault),
    module_hint="operating_report",
)
```

这段接入只注册 `context.on("response")`；不会调用旧采集器的导航、`bring_to_front()`、点击或刷新方法。目标页自然请求完成后调用 `inspector.write_jsonl(...)` 只会写脱敏记录。

## 授权会话 Test A-D

已有本地 CDP Chrome 且员工/测试人员愿意手工完成页面状态切换时：

```sh
PYTHONPATH=python python3 -m ctrip_silent_poc.cli session \
  --cdp-url http://127.0.0.1:PORT \
  --old-result /本机路径/old_collector_result.json \
  --output-dir artifacts/local-session
```

程序只监听和同页请求。Test A 的三个目标页、Test B 首页、Test C 普通页、Test D 手工刷新均由测试人员自己完成；程序不会代替其切页或刷新。Test A 结果直接由本次会话捕获的自然响应生成，并校验三模块覆盖、酒店身份和捕获时间窗；不再接受外部 Test A 结果文件。Test A/B/C/D 只有在全部必需字段与旧采集器逐字段一致且 URL 未变化时才显示成功。

捕获结束后生成的 `ctrip_api_map.json` 是 `map_kind: "discovery"` 的发现地图，不是可直接执行的插件配置。CLI 会暂停，要求人工逐项审核；只有当前进程里与发现记录唯一匹配、被明确设为 `read_only:true` 的精确端点才可能进入 B-D。POST 还必须填写 `read_only_justification`；在尚未证明任意页面可调用时，受控 B-D 试验还必须逐端点显式设置 `controlled_silent_test:true`。该标记只允许本次人工确认的 CLI 试验，扩展自动采集仍要求模块和每个端点都明确 `can_call_from_any_ebooking_page:true`、`required_page_context:"any_ebooking_page"`。输出目录已被 `.gitignore` 排除。

发现地图必须经过受控编译，补齐无敏感值的静态 payload、字段路径和只读批准，形成独立的可执行地图。只有以下条件全部满足后，执行地图才能改为 `map_status: "verified"` 并在插件中启用：

1. Test A 自然请求与旧采集器结果一致。
2. Test B、C、D 每个必需接口都成功，且前后 URL 不变。
3. 经营报告全部 10 个原始字段与四象限一致。
4. 金字塔 7 天/30 天状态严格区分成功、明确无数据、加载、请求失败和登录失效。
5. 违约状态明确为有违约或无违约。
6. 脱敏扫描未发现认证值。

## 加载扩展

在 Chrome 扩展管理页手工“加载已解压的扩展程序”，选择 `extension/`。默认地图未验证，所以初次加载只会识别页面状态并显示调试信息，不会发送业务接口请求。真实地图导入与调试方式见 `extension/README.md`。
