import {
  assertControlledTestMap,
  DEFAULT_CONFIG,
  MODULE_NAMES,
  RESULT_STATUSES,
  clampConfig,
  createCollectionResult,
  derivePyramidOutput,
  getModuleEndpoint,
  getModuleEndpoints,
  isModuleCallable,
  materializeApiMap,
  mergeOperatingSources,
  normalizePyramidPeriod,
  normalizeViolationData,
  sanitizeForStorage,
  shouldReplaceBundledMap,
  validateApiMap
} from "./core.js";
import { connectorMain } from "./connector.js";

const STORAGE_KEYS = Object.freeze({
  config: "config",
  apiMap: "apiMap",
  lastResult: "lastResult",
  history: "history",
  lastSuccessByHotel: "lastSuccessByHotel",
  pageStates: "pageStates",
  diagnostics: "diagnostics"
});

const DEFAULT_MAP_PATH = "config/ctrip_api_map.json";
const tabStates = new Map();
const inFlightHotels = new Map();
let pageStatesQueue = Promise.resolve();

const storageGet = (keys) => chrome.storage.local.get(keys);
const storageSet = (values) => chrome.storage.local.set(values);
const storageRemove = (keys) => chrome.storage.local.remove(keys);

function enqueuePageStatesMutation(mutator) {
  const operation = pageStatesQueue.catch(() => {}).then(async () => {
    const stored = await storageGet([STORAGE_KEYS.pageStates]);
    const pageStates = stored[STORAGE_KEYS.pageStates] && typeof stored[STORAGE_KEYS.pageStates] === "object"
      ? { ...stored[STORAGE_KEYS.pageStates] }
      : {};
    const next = await mutator(pageStates);
    if (next === null) await storageRemove(STORAGE_KEYS.pageStates);
    else await storageSet({ [STORAGE_KEYS.pageStates]: next });
  });
  pageStatesQueue = operation.catch(() => {});
  return operation;
}

async function loadDefaultMap() {
  const response = await fetch(chrome.runtime.getURL(DEFAULT_MAP_PATH));
  if (!response.ok) throw new Error(`默认接口地图加载失败: ${response.status}`);
  return validateApiMap(await response.json());
}

async function ensureDefaults() {
  const current = await storageGet([STORAGE_KEYS.config, STORAGE_KEYS.apiMap]);
  const values = {};
  if (!current[STORAGE_KEYS.config]) values[STORAGE_KEYS.config] = { ...DEFAULT_CONFIG };
  const bundledMap = await loadDefaultMap();
  const storedMap = current[STORAGE_KEYS.apiMap];
  const replaceBundledMap = shouldReplaceBundledMap(storedMap, bundledMap);
  if (replaceBundledMap) values[STORAGE_KEYS.apiMap] = bundledMap;
  if (Object.keys(values).length) await storageSet(values);
  return {
    config: clampConfig(current[STORAGE_KEYS.config] || values[STORAGE_KEYS.config] || DEFAULT_CONFIG),
    apiMap: values[STORAGE_KEYS.apiMap] || current[STORAGE_KEYS.apiMap]
  };
}

async function getSettings() {
  const settings = await ensureDefaults();
  const map = validateApiMap(settings.apiMap);
  assertControlledTestMap(map, await loadDefaultMap());
  return { config: settings.config, apiMap: map };
}

function hotelKey(state, tabId) {
  const hotelId = String(state?.hotel?.hotel_id || "").trim();
  if (hotelId) return `id:${hotelId}`;
  const hotelName = String(state?.hotel?.hotel_name || "").replace(/\s+/g, " ").trim().toLowerCase();
  if (hotelName) return `name:${hotelName}`;
  return `tab:${tabId}`;
}

function nowIso() {
  return new Date().toISOString();
}

function moduleRawStatus(raw) {
  const status = raw?.status;
  return RESULT_STATUSES.includes(status) ? status : "request_failed";
}

function addWarning(result, warning) {
  if (warning && !result.collector.warnings.includes(warning)) result.collector.warnings.push(warning);
}

function addFailedModule(result, moduleName) {
  if (!result.collector.failed_modules.includes(moduleName)) result.collector.failed_modules.push(moduleName);
}

function moduleUsableStatus(status) {
  return status === "success" || status === "no_data";
}

function buildResult(raw, state, apiMap) {
  const result = createCollectionResult(state.hotel, nowIso());
  result.collector.current_page_unchanged = raw?.current_page_unchanged === true;
  if (!result.collector.current_page_unchanged) {
    addWarning(result, "采集期间当前页面 URL 发生变化，已丢弃本次业务结果");
    for (const moduleName of MODULE_NAMES) addFailedModule(result, moduleName);
    return result;
  }

  const modules = raw?.modules || {};
  const operatingMap = apiMap.modules.operating_report;
  const operatingRaw = modules.operating_report;
  const operatingStatus = moduleRawStatus(operatingRaw);
  if (moduleUsableStatus(operatingStatus)) {
    const endpoints = getModuleEndpoints(operatingMap);
    const sources = Array.isArray(operatingRaw?.responses)
      ? operatingRaw.responses.map((response, index) => ({ data: response?.data, endpoint: endpoints[index] }))
      : (operatingRaw?.data !== undefined ? [{ data: operatingRaw.data, endpoint: operatingMap }] : []);
    result.operating_report = mergeOperatingSources(sources, operatingMap, { hotel: state.hotel });
    const missingFields = Object.entries(result.operating_report).filter(([key, value]) => key !== "category" && value === null).map(([key]) => key);
    if (missingFields.length) {
      addFailedModule(result, "operating_report");
      addWarning(result, `经营报告缺少字段：${missingFields.join(", ")}，请复核 response_schema`);
    }
  } else {
    addFailedModule(result, "operating_report");
    addWarning(result, `经营报告状态为 ${operatingStatus}`);
  }

  const pyramidMap = apiMap.modules.pyramid;
  const pyramidRaw = modules.pyramid || {};
  const p7Raw = pyramidRaw.periods?.["7d"] || { status: pyramidRaw.status || "request_failed" };
  const p30Raw = pyramidRaw.periods?.["30d"];
  const p7Endpoint = getModuleEndpoint(pyramidMap, "7d") || {};
  const p30Endpoint = getModuleEndpoint(pyramidMap, "30d") || {};
  const p7 = moduleRawStatus(p7Raw) === "success"
    ? normalizePyramidPeriod(p7Raw.data, p7Endpoint)
    : { status: moduleRawStatus(p7Raw), roas: null, explicit_no_data: Boolean(p7Raw.explicit_no_data) };
  const p30 = p30Raw && p30Raw.status !== "unverified" && p30Raw.error !== "not_needed"
    ? (moduleRawStatus(p30Raw) === "success"
      ? normalizePyramidPeriod(p30Raw.data, p30Endpoint)
      : { status: moduleRawStatus(p30Raw), roas: null, explicit_no_data: Boolean(p30Raw.explicit_no_data) })
    : null;
  const pyramidDerived = derivePyramidOutput(p7, p30);
  result.pyramid = pyramidDerived.output;
  for (const warning of pyramidDerived.warnings) addWarning(result, warning);
  if (!moduleUsableStatus(p7.status)) {
    addFailedModule(result, "pyramid");
    addWarning(result, `金字塔近7天状态为 ${p7.status}`);
  } else if (p7.status === "no_data" && !p7.explicit_no_data) {
    addFailedModule(result, "pyramid");
    addWarning(result, "金字塔近7天字段缺失，未将其判定为未投流");
  }
  const needs30d = (p7.status === "success" && p7.roas === 0) || (p7.status === "no_data" && p7.explicit_no_data === true);
  if (needs30d && !p30) {
    addFailedModule(result, "pyramid");
    addWarning(result, "金字塔近30天接口未验证或未返回，未判定为未投流");
  }
  if (p30 && !moduleUsableStatus(p30.status)) {
    addFailedModule(result, "pyramid");
    addWarning(result, `金字塔近30天状态为 ${p30.status}，未判定为未投流`);
  } else if (needs30d && p30 && p30.status === "no_data" && !p30.explicit_no_data) {
    addFailedModule(result, "pyramid");
    addWarning(result, "金字塔近30天字段缺失，未判定为未投流");
  }

  const violationMap = apiMap.modules.violation;
  const violationRaw = modules.violation;
  const violationStatus = moduleRawStatus(violationRaw);
  if (moduleUsableStatus(violationStatus)) {
    const endpoints = getModuleEndpoints(violationMap);
    const sources = Array.isArray(violationRaw?.responses)
      ? violationRaw.responses.map((response, index) => ({ data: response?.data, endpoint: endpoints[index] }))
      : (violationRaw?.data !== undefined ? [{ data: violationRaw.data, endpoint: violationMap }] : []);
    const statuses = sources.map((source) => normalizeViolationData(source.data, source.endpoint || violationMap)).filter((item) => item.status !== null);
    result.violation = { status: statuses[0]?.status || null };
    if (new Set(statuses.map((item) => item.status)).size > 1) {
      addFailedModule(result, "violation");
      addWarning(result, "多个违约接口返回冲突状态，请人工复核");
    }
    if (result.violation.status === null) {
      addFailedModule(result, "violation");
      addWarning(result, "违约接口成功但未匹配到明确有违约/无违约字段");
    }
  } else {
    addFailedModule(result, "violation");
    addWarning(result, `违约看板状态为 ${violationStatus}`);
  }
  return sanitizeForStorage(result);
}

function completeCollection(result, raw) {
  if (!result || result.collector.current_page_unchanged !== true) return false;
  if (result.collector.failed_modules.length) return false;
  if (!raw?.modules) return false;
  const statuses = [];
  const operating = raw.modules.operating_report;
  statuses.push(moduleRawStatus(operating));
  const pyramid = raw.modules.pyramid;
  statuses.push(moduleRawStatus(pyramid?.periods?.["7d"] || pyramid));
  if (pyramid?.periods?.["30d"]?.error !== "not_needed" && pyramid?.periods?.["30d"]?.status !== "unverified") statuses.push(moduleRawStatus(pyramid.periods["30d"]));
  statuses.push(moduleRawStatus(raw.modules.violation));
  return statuses.every(moduleUsableStatus);
}

async function persistCollection(result, hotel, raw, diagnostics) {
  const current = await storageGet([STORAGE_KEYS.history, STORAGE_KEYS.lastSuccessByHotel]);
  const history = Array.isArray(current[STORAGE_KEYS.history]) ? current[STORAGE_KEYS.history] : [];
  const settings = await getSettings();
  history.unshift(result);
  history.splice(settings.config.maxHistory);
  const values = {
    [STORAGE_KEYS.lastResult]: result,
    [STORAGE_KEYS.history]: history,
    [STORAGE_KEYS.diagnostics]: diagnostics
  };
  if (completeCollection(result, raw)) {
    const lastSuccess = current[STORAGE_KEYS.lastSuccessByHotel] && typeof current[STORAGE_KEYS.lastSuccessByHotel] === "object"
      ? current[STORAGE_KEYS.lastSuccessByHotel]
      : {};
    lastSuccess[hotel] = Date.now();
    values[STORAGE_KEYS.lastSuccessByHotel] = lastSuccess;
  }
  await storageSet(values);
}

async function sendTabMessage(tabId, message) {
  try {
    await chrome.tabs.sendMessage(tabId, message);
  } catch {
    // The tab may have navigated or the content script may not be ready.
  }
}

async function runCollection(tabId, state, source = "automatic", force = false) {
  if (!state || state.is_ebooking !== true || state.logged_in !== true || state.initialized !== true || state.stable !== true) {
    return { ok: false, skipped: true, reason: "page_not_ready_or_not_logged_in" };
  }
  if (!String(state?.hotel?.hotel_id || "").trim() && !String(state?.hotel?.hotel_name || "").trim()) {
    return { ok: false, skipped: true, reason: "hotel_identity_unavailable" };
  }
  const settings = await getSettings();
  const key = hotelKey(state, tabId);
  if (inFlightHotels.has(key)) return { ok: false, skipped: true, reason: "in_flight" };
  const current = await storageGet([STORAGE_KEYS.lastSuccessByHotel]);
  const previous = Number(current[STORAGE_KEYS.lastSuccessByHotel]?.[key] || 0);
  const cooldownMs = settings.config.cooldownMinutes * 60 * 1000;
  if (!force && cooldownMs > 0 && previous > 0 && Date.now() - previous < cooldownMs) {
    const cooldownUntil = new Date(previous + cooldownMs).toISOString();
    await sendTabMessage(tabId, { type: "CTRIP_COLLECTION_STATUS", status: "cooldown", cooldown_until: cooldownUntil });
    return { ok: true, skipped: true, reason: "cooldown", cooldown_until: cooldownUntil };
  }

  const latestState = tabStates.get(tabId) || (await storageGet([STORAGE_KEYS.pageStates])).pageStates?.[String(tabId)];
  if (!latestState || latestState.url !== state.url) {
    return { ok: false, skipped: true, reason: "page_state_stale" };
  }
  let tab;
  try {
    tab = await chrome.tabs.get(tabId);
  } catch {
    return { ok: false, skipped: true, reason: "tab_unavailable" };
  }
  let tabUrl;
  try {
    tabUrl = new URL(String(tab?.url || ""));
  } catch {
    return { ok: false, skipped: true, reason: "tab_url_unavailable" };
  }
  const exactEbookingUrl = tabUrl.protocol === "https:" && tabUrl.hostname === "ebooking.ctrip.com" && (!tabUrl.port || tabUrl.port === "443");
  if (!exactEbookingUrl || tab.url !== latestState.url || tab.url !== state.url) {
    return { ok: false, skipped: true, reason: "page_changed_before_collection" };
  }

  inFlightHotels.set(key, true);
  await sendTabMessage(tabId, { type: "CTRIP_COLLECTION_STATUS", status: "running", source });
  let raw;
  try {
    const executions = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: connectorMain,
      args: [{ map: materializeApiMap(settings.apiMap), timeoutMs: settings.config.requestTimeoutMs, expectedUrl: latestState.url }]
    });
    raw = executions?.[0]?.result || { current_page_unchanged: false, modules: {}, error: "empty_connector_result" };
  } catch (error) {
    raw = { current_page_unchanged: false, modules: {}, error: "execute_script_failed" };
  } finally {
    inFlightHotels.delete(key);
  }

  const result = buildResult(raw, state, settings.apiMap);
  if (raw.error) addWarning(result, `Connector 执行失败：${raw.error}`);
  const diagnostics = sanitizeForStorage({
    collected_at: result.collected_at,
    source,
    page_before: raw.page_before || state.url,
    page_after: raw.page_after || state.url,
    current_page_unchanged: raw.current_page_unchanged === true,
    modules: Object.fromEntries(MODULE_NAMES.map((name) => {
      const item = raw.modules?.[name];
      if (name === "pyramid") {
        return [name, {
          "7d": item?.periods?.["7d"] ? { status: item.periods["7d"].status, http_status: item.periods["7d"].http_status, content_type: item.periods["7d"].content_type } : null,
          "30d": item?.periods?.["30d"] ? { status: item.periods["30d"].status, http_status: item.periods["30d"].http_status, content_type: item.periods["30d"].content_type } : null
        }];
      }
      return [name, item ? {
        status: item.status,
        responses: Array.isArray(item.responses) ? item.responses.map((response) => ({ status: response.status, http_status: response.http_status, content_type: response.content_type })) : undefined,
        http_status: item.http_status,
        content_type: item.content_type
      } : null];
    }))
  });
  await persistCollection(result, key, raw, diagnostics);
  await sendTabMessage(tabId, { type: "CTRIP_COLLECTION_STATUS", status: "complete", result });
  return { ok: true, result, diagnostics };
}

async function handlePageState(tabId, state) {
  if (!state || state.is_ebooking !== true) return { ok: false, reason: "not_ebooking" };
  tabStates.set(tabId, state);
  await enqueuePageStatesMutation((pageStates) => {
    pageStates[String(tabId)] = sanitizeForStorage(state);
    return pageStates;
  });
  const settings = await getSettings();
  if (settings.apiMap.map_kind !== "controlled_test" && state.stable && state.initialized && state.logged_in === true) return runCollection(tabId, state, "automatic");
  return { ok: true, accepted: true, config: settings.config };
}

async function currentTrackedTab() {
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  for (const tab of tabs) {
    let state;
    try { state = await chrome.tabs.sendMessage(tab.id, { type: "CTRIP_READ_PAGE_STATE" }); } catch { continue; }
    if (state?.is_ebooking === true && state.url === tab.url) {
      tabStates.set(tab.id, state);
      return { tabId: tab.id, state };
    }
  }
  return null;
}

async function debugState() {
  const settings = await getSettings();
  const current = await storageGet([STORAGE_KEYS.lastResult, STORAGE_KEYS.history, STORAGE_KEYS.diagnostics, STORAGE_KEYS.lastSuccessByHotel]);
  const modules = Object.fromEntries(MODULE_NAMES.map((name) => {
    const module = settings.apiMap.modules[name];
    const periods = name === "pyramid" ? {
      "7d": isModuleCallable(module, settings.apiMap.map_status, "7d", settings.apiMap.map_kind),
      "30d": isModuleCallable(module, settings.apiMap.map_status, "30d", settings.apiMap.map_kind)
    } : undefined;
    return [name, { enabled: module.enabled === true, result: module.result || "unverified", callable: name === "pyramid" ? periods["7d"] : isModuleCallable(module, settings.apiMap.map_status, null, settings.apiMap.map_kind), periods }];
  }));
  return {
    config: settings.config,
    map_status: settings.apiMap.map_status,
    modules,
    page: sanitizeForStorage(await currentTrackedTab()),
    last_result: current[STORAGE_KEYS.lastResult] || null,
    diagnostics: current[STORAGE_KEYS.diagnostics] || null,
    history: current[STORAGE_KEYS.history] || [],
    last_success_by_hotel: current[STORAGE_KEYS.lastSuccessByHotel] || {}
  };
}

async function clearLocalState() {
  await pageStatesQueue.catch(() => {});
  await storageRemove([
    STORAGE_KEYS.lastResult,
    STORAGE_KEYS.history,
    STORAGE_KEYS.lastSuccessByHotel,
    STORAGE_KEYS.diagnostics,
    STORAGE_KEYS.config,
    STORAGE_KEYS.apiMap,
    STORAGE_KEYS.pageStates
  ]);
  tabStates.clear();
  inFlightHotels.clear();
  await ensureDefaults();
  return { ok: true };
}

async function handleMessage(message, sender) {
  const type = message?.type;
  if (type === "CTRIP_PAGE_CONFIG_REQUEST") {
    const settings = await getSettings();
    return { ok: true, config: settings.config };
  }
  if (type === "CTRIP_PAGE_STATE") return handlePageState(sender.tab?.id, message.state);
  if (type === "CTRIP_DEBUG_GET_STATE") return { ok: true, state: await debugState() };
  if (type === "CTRIP_DEBUG_RUN") {
    const target = await currentTrackedTab();
    if (!target) return { ok: false, error: "没有已识别的 eBooking 登录页面" };
    return runCollection(target.tabId, target.state, "manual", true);
  }
  if (type === "CTRIP_DEBUG_IMPORT_MAP") {
    const map = validateApiMap(message.map);
    assertControlledTestMap(map, await loadDefaultMap());
    await storageSet({ [STORAGE_KEYS.apiMap]: map });
    return { ok: true, map_status: map.map_status };
  }
  if (type === "CTRIP_DEBUG_UPDATE_CONFIG") {
    const config = clampConfig(message.config || {});
    await storageSet({ [STORAGE_KEYS.config]: config });
    return { ok: true, config };
  }
  if (type === "CTRIP_DEBUG_CLEAR_LOCAL") return clearLocalState();
  return { ok: false, error: "unknown_message" };
}

chrome.runtime.onInstalled.addListener(() => { void ensureDefaults(); });
chrome.runtime.onStartup.addListener(() => { void ensureDefaults(); });
chrome.tabs.onRemoved.addListener((tabId) => {
  tabStates.delete(tabId);
  void enqueuePageStatesMutation((pageStates) => {
    delete pageStates[String(tabId)];
    return pageStates;
  });
});
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender).then(sendResponse).catch((error) => sendResponse({ ok: false, error: error.message || "internal_error" }));
  return true;
});
