# Project Handoff

更新时间：2026-08-29

## 当前基线

本次工作仅建立 GitHub 仓库与项目文档基线，没有继续开发 Silent Collector 功能。默认分支为 `main`，远程仓库为 `https://github.com/william1395946705-cell/ctrip-data-agent.git`。

Git 中保存的是源码、离线测试、占位接口地图和项目文档。授权会话的抓包、结果、Chrome 登录态、Profile、账号文件、日志和业务下载文件不属于仓库基线。

## 当前已完成能力

- Python 被动 Network Inspector 和 response 捕获接入。
- 请求/响应结构脱敏、敏感 header 过滤和安全错误信息处理。
- 发现候选与人工批准的只读内存模板隔离。
- 当前页同源 request replay 及 URL 不变检查。
- 经营报告字段归一化、多接口合并和四象限计算。
- 金字塔 7 天/30 天回退及加载、失败、登录失效、无数据状态区分框架。
- 违约状态归一化。
- 旧采集器结果适配和逐字段比较框架。
- Test A-D 的 CLI/runner 框架。
- Manifest V3 扩展的 Content Script、Connector、Service Worker、本地缓存、冷却配置和调试页。
- Python 与 Node 离线测试套件。

## 尚未完成或尚未证明

- 未生成经过审核的真实携程业务接口执行地图。
- 经营报告、金字塔和违约三个模块均未通过授权账号的完整 Test A-D。
- 尚未证明三个模块能脱离各自业务页面、从任意 eBooking 页面调用。
- 尚未完成同一酒店旧采集器与 Silent Collector 的全字段真实对照。
- 当前扩展地图为 `unverified/disabled`，不会自动发送业务接口请求。

## 安全状态

- `artifacts/` 下的本机会话捕获和报告由 `.gitignore` 排除。
- Python 缓存、Node 依赖、构建产物和系统文件由 `.gitignore` 排除。
- Cookie、Token、授权状态、Chrome Profile/用户数据目录、抓包文件、日志和下载的 XLS/XLSX 由 `.gitignore` 规则覆盖。
- 含本机绝对路径的 `docs/legacy_collector_source_baseline.json` 作为本地审计元数据保留，但不进入 Git。
- 代码内存在用于验证脱敏器的伪造泄漏字符串测试样本；它们不是真实凭证。

## 基线测试

2026-08-29 首次仓库提交前已运行：

```sh
PYTHONPATH=python python3 -m unittest discover -v
python3 -m compileall -q python
cd extension && npm test
```

- Python `unittest`：19/19 通过。
- Python `compileall`：通过。
- Extension Node tests：17/17 通过。

这些结果只证明离线代码基线通过，不代表真实携程接口或 Silent Test A-D 已验证。

## 下一步建议

首次基线 push 完成后，下一项独立任务必须先从最新 `main` 创建 `codex/<task-name>` 分支。功能层面的下一步应是仅在明确授权的测试酒店会话中进行真实接口发现和 Test A-D，不得用自动切页替代 Silent 验证。
