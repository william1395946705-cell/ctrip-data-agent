const $ = (id) => document.getElementById(id);

function send(type, payload = {}) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type, ...payload }, (response) => {
      const error = chrome.runtime.lastError;
      resolve(error ? { ok: false, error: error.message } : (response || { ok: false, error: "empty_response" }));
    });
  });
}

function json(value) {
  return value === null || value === undefined ? "暂无" : JSON.stringify(value, null, 2);
}

function setStatus(message, isError = false) {
  const node = $("action-status");
  node.textContent = message || "";
  node.style.color = isError ? "#b42318" : "";
}

function render(state) {
  const page = state.page?.state || state.page;
  $("page-state").textContent = json(page ? {
    is_ebooking: page.is_ebooking,
    logged_in: page.logged_in,
    initialized: page.initialized,
    stable: page.stable,
    hotel: page.hotel,
    url: page.url,
    observed_at: page.observed_at
  } : null);
  $("map-status").textContent = `map_status: ${state.map_status || "unknown"}`;
  $("module-status").textContent = json(state.modules);
  $("last-result").textContent = json(state.last_result);
  $("diagnostics").textContent = json(state.diagnostics);
  const config = state.config || {};
  $("cooldown-minutes").value = config.cooldownMinutes ?? "";
  $("quiet-window-ms").value = config.quietWindowMs ?? "";
  $("request-timeout-ms").value = config.requestTimeoutMs ?? "";
}

async function refresh() {
  const response = await send("CTRIP_DEBUG_GET_STATE");
  if (!response.ok) return setStatus(response.error || "状态读取失败", true);
  render(response.state);
}

$("refresh").addEventListener("click", () => { void refresh(); });
$("run").addEventListener("click", async () => {
  setStatus("执行中…");
  const response = await send("CTRIP_DEBUG_RUN");
  if (!response.ok) setStatus(response.error || "执行失败", true);
  else setStatus(response.skipped ? `未执行：${response.reason}` : "已完成，正在刷新结果");
  await refresh();
});

$("save-config").addEventListener("click", async () => {
  const response = await send("CTRIP_DEBUG_UPDATE_CONFIG", { config: {
    cooldownMinutes: Number($("cooldown-minutes").value),
    quietWindowMs: Number($("quiet-window-ms").value),
    requestTimeoutMs: Number($("request-timeout-ms").value)
  } });
  if (!response.ok) setStatus(response.error || "配置保存失败", true);
  else setStatus("配置已保存");
  await refresh();
});

$("map-file").addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const map = JSON.parse(await file.text());
    const response = await send("CTRIP_DEBUG_IMPORT_MAP", { map });
    if (!response.ok) setStatus(response.error || "接口地图导入失败", true);
    else setStatus(`接口地图已导入：${response.map_status}`);
  } catch (error) {
    setStatus(`接口地图导入失败：${error.message}`, true);
  }
  event.target.value = "";
  await refresh();
});

$("clear-local").addEventListener("click", async () => {
  if (!confirm("确定清除本插件本地结果、历史、接口地图和配置吗？不会清理网页 Cookie 或缓存。")) return;
  const response = await send("CTRIP_DEBUG_CLEAR_LOCAL");
  if (!response.ok) setStatus(response.error || "清除失败", true);
  else setStatus("本插件本地结果和配置已清除");
  await refresh();
});

void refresh();
