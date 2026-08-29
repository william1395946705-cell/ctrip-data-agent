# Ctrip Data Agent Repository Instructions

本文件适用于仓库全部目录。主 Agent 对需求理解、复杂度判断、技术方案、风险取舍、最终验收和用户回复负最终责任。

## Single-Agent First

主 Agent Sol 应优先自己完成任务。如果能在合理时间内直接理解、修改并验证，禁止为了流程完整而调用子 Agent。

- Level 0：文字、配置、单文件低风险修改，由 Sol 直接完成并做最小验证。
- Level 1：边界明确的大量机械执行，可由 Sol 明确验收标准后交给一个 `luna_worker`。
- Level 2：问题位置不明确、需要跨模块搜索或接口/日志调查时，可先用一个 `luna_explorer`；方案仍由 Sol 决定。
- Level 3：核心业务、认证权限、重要接口、并发、自动化核心链路或高回归风险修改，才考虑 Explorer、Worker 和 `terra_reviewer` 的完整流程。

只在任务真正独立时并行；不要让多个 Agent 重复读取相同上下文或同时修改高度相关文件。Reviewer 只审查、不修改，修复与复审最多两轮。

目标配置：Sol、Luna 和 Terra 均使用 GPT-5.6 系列，reasoning effort 为 high；当前不增加 QA Agent。

## GitHub Workflow

GitHub 仓库 `https://github.com/william1395946705-cell/ctrip-data-agent.git` 是唯一代码事实源。

除本次经用户明确授权的首次 `main` 基线初始化外，每项独立任务必须：

1. 切换到 `main`，执行 `git pull --ff-only origin main`。
2. 完整阅读本文件、`docs/PROJECT.md` 和 `docs/HANDOFF.md`。
3. 确认工作树状态，保护用户已有修改。
4. 创建 `codex/<task-name>` 独立分支。
5. 只做本任务范围内的最小修改。
6. 运行相关测试；任何必需测试失败时不得宣称完成。
7. 更新 `docs/HANDOFF.md`，提交 commit，并 push 当前分支到 GitHub。

不要把未验证的本地文件当作远程基线，不要覆盖或丢弃用户修改，不使用破坏性 Git 命令。

## Security Boundaries

- 只使用本人或明确授权的测试酒店账号。
- Cookie、Session、Authorization、Token、CSRF、账号密码和 Chrome 登录态只能在当前授权本机进程内使用。
- 上述信息不得进入源码、日志、测试报告、接口地图、扩展存储、Git 或聊天回复。
- 不读取或提交 Chrome Profile、用户数据目录、Cookie 数据库、localStorage、抓包原文、下载业务文件或运行产物。
- 不处理验证码，不绕过登录、安全验证、设备限制或平台风控，不实现浏览器指纹伪装。
- 新增文件在 `git add` 前必须做敏感信息检查；本地采集产物必须保持在 `.gitignore` 覆盖范围内。

## Silent Collector Constraints

- 不改变活动标签页 URL，不自动进入经营报告、金字塔或违约看板。
- 不调用 `bringToFront()`，不抢焦点，不模拟鼠标或键盘，不修改页面表单。
- 不主动退出登录，不清理 Cookie 或 Cache，不影响订单、价格和库存操作。
- 仅允许精确的 `https://ebooking.ctrip.com` 同源只读请求；发现请求未经人工审核不得自动执行。
- 旧 Playwright 采集器是外部对照组，不得删除、重构或破坏。

## Evidence and Completion Rules

- 构建通过、离线测试通过或代码框架存在，不等于真实 Silent 采集成功。
- 任何模块只有完成授权会话 Test A-D、保持页面 URL 不变，并与旧采集器逐字段一致后，才能标记为 verified。
- 网络失败、加载中、登录失效和真实未投流必须分开表示。
- 当前接口地图为 `unverified/disabled` 时，不得声称已可无感采集。

## Baseline Validation

```sh
PYTHONPATH=python python3 -m unittest discover -v
python3 -m compileall -q python
cd extension && npm test
```
