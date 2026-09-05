export const RESULT_STATUSES = Object.freeze([
  "success",
  "no_data",
  "loading",
  "request_failed",
  "login_expired",
  "blocked",
  "discovered",
  "unverified"
]);

export const MODULE_NAMES = Object.freeze([
  "operating_report",
  "pyramid",
  "violation"
]);

export const DEFAULT_CONFIG = Object.freeze({
  cooldownMinutes: 30,
  quietWindowMs: 1500,
  requestTimeoutMs: 15000,
  maxHistory: 20
});

const SENSITIVE_KEY_RE = /(?:auth|cookie|token|session|csrf|password|passwd|secret|credential|ticket|sso|otp|signature|private[_-]?key|access[_-]?key|api[_-]?key)/i;
const SENSITIVE_EXACT_KEYS = new Set([
  "clientid", "ctok", "deviceid", "fingerprintkeys", "fp", "fxpcqlniredt",
  "logid", "oneid", "pvid", "requestid", "sid", "spiderkey", "traceid",
  "vid", "xsid", "xtraceid"
]);
const SENSITIVE_ASSIGNMENT_SOURCE = String.raw`((?:^|[^A-Za-z0-9])(?:data[-_])?(?:api[-_]?key|access[-_]?key|refresh[-_]?key|private[-_]?key|auth[-_]?value|authorization|cookie|csrf(?:[-_]?token)?|session(?:[-_]?id)?|access[-_]?token|refresh[-_]?token))(\s*["']?\s*[:=]\s*["']?)([^"'&,\s<}]+)`;
const SENSITIVE_VALUE_RE = new RegExp(String.raw`(?:\bBearer\s+[A-Za-z0-9._~+/=-]+|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b|${SENSITIVE_ASSIGNMENT_SOURCE})`, "i");
const SENSITIVE_ASSIGNMENT_RE_GLOBAL = new RegExp(SENSITIVE_ASSIGNMENT_SOURCE, "gi");
const BEARER_RE_GLOBAL = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const JWT_RE_GLOBAL = /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b/g;
const LOGIN_REDIRECT_RE = /(?:\/login(?:\/|$)|\/signin(?:\/|$)|passport)/i;
const NO_DATA_RE = /(?:暂无|无数据|未投流|没有投放|未投放|not\s*available|no\s*data|no\s*investment|not\s*invested)/i;

export function isSensitiveKey(key) {
  if (typeof key !== "string") return false;
  const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, "");
  return normalized.includes("fingerprint") || SENSITIVE_EXACT_KEYS.has(normalized) || SENSITIVE_KEY_RE.test(key);
}

function assertSafeUrl(value, path) {
  if (typeof value !== "string") return;
  let parsed;
  try {
    parsed = new URL(value, "https://ebooking.ctrip.com/");
  } catch {
    throw new Error(`${path} 不是有效 URL`);
  }
  if (parsed.protocol !== "https:") {
    throw new Error(`${path} 只允许 HTTPS URL`);
  }
  if (parsed.username || parsed.password) {
    throw new Error(`${path} 不得包含 URL 凭据`);
  }
  for (const key of parsed.searchParams.keys()) {
    if (isSensitiveKey(key)) throw new Error(`${path} 查询参数疑似认证敏感字段`);
  }
  if (parsed.hostname !== "ebooking.ctrip.com") {
    throw new Error(`${path} 只能指向 ebooking.ctrip.com 同源接口`);
  }
}

export function assertNoSensitiveFields(value, path = "map") {
  if (value === null || value === undefined) return;
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertNoSensitiveFields(item, `${path}[${index}]`));
    return;
  }
  if (typeof value === "string" && SENSITIVE_VALUE_RE.test(value)) throw new Error(`${path} 疑似包含认证敏感值，拒绝导入`);
  if (typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    if (isSensitiveKey(key)) throw new Error(`${path}.${key} 疑似认证敏感字段，拒绝导入`);
    if (/^(?:request_url|url|endpoint)$/i.test(key)) assertSafeUrl(child, `${path}.${key}`);
    if (/field[_-]?path/i.test(key) && typeof child === "string" && child.split(/[.\[\]]/).some(isSensitiveKey)) {
      throw new Error(`${path}.${key} 不得读取认证敏感字段`);
    }
    assertNoSensitiveFields(child, `${path}.${key}`);
  }
}

function validEndpoint(endpoint, path, mapKind = "verified") {
  if (endpoint === null || endpoint === undefined) return null;
  if (typeof endpoint !== "object" || Array.isArray(endpoint)) throw new Error(`${path} 必须是对象`);
  if (endpoint.request_url !== undefined) assertSafeUrl(endpoint.request_url, `${path}.request_url`);
  if (endpoint.url !== undefined) assertSafeUrl(endpoint.url, `${path}.url`);
  const requestUrl = endpoint.request_url ?? endpoint.url;
  if (typeof requestUrl !== "string" || !requestUrl.trim()) throw new Error(`${path} 缺少 request_url`);
  if (typeof endpoint.method !== "string" || !endpoint.method.trim()) throw new Error(`${path}.method 必须明确声明 GET 或 POST`);
  const method = endpoint.method.toUpperCase();
  if (!['GET', 'POST'].includes(method)) throw new Error(`${path}.method 只允许 GET/POST`);
  if (endpoint.read_only !== true && !(mapKind === "discovery" && endpoint.read_only === false)) {
    throw new Error(`${path}.read_only 必须为 true`);
  }
  if (method === "POST" && endpoint.read_only === true && (typeof endpoint.read_only_justification !== "string" || !endpoint.read_only_justification.trim())) {
    throw new Error(`${path}.read_only_justification 必须是非空说明`);
  }
  return { ...endpoint, method, request_url: requestUrl };
}

function validEndpointList(endpoints, path, mapKind = "verified") {
  if (endpoints === null || endpoints === undefined) return [];
  if (!Array.isArray(endpoints)) throw new Error(`${path} 必须是数组`);
  return endpoints.map((endpoint, index) => validEndpoint(endpoint, `${path}[${index}]`, mapKind));
}

function modulesToObject(modules) {
  if (Array.isArray(modules)) {
    return Object.fromEntries(modules.map((module) => [module.module, module]));
  }
  if (!modules || typeof modules !== "object" || Array.isArray(modules)) return {};
  return modules;
}

export function validateApiMap(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("接口地图必须是 JSON 对象");
  assertNoSensitiveFields(input);
  const mapStatus = input.map_status || input.status || "unverified";
  if (!["controlled_test", "verified", "discovered", "unverified", "blocked"].includes(mapStatus)) throw new Error("无效 map_status");
  const mapKind = input.map_kind || (mapStatus === "verified" ? "verified" : "discovery");
  if (!["controlled_test", "verified", "discovery"].includes(mapKind)) throw new Error("无效 map_kind");
  const modules = modulesToObject(input.modules);
  const normalizedModules = {};
  for (const name of MODULE_NAMES) {
    const module = modules[name];
    if (!module || typeof module !== "object" || Array.isArray(module)) {
      normalizedModules[name] = { module: name, enabled: false, result: "unverified" };
      continue;
    }
    const normalized = { ...module, module: name };
    if (normalized.request_url || normalized.url) {
      normalized.endpoint = validEndpoint(normalized, `modules.${name}`, mapKind);
    } else if (normalized.endpoint) {
      normalized.endpoint = validEndpoint(normalized.endpoint, `modules.${name}.endpoint`, mapKind);
    }
    if (name === "pyramid") {
      const periods = normalized.periods || normalized.endpoints || {};
      normalized.periods = {
        "7d": validEndpoint(periods["7d"] ?? periods[7] ?? normalized.endpoint_7d, `modules.${name}.periods.7d`, mapKind),
        "30d": validEndpoint(periods["30d"] ?? periods[30] ?? normalized.endpoint_30d, `modules.${name}.periods.30d`, mapKind)
      };
    } else {
      const legacyEndpoint = normalized.endpoint ? [normalized.endpoint] : [];
      normalized.endpoints = validEndpointList(normalized.endpoints ?? legacyEndpoint, `modules.${name}.endpoints`, mapKind);
    }
    normalizedModules[name] = normalized;
  }
  return {
    version: input.version ?? 1,
    revision: input.revision ?? 0,
    distribution: input.distribution ?? null,
    release_scope: input.release_scope ?? null,
    map_kind: mapKind,
    map_status: mapStatus,
    generated_at: input.generated_at ?? null,
    modules: normalizedModules
  };
}

export function shouldReplaceBundledMap(stored, bundled) {
  if (!stored) return true;
  if (stored.distribution === "bundled") return Number(stored.revision || 0) < Number(bundled.revision || 0);
  // Only migrate the empty shipped v1 skeleton, never a user-authored map.
  if (stored.map_kind !== "discovery" || stored.map_status !== "unverified" || stored.generated_at != null) return false;
  return MODULE_NAMES.every(name => {
    const module = stored.modules?.[name];
    if (!module || module.enabled !== false || module.result !== "unverified" || module.request_url || module.endpoint) return false;
    return name === "pyramid"
      ? Object.values(module.periods || {}).every(value => value == null)
      : Array.isArray(module.endpoints) && module.endpoints.length === 0;
  });
}

export function assertControlledTestMap(map, bundled) {
  if (map.map_kind !== "controlled_test") return;
  const canonical = value => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
    return value;
  };
  if (JSON.stringify(canonical(map)) !== JSON.stringify(canonical(bundled))) {
    throw new Error("受控测试地图与内置审核模板不一致，拒绝执行");
  }
}

export function isModuleCallable(module, mapStatus = "unverified", period = null, mapKind = "verified") {
  const moduleContextApproved = module?.can_call_from_any_ebooking_page === true && module?.required_page_context === "any_ebooking_page";
  const endpointContextApproved = (endpoint) => endpoint?.can_call_from_any_ebooking_page === true && endpoint?.required_page_context === "any_ebooking_page";
  const approved = (mapStatus === "verified" && mapKind === "verified" && ["verified", "success"].includes(module?.result)) || (mapStatus === "controlled_test" && mapKind === "controlled_test" && module?.result === "discovered");
  if (!module || module.enabled !== true || !approved || !moduleContextApproved) return false;
  const endpoint = period && module.periods ? module.periods[period] : module.endpoint;
  if (period) return Boolean(endpoint && endpoint.read_only === true && endpointContextApproved(endpoint) && typeof endpoint.request_url === "string");
  const declaredEndpoints = Array.isArray(module.endpoints) ? module.endpoints : (endpoint ? [endpoint] : []);
  return getModuleEndpoints(module).length > 0 && declaredEndpoints.length > 0 && declaredEndpoints.every((item) => item.read_only === true && endpointContextApproved(item));
}

export function getModuleEndpoint(module, period = null) {
  if (!module) return null;
  if (period && module.periods) return module.periods[period] || null;
  return module.endpoint || (module.request_url ? module : null);
}

export function getModuleEndpoints(module) {
  if (!module) return [];
  if (Array.isArray(module.endpoints)) return module.endpoints.filter((endpoint) => endpoint && endpoint.enabled !== false);
  const endpoint = getModuleEndpoint(module);
  return endpoint ? [endpoint] : [];
}

export function getAtPath(value, path) {
  if (value === null || value === undefined || path === null || path === undefined) return undefined;
  if (path === "" || path === "$" || (Array.isArray(path) && path.length === 0)) return value;
  const parts = Array.isArray(path)
    ? path
    : String(path).replace(/^\$\.?/, "").replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean);
  let current = value;
  for (const part of parts) {
    if (current === null || current === undefined) return undefined;
    current = current[part];
  }
  return current;
}

export function extractFirst(value, paths) {
  const candidates = Array.isArray(paths) ? paths : [paths];
  for (const path of candidates) {
    const found = getAtPath(value, path);
    if (found !== undefined && found !== null) return found;
  }
  return undefined;
}

export function toNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const normalized = value.replace(/,/g, "").trim();
  if (!normalized || NO_DATA_RE.test(normalized)) return null;
  const match = normalized.match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
}

export function hasNoDataMarker(value, depth = 0) {
  if (depth > 4 || value === null || value === undefined) return false;
  if (typeof value === "string") return NO_DATA_RE.test(value);
  if (Array.isArray(value)) return value.some((item) => hasNoDataMarker(item, depth + 1));
  if (typeof value === "object") return Object.values(value).some((item) => hasNoDataMarker(item, depth + 1));
  return false;
}

export function computeCategory(hotelExposure, compExposure, hotelOrderConversion, compOrderConversion) {
  const values = [hotelExposure, compExposure, hotelOrderConversion, compOrderConversion].map(toNumber);
  if (values.some((value) => value === null)) return null;
  const [hotelExp, compExp, hotelOrder, compOrder] = values;
  const highExposure = hotelExp >= compExp;
  const highOrder = hotelOrder >= compOrder;
  if (highExposure && highOrder) return "高曝高转";
  if (highExposure && !highOrder) return "高曝低转";
  if (!highExposure && highOrder) return "低曝高转";
  return "低曝低转";
}

export const OPERATING_FIELDS = [
  "operating_reminder",
  "departed_room_nights",
  "room_night_rank",
  "review_score",
  "psi_score",
  "hotel_list_exposure",
  "comp_list_exposure",
  "hotel_exposure_conversion",
  "comp_exposure_conversion",
  "hotel_order_conversion",
  "comp_order_conversion"
];

function roundRatio(numerator, denominator) {
  const top = toNumber(numerator);
  const bottom = toNumber(denominator);
  if (top === null || bottom === null || bottom === 0) return top === 0 && bottom === 0 ? 0 : null;
  return Math.round((top / bottom) * 10000) / 10000;
}

function normalizeKnownOperatingResponse(data, endpoint, context) {
  const adapter = endpoint?.response_adapter;
  if (!adapter) return null;
  const output = Object.fromEntries(OPERATING_FIELDS.map((field) => [field, null]));
  if (adapter === "ctrip_operating_advice_v1") {
    const bad = getAtPath(data, "data.badhotelAdviceEntityList");
    if (!Array.isArray(bad)) return output;
    output.operating_reminder = bad.length === 0 ? "无" : `经营提醒${bad.length}项，需点开查看`;
    return output;
  }
  if (adapter === "ctrip_operating_market_overview_v1") {
    output.departed_room_nights = toNumber(getAtPath(data, "data.quantity"));
    const rank = toNumber(getAtPath(data, "data.rankOfQuantity"));
    const total = toNumber(getAtPath(data, "data.competitorNumber"));
    if (rank !== null && total !== null) output.room_night_rank = `${Math.trunc(rank)} / ${Math.trunc(total)}`;
    return output;
  }
  if (adapter === "ctrip_operating_scores_v1") {
    output.review_score = toNumber(getAtPath(data, "data.ctripRatingall"));
    output.psi_score = toNumber(getAtPath(data, "data.serviceScore"));
    return output;
  }
  if (adapter === "ctrip_operating_flow_v1") {
    if (!Array.isArray(data) || data.length !== 2) return output;
    const hotelId = String(context?.hotel?.hotel_id || "").trim().toLowerCase();
    let hotelRow = hotelId
      ? data.find((row) => String(row?.hotelId || "").trim().toLowerCase() === hotelId)
      : null;
    let compRow = hotelRow ? data.find((row) => row !== hotelRow) : null;
    if ((!hotelRow || !compRow) && endpoint.flow_row_order_confirmed === true) {
      [hotelRow, compRow] = data;
    }
    if (!hotelRow || !compRow) return output;
    output.hotel_list_exposure = toNumber(hotelRow.listExposure);
    output.comp_list_exposure = toNumber(compRow.listExposure);
    output.hotel_exposure_conversion = roundRatio(hotelRow.detailExposure, hotelRow.listExposure);
    output.comp_exposure_conversion = roundRatio(compRow.detailExposure, compRow.listExposure);
    output.hotel_order_conversion = roundRatio(hotelRow.orderFillingNum, hotelRow.detailExposure);
    output.comp_order_conversion = roundRatio(compRow.orderFillingNum, compRow.detailExposure);
    return output;
  }
  throw new Error(`未知经营报告 response_adapter: ${adapter}`);
}

export function normalizeOperatingData(data, module = {}, context = {}) {
  const adapted = normalizeKnownOperatingResponse(data, module, context);
  if (adapted) {
    adapted.category = computeCategory(
      adapted.hotel_list_exposure,
      adapted.comp_list_exposure,
      adapted.hotel_order_conversion,
      adapted.comp_order_conversion
    );
    return adapted;
  }
  const paths = module.field_paths || module.response_schema?.field_paths || {};
  const output = Object.fromEntries(OPERATING_FIELDS.map((field) => [field, null]));
  for (const field of OPERATING_FIELDS) {
    const found = extractFirst(data, paths[field]);
    if (field === "operating_reminder" || field === "room_night_rank") output[field] = found ?? null;
    else output[field] = toNumber(found);
  }
  output.category = computeCategory(
    output.hotel_list_exposure,
    output.comp_list_exposure,
    output.hotel_order_conversion,
    output.comp_order_conversion
  );
  return output;
}

export function mergeOperatingSources(sources, module = {}, context = {}) {
  const output = Object.fromEntries(OPERATING_FIELDS.map((field) => [field, null]));
  const endpoints = getModuleEndpoints(module);
  for (const [index, source] of (Array.isArray(sources) ? sources : []).entries()) {
    if (!source || source.data === undefined) continue;
    const endpoint = source.endpoint || endpoints[index] || module;
    const partial = normalizeOperatingData(source.data, endpoint, context);
    for (const field of OPERATING_FIELDS) {
      if (partial[field] !== null && partial[field] !== undefined) output[field] = partial[field];
    }
  }
  output.category = computeCategory(
    output.hotel_list_exposure,
    output.comp_list_exposure,
    output.hotel_order_conversion,
    output.comp_order_conversion
  );
  return output;
}

export function normalizeViolationData(data, module = {}) {
  const paths = module.field_paths || module.response_schema?.field_paths || {};
  const rawStatus = extractFirst(data, paths.status);
  const rawCount = extractFirst(data, paths.violation_count ?? paths.count);
  if (typeof rawStatus === "boolean") return { status: rawStatus ? "有违约" : "无违约", explicit: true };
  if (typeof rawStatus === "string") {
    if (/无违约|无违规|正常|none|clear|no\s*violation/i.test(rawStatus)) return { status: "无违约", explicit: true };
    if (/有违约|有违规|违约|违规|violation/i.test(rawStatus)) return { status: "有违约", explicit: true };
  }
  const count = toNumber(rawCount);
  if (count !== null) return { status: count > 0 ? "有违约" : "无违约", explicit: true };
  if (hasNoDataMarker(data)) return { status: null, explicit: false };
  return { status: null, explicit: false };
}

export function normalizePyramidPeriod(data, endpoint = {}) {
  const paths = endpoint.field_paths || endpoint.response_schema?.field_paths || {};
  const rawRoas = extractFirst(data, paths.roas ?? paths.roas_value ?? paths.value);
  const roas = toNumber(rawRoas);
  const explicitNoData = Boolean(
    extractFirst(data, paths.no_data ?? paths.no_investment) === true ||
    hasNoDataMarker(extractFirst(data, paths.no_data ?? paths.no_investment)) ||
    hasNoDataMarker(extractFirst(data, paths.status)) ||
    hasNoDataMarker(rawRoas)
  );
  if (roas !== null) return { status: "success", roas, explicit_no_data: explicitNoData };
  if (explicitNoData) return { status: "no_data", roas: null, explicit_no_data: true };
  return { status: "no_data", roas: null, explicit_no_data: false };
}

function usableZeroPeriod(period) {
  return Boolean(period && (period.status === "success" || period.status === "no_data") && (period.roas === 0 || period.explicit_no_data === true));
}

export function derivePyramidOutput(period7, period30) {
  const output = { roas_7d: null, roas_30d: null, no_investment: false };
  const warnings = [];
  if (period7?.status === "success" && period7.roas !== null && period7.roas > 0) {
    output.roas_7d = period7.roas;
    return { output, warnings };
  }
  if (period7?.status === "success" && period7.roas === 0) output.roas_7d = 0;
  if (period7?.status === "no_data" && period7.explicit_no_data) warnings.push("金字塔近7天明确暂无数据，已检查近30天");
  if (usableZeroPeriod(period7)) {
    if (period30?.status === "success" && period30.roas !== null && period30.roas > 0) {
      output.roas_30d = period30.roas;
      return { output, warnings };
    }
    if (period30?.status === "success" && period30.roas === 0) {
      output.roas_30d = 0;
      output.no_investment = true;
      return { output, warnings };
    }
    if (period30?.status === "no_data" && period30.explicit_no_data) {
      output.no_investment = true;
      return { output, warnings };
    }
    if (period30 && period30.status && period30.status !== "success" && period30.status !== "no_data") {
      warnings.push(`金字塔近30天状态为 ${period30.status}，未判定为未投流`);
    }
  }
  return { output, warnings };
}

export function createCollectionResult(hotel = {}, collectedAt = new Date().toISOString()) {
  return {
    platform: "ctrip",
    hotel: {
      hotel_id: hotel.hotel_id || "",
      hotel_name: hotel.hotel_name || ""
    },
    collected_at: collectedAt,
    operating_report: {
      operating_reminder: null,
      departed_room_nights: null,
      room_night_rank: null,
      review_score: null,
      psi_score: null,
      hotel_list_exposure: null,
      comp_list_exposure: null,
      hotel_exposure_conversion: null,
      comp_exposure_conversion: null,
      hotel_order_conversion: null,
      comp_order_conversion: null,
      category: null
    },
    pyramid: {
      roas_7d: null,
      roas_30d: null,
      no_investment: false
    },
    violation: {
      status: null
    },
    collector: {
      mode: "silent",
      current_page_unchanged: true,
      failed_modules: [],
      warnings: []
    }
  };
}

function localIsoDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error("无效的日期基准");
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function shiftedLocalDate(value, days) {
  const date = new Date(value);
  date.setHours(12, 0, 0, 0);
  date.setDate(date.getDate() + days);
  return localIsoDate(date);
}

function materializeEndpointDates(endpoint, now) {
  if (!endpoint || typeof endpoint !== "object" || !endpoint.date_policy) return endpoint;
  const output = { ...endpoint, payload: { ...(endpoint.payload || {}) } };
  const policy = endpoint.date_policy;
  if (!policy || typeof policy !== "object") throw new Error("date_policy 必须是对象");
  const yesterday = shiftedLocalDate(now, -1);
  if (policy.kind === "previous_day") {
    const fields = Array.isArray(policy.fields) ? policy.fields : [];
    if (!fields.length || fields.some((field) => !["startDate", "endDate"].includes(field))) {
      throw new Error("previous_day date_policy 字段不合法");
    }
    for (const field of fields) output.payload[field] = yesterday;
  } else if (policy.kind === "previous_days_closed") {
    const days = Number(policy.days);
    if (!Number.isInteger(days) || days < 1 || days > 31) throw new Error("previous_days_closed 天数不合法");
    output.payload.startDate = shiftedLocalDate(now, -days);
    output.payload.endDate = yesterday;
  } else {
    throw new Error(`未知 date_policy: ${policy.kind}`);
  }
  return output;
}

export function materializeApiMap(input, now = new Date()) {
  const map = validateApiMap(input);
  const modules = { ...map.modules };
  for (const name of MODULE_NAMES) {
    const module = modules[name];
    if (!module) continue;
    if (name === "pyramid") {
      modules[name] = {
        ...module,
        periods: {
          "7d": materializeEndpointDates(module.periods?.["7d"], now),
          "30d": materializeEndpointDates(module.periods?.["30d"], now)
        }
      };
    } else {
      modules[name] = {
        ...module,
        endpoints: (module.endpoints || []).map((endpoint) => materializeEndpointDates(endpoint, now))
      };
    }
  }
  return { ...map, modules };
}

export function isLoginExpiredResponse(status, responseUrl = "", redirected = false, responseText = "") {
  const loginSemantic = /(?:登录已失效|请登录|未登录|session\s*expired|unauthorized|login\s*required)/i.test(String(responseText || ""));
  return status === 401 ||
    (status === 403 && ((redirected && LOGIN_REDIRECT_RE.test(responseUrl)) || LOGIN_REDIRECT_RE.test(responseUrl) || loginSemantic)) ||
    (redirected && LOGIN_REDIRECT_RE.test(responseUrl));
}

function sanitizeStorageString(value, key = "") {
  let output = String(value)
    .replace(BEARER_RE_GLOBAL, "Bearer [redacted]")
    .replace(JWT_RE_GLOBAL, "[redacted]")
    .replace(SENSITIVE_ASSIGNMENT_RE_GLOBAL, "$1$2[redacted]");
  if (/(?:^|_)(?:url|request_url|response_url|page_before|page_after)$/i.test(key)) {
    try {
      const parsed = new URL(output, "https://ebooking.ctrip.com/");
      for (const name of [...parsed.searchParams.keys()]) {
        if (isSensitiveKey(name)) parsed.searchParams.set(name, "[redacted]");
      }
      parsed.hash = "";
      output = parsed.href;
    } catch {
      // Keep the already token-scrubbed text when it is not a URL.
    }
  }
  return output;
}

export function sanitizeForStorage(value, depth = 0, key = "") {
  if (depth > 8) return "[truncated]";
  if (value === null || value === undefined) return value;
  if (typeof value === "string") return sanitizeStorageString(value, key);
  if (typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map((item) => sanitizeForStorage(item, depth + 1, key));
  return Object.fromEntries(Object.entries(value).filter(([childKey]) => !isSensitiveKey(childKey)).map(([childKey, child]) => [childKey, sanitizeForStorage(child, depth + 1, childKey)]));
}

export function clampConfig(input = {}) {
  const config = {};
  for (const key of Object.keys(DEFAULT_CONFIG)) {
    const candidate = Number(input[key]);
    config[key] = Number.isFinite(candidate) && candidate >= 0 ? candidate : DEFAULT_CONFIG[key];
  }
  config.cooldownMinutes = Math.min(config.cooldownMinutes, 24 * 60);
  config.quietWindowMs = Math.min(config.quietWindowMs, 10 * 60 * 1000);
  config.requestTimeoutMs = Math.min(config.requestTimeoutMs, 120 * 1000);
  config.maxHistory = Math.min(Math.max(Math.round(config.maxHistory), 1), 100);
  return config;
}
