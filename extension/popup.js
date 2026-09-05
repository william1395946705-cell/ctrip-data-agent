const $ = (id) => document.getElementById(id);

function send(type) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type }, (response) => {
      const error = chrome.runtime.lastError;
      resolve(error ? { ok: false, error: error.message } : (response || { ok: false, error: "empty_response" }));
    });
  });
}

function text(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", { hour12: false });
}

function render(state) {
  $("version").textContent = chrome.runtime.getManifest().version;
  $("map-status").textContent = text(state.map_status);
  const page = state.page?.state || state.page;
  const ready = Boolean(page?.is_ebooking && page?.logged_in === true && page?.initialized && page?.stable);
  $("page-status").textContent = ready ? "已登录并就绪" : (page?.logged_in === false ? "登录已失效" : "未识别/未就绪");
  $("hotel-name").textContent = text(page?.hotel?.hotel_name || page?.hotel?.hotel_id);
  $("page-note").textContent = ready
    ? "可在当前页面直接采集，无需进入经营报告、金字塔或违约看板。"
    : "请停留在已登录的 eBooking 页面，等待页面加载完成。";
  $("run").disabled = !ready;
  $("status-dot").className = `dot ${ready ? "ok" : "neutral"}`;

  const result = state.last_result;
  if (!result) return;
  const failed = Array.isArray(result.collector?.failed_modules) ? result.collector.failed_modules : [];
  $("last-status").textContent = failed.length ? `${failed.length === 3 ? "全部模块失败" : "部分失败"}：${failed.join("、")}` : "采集成功";
  $("warnings").textContent = (result.collector?.warnings || []).join("；");
  $("last-time").textContent = formatTime(result.collected_at);
  $("url-unchanged").textContent = result.collector?.current_page_unchanged === true ? "是" : "否";
  $("roas").textContent = text(result.pyramid?.roas_7d);
  $("violation").textContent = text(result.violation?.status);
  $("last-result").textContent = JSON.stringify(result, null, 2);
}

async function refresh() {
  const response = await send("CTRIP_DEBUG_GET_STATE");
  if (!response.ok) {
    $("action-status").textContent = response.error || "状态读取失败";
    return;
  }
  render(response.state);
}

$("run").addEventListener("click", async () => {
  $("run").disabled = true;
  $("action-status").textContent = "采集中，请保持当前页面不变…";
  const response = await send("CTRIP_DEBUG_RUN");
  if (!response.ok) {
    $("action-status").textContent = response.error || response.reason || "采集失败";
  } else if (response.skipped) {
    $("action-status").textContent = `未执行：${response.reason}`;
  } else {
    const failed = response.result?.collector?.failed_modules || [];
    $("action-status").textContent = failed.length ? `完成，但模块失败：${failed.join("、")}` : "采集完成";
  }
  await refresh();
});

void refresh();
