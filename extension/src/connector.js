/*
 * This function is intentionally self-contained: Chrome serializes `func` when
 * executeScript runs it in the page's MAIN world. It must not rely on imports or
 * extension globals. Cookies and browser-managed credentials are never read;
 * fetch(credentials: include) is the only session mechanism used here.
 */
export function connectorMain(args = {}) {
  const map = args.map || {};
  const timeoutMs = Number(args.timeoutMs) > 0 ? Number(args.timeoutMs) : 15000;
  const pageBefore = location.href;
  const expectedUrl = typeof args.expectedUrl === "string" ? args.expectedUrl : "";
  const hostAllowed = location.protocol === "https:" && location.hostname === "ebooking.ctrip.com" && (!location.port || location.port === "443");
  const sensitiveKey = (key) => /(?:cookie|authorization|proxy-authorization|set-cookie|token|session|csrf|password|passwd|secret|credential|ticket|sso|otp|signature|private[_-]?key|access[_-]?key|api[_-]?key|auth[_-]?value)/i.test(String(key));
  const loginRedirect = (url) => /(?:\/login(?:\/|$)|\/signin(?:\/|$)|passport)/i.test(String(url || ""));
  const loginSemantic = (text) => /(?:登录已失效|请登录|未登录|session\s*expired|unauthorized|login\s*required)/i.test(String(text || ""));
  const noDataText = (value) => /(?:暂无|无数据|未投流|没有投放|未投放|not\s*available|no\s*data|no\s*investment|not\s*invested)/i.test(String(value || ""));
  const redactText = (value) => {
    let output = String(value)
      .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [redacted]")
      .replace(/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b/g, "[redacted]")
      .replace(/((?:^|[^A-Za-z0-9])(?:data[-_])?(?:api[-_]?key|access[-_]?key|refresh[-_]?key|private[-_]?key|auth[-_]?value|authorization|cookie|csrf(?:[-_]?token)?|session(?:[-_]?id)?|access[-_]?token|refresh[-_]?token))(\s*[\"']?\s*[:=]\s*[\"']?)([^\"'&;,\s<}]+)/gi, "$1$2[redacted]");
    try {
      const parsed = new URL(output);
      for (const key of [...parsed.searchParams.keys()]) {
        if (sensitiveKey(key)) parsed.searchParams.set(key, "[redacted]");
      }
      parsed.hash = "";
      output = parsed.href;
    } catch {
      // Keep sanitized text when the response is not an absolute URL.
    }
    return output;
  };
  const redact = (value, depth = 0) => {
    if (depth > 8) return "[truncated]";
    if (value === null || value === undefined) return value;
    if (typeof value === "string") return redactText(value);
    if (typeof value !== "object") return value;
    if (Array.isArray(value)) return value.map((item) => redact(item, depth + 1));
    return Object.fromEntries(Object.entries(value).filter(([key]) => !sensitiveKey(key)).map(([key, child]) => [key, redact(child, depth + 1)]));
  };
  const getAt = (value, path) => {
    if (value === null || value === undefined || path === null || path === undefined) return undefined;
    const parts = Array.isArray(path) ? path : String(path).replace(/^\$\.?/, "").replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean);
    let current = value;
    for (const part of parts) {
      if (current === null || current === undefined) return undefined;
      current = current[part];
    }
    return current;
  };
  const first = (value, paths) => {
    const candidates = Array.isArray(paths) ? paths : [paths];
    for (const path of candidates) {
      const found = getAt(value, path);
      if (found !== undefined && found !== null) return found;
    }
    return undefined;
  };
  const toNumber = (value) => {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value !== "string") return null;
    if (noDataText(value)) return null;
    const match = value.replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
    return match ? Number(match[0]) : null;
  };
  const moduleEnabled = (module, endpoint) => Boolean(
    module && module.enabled === true &&
    ((map.map_status === "verified" && map.map_kind === "verified" && ["verified", "success"].includes(module.result)) ||
      (map.map_status === "controlled_test" && map.map_kind === "controlled_test" && module.result === "discovered")) &&
    module.can_call_from_any_ebooking_page === true && module.required_page_context === "any_ebooking_page" &&
    endpoint && endpoint.read_only === true &&
    endpoint.can_call_from_any_ebooking_page === true && endpoint.required_page_context === "any_ebooking_page" &&
    typeof endpoint.request_url === "string"
  );
  const endpointFor = (module, period = null) => {
    if (!module) return null;
    if (period && module.periods) return module.periods[period] || null;
    return module.endpoint || (module.request_url ? module : null);
  };
  const endpointsFor = (module) => {
    if (!module) return [];
    if (Array.isArray(module.endpoints)) return module.endpoints.filter((endpoint) => endpoint && endpoint.enabled !== false);
    const endpoint = endpointFor(module);
    return endpoint ? [endpoint] : [];
  };
  const declaredEndpointsFor = (module) => {
    if (!module) return [];
    if (Array.isArray(module.endpoints)) return module.endpoints;
    const endpoint = endpointFor(module);
    return endpoint ? [endpoint] : [];
  };
  const allowedEndpoint = (endpoint) => {
    try {
      const parsed = new URL(endpoint.request_url, location.href);
      return parsed.protocol === "https:" &&
        parsed.origin === location.origin &&
        !parsed.username && !parsed.password &&
        [...parsed.searchParams.keys()].every((key) => !sensitiveKey(key));
    } catch {
      return false;
    }
  };
  const safePayload = (value) => {
    if (value === null || value === undefined) return null;
    if (typeof value !== "object" || Array.isArray(value)) return null;
    const visit = (item) => {
      if (item === null || typeof item !== "object") return item;
      if (Array.isArray(item)) return item.map(visit);
      const output = {};
      for (const [key, child] of Object.entries(item)) {
        if (sensitiveKey(key)) return null;
        output[key] = visit(child);
      }
      return output;
    };
    return visit(value);
  };
  const requestEndpoint = async (endpoint, period = null) => {
    if (!expectedUrl || location.href !== expectedUrl) return { status: "page_state_stale", period, error: "page_state_stale" };
    if (!allowedEndpoint(endpoint)) return { status: "blocked", period, error: "endpoint_not_allowed" };
    const parsedUrl = new URL(endpoint.request_url, location.href);
    const query = endpoint.query && typeof endpoint.query === "object" ? endpoint.query : null;
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (!sensitiveKey(key) && value !== null && value !== undefined) parsedUrl.searchParams.set(key, String(value));
      }
    }
    const method = String(endpoint.method || "GET").toUpperCase();
    const headers = {};
    if (endpoint.headers && typeof endpoint.headers === "object") {
      for (const [key, value] of Object.entries(endpoint.headers)) {
        const lower = key.toLowerCase();
        if (["accept", "content-type", "x-requested-with", "x-business-line", "x-client-version", "x-platform"].includes(lower) && typeof value === "string") headers[key] = value;
      }
    }
    const options = { method, credentials: "include", redirect: "manual", headers };
    if (!["GET", "HEAD"].includes(method)) {
      const payload = safePayload(endpoint.payload ?? endpoint.body ?? endpoint.request_body);
      if (payload === null && (endpoint.payload !== undefined || endpoint.body !== undefined || endpoint.request_body !== undefined)) {
        return { status: "blocked", period, error: "payload_not_allowed" };
      }
      if (payload !== null) {
        const bodyFormat = String(endpoint.body_format || "json").toLowerCase();
        if (bodyFormat === "form") {
          options.body = new URLSearchParams(Object.entries(payload).map(([key, value]) => [key, String(value ?? "")])).toString();
          if (!Object.keys(headers).some((key) => key.toLowerCase() === "content-type")) headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8";
        } else if (bodyFormat === "json") {
          options.body = JSON.stringify(payload);
          if (!Object.keys(headers).some((key) => key.toLowerCase() === "content-type")) headers["Content-Type"] = "application/json";
        } else {
          return { status: "blocked", period, error: "body_format_not_allowed" };
        }
      }
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    options.signal = controller.signal;
    try {
      const response = await fetch(parsedUrl.href, options);
      const responseUrl = response.url || parsedUrl.href;
      const contentType = response.headers.get("content-type") || "";
      const text = await response.text();
      if (response.type === "opaqueredirect") {
        return { status: "blocked", period, http_status: response.status, response_url: responseUrl, content_type: contentType, error: "redirect_not_allowed" };
      }
      if (response.status >= 300 && response.status < 400) {
        const redirectLocation = response.headers.get("location") || "";
        const login = loginRedirect(redirectLocation) || loginRedirect(responseUrl) || loginSemantic(text);
        return { status: login ? "login_expired" : "blocked", period, http_status: response.status, response_url: responseUrl, content_type: contentType, error: "redirect_not_followed" };
      }
      if (response.status === 401 ||
        (response.status === 403 && ((response.redirected && loginRedirect(responseUrl)) || loginRedirect(responseUrl) || loginSemantic(text))) ||
        (response.redirected && loginRedirect(responseUrl))) {
        return { status: "login_expired", period, http_status: response.status, response_url: responseUrl, content_type: contentType };
      }
      if (response.status === 403 || response.status === 429) return { status: "blocked", period, http_status: response.status, response_url: responseUrl, content_type: contentType };
      if (!response.ok) return { status: "request_failed", period, http_status: response.status, response_url: responseUrl, content_type: contentType };
      let data = text;
      try { data = JSON.parse(text); } catch { /* plain text is retained only after redaction */ }
      const redactedData = redact(data);
      // Known production responses must pass business and shape checks;
      // HTTP 200 can also contain an HTML shell or an error envelope.
      if (endpoint.id) {
        const object = data && typeof data === "object" && !Array.isArray(data);
        const code = object ? (data.rcode ?? data.code) : undefined;
        if (code !== undefined && !["0", "200"].includes(String(code))) {
          return { status: loginSemantic(text) ? "login_expired" : "request_failed", period, http_status: response.status, error: "business_code_failed" };
        }
        const body = object ? data.data : null;
        const numeric = (value) => (typeof value === "number" || (typeof value === "string" && value.trim() !== "")) && Number.isFinite(Number(value));
        let valid = false;
        if (endpoint.id === "operating_advice") valid = Array.isArray(body?.badhotelAdviceEntityList) && Array.isArray(body?.goodhotelAdviceEntityList);
        if (endpoint.id === "operating_market_overview") valid = [body?.quantity, body?.rankOfQuantity, body?.competitorNumber].every(numeric);
        if (endpoint.id === "operating_scores") valid = [body?.serviceScore, body?.ctripRatingall].every(numeric);
        if (endpoint.id === "operating_flow") valid = Array.isArray(data) && data.length === 2 && data.every(row => row && row.date === endpoint.payload?.startDate && [row.listExposure, row.detailExposure, row.orderFillingNum].every(numeric));
        if (endpoint.id === "pyramid_7d") valid = Array.isArray(body?.records) && body.records.length === 1 && Number(body.totalRecords) === 1 && numeric(body.records[0]?.roas);
        // Positive samples are outside this POC: return incomplete rather than
        // promoting an untested interpretation to a successful collection.
        if (endpoint.id === "violation_list") valid = numeric(body?.totalRecords) && Number(body.totalRecords) === 0 && (body.records === null || (Array.isArray(body.records) && body.records.length === 0));
        if (!valid) return { status: "request_failed", period, http_status: response.status, error: "business_shape_or_completeness_failed" };
      }
      const paths = endpoint.field_paths || endpoint.response_schema?.field_paths || {};
      const rawRoas = first(redactedData, paths.roas ?? paths.roas_value ?? paths.value);
      const explicitNoData = Boolean(
        first(redactedData, paths.no_data ?? paths.no_investment) === true ||
        noDataText(first(redactedData, paths.no_data ?? paths.no_investment)) ||
        noDataText(first(redactedData, paths.status)) ||
        noDataText(rawRoas)
      );
      return {
        status: "success",
        period,
        http_status: response.status,
        response_url: responseUrl,
        content_type: contentType,
        data: redactedData,
        explicit_no_data: explicitNoData
      };
    } catch (error) {
      if (error && error.name === "AbortError") return { status: "loading", period, error: "timeout" };
      return { status: "request_failed", period, error: "network_error" };
    } finally {
      clearTimeout(timer);
    }
  };
  const unverified = (module, period = null) => ({ module, period, status: "unverified", error: "api_map_unverified" });
  const aggregateStatus = (responses) => {
    const statuses = responses.map((item) => item?.status || "request_failed");
    for (const status of ["login_expired", "blocked", "loading", "request_failed", "unverified"]) {
      if (statuses.includes(status)) return status;
    }
    if (statuses.length && statuses.every((status) => status === "no_data")) return "no_data";
    return statuses.length ? "success" : "unverified";
  };

  if (!expectedUrl || pageBefore !== expectedUrl) {
    return Promise.resolve({ status: "page_state_stale", page_before: pageBefore, page_after: location.href, current_page_unchanged: false, modules: {}, error: "page_state_stale" });
  }
  if (!hostAllowed) {
    return Promise.resolve({ page_before: pageBefore, page_after: location.href, current_page_unchanged: location.href === pageBefore, modules: {}, error: "not_ebooking" });
  }

  return (async () => {
    const modules = map.modules || {};
    const output = { page_before: pageBefore, page_after: pageBefore, current_page_unchanged: true, modules: {} };
    const operating = modules.operating_report;
    const operatingEndpoints = endpointsFor(operating);
    const operatingDeclared = declaredEndpointsFor(operating);
    if (operatingEndpoints.length && operatingDeclared.length === operatingEndpoints.length && operatingDeclared.every((endpoint) => endpoint.can_call_from_any_ebooking_page === true && endpoint.required_page_context === "any_ebooking_page") && operatingEndpoints.every((endpoint) => moduleEnabled(operating, endpoint))) {
      const responses = [];
      for (const endpoint of operatingEndpoints) {
        const response = await requestEndpoint(endpoint);
        responses.push(response);
        if (response.status === "page_state_stale") return { ...output, status: "page_state_stale", error: "page_state_stale", page_after: location.href, current_page_unchanged: false };
      }
      output.modules.operating_report = { module: "operating_report", status: aggregateStatus(responses), responses };
    } else {
      output.modules.operating_report = unverified("operating_report");
    }
    if (location.href !== pageBefore) {
      output.status = "page_state_stale";
      output.error = "page_state_stale";
      output.current_page_unchanged = false;
      output.page_after = location.href;
      return output;
    }

    const pyramid = modules.pyramid;
    const endpoint7d = endpointFor(pyramid, "7d");
    const endpoint30d = endpointFor(pyramid, "30d");
    // This packaged POC deliberately supports the verified 7d request without
    // silently expanding the current task to 30d. If 7d is zero/no-data and
    // 30d is unavailable, normalization fails closed instead of claiming no spend.
    const pyramidReady = moduleEnabled(pyramid, endpoint7d);
    if (!pyramidReady) {
      output.modules.pyramid = { module: "pyramid", status: "unverified", periods: { "7d": unverified("pyramid", "7d"), "30d": unverified("pyramid", "30d") } };
    } else {
      const result7d = await requestEndpoint(endpoint7d, "7d");
      const paths7d = endpoint7d.field_paths || endpoint7d.response_schema?.field_paths || {};
      const roas7d = result7d.status === "success" ? toNumber(first(result7d.data, paths7d.roas ?? paths7d.roas_value ?? paths7d.value)) : null;
      const shouldCheck30d = result7d.status === "success" && (roas7d === 0 || result7d.explicit_no_data === true);
      let result30d = { module: "pyramid", period: "30d", status: "unverified", error: "not_needed" };
      if (shouldCheck30d && moduleEnabled(pyramid, endpoint30d)) result30d = await requestEndpoint(endpoint30d, "30d");
      else if (shouldCheck30d) result30d = unverified("pyramid", "30d");
      output.modules.pyramid = { module: "pyramid", status: result7d.status, periods: { "7d": result7d, "30d": result30d } };
    }
    if (location.href !== pageBefore) {
      output.status = "page_state_stale";
      output.error = "page_state_stale";
      output.current_page_unchanged = false;
      output.page_after = location.href;
      return output;
    }

    const violation = modules.violation;
    const violationEndpoints = endpointsFor(violation);
    const violationDeclared = declaredEndpointsFor(violation);
    if (violationEndpoints.length && violationDeclared.length === violationEndpoints.length && violationDeclared.every((endpoint) => endpoint.can_call_from_any_ebooking_page === true && endpoint.required_page_context === "any_ebooking_page") && violationEndpoints.every((endpoint) => moduleEnabled(violation, endpoint))) {
      const responses = [];
      for (const endpoint of violationEndpoints) {
        const response = await requestEndpoint(endpoint);
        responses.push(response);
        if (response.status === "page_state_stale") return { ...output, status: "page_state_stale", error: "page_state_stale", page_after: location.href, current_page_unchanged: false };
      }
      output.modules.violation = { module: "violation", status: aggregateStatus(responses), responses };
    } else {
      output.modules.violation = unverified("violation");
    }
    output.page_after = location.href;
    output.current_page_unchanged = output.page_after === pageBefore;
    if (!output.current_page_unchanged) {
      output.status = "page_state_stale";
      output.error = "page_state_stale";
    }
    return output;
  })();
}
