import assert from "node:assert/strict";
import { test } from "node:test";
import { connectorMain } from "../src/connector.js";

const PAGE_URL = "https://ebooking.ctrip.com/home";

function endpoint(path, extra = {}) {
  return {
    request_url: path,
    method: "GET",
    read_only: true,
    can_call_from_any_ebooking_page: true,
    required_page_context: "any_ebooking_page",
    ...extra
  };
}

function verifiedModule(endpoints) {
  return {
    enabled: true,
    result: "verified",
    can_call_from_any_ebooking_page: true,
    required_page_context: "any_ebooking_page",
    endpoints
  };
}

function operatingMap(item) {
  return {
    map_status: "verified",
    map_kind: "verified",
    modules: {
      operating_report: verifiedModule([item])
    }
  };
}

function pyramidMap(period30d) {
  return {
    map_status: "verified",
    map_kind: "verified",
    modules: {
      pyramid: {
        enabled: true,
        result: "verified",
        can_call_from_any_ebooking_page: true,
        required_page_context: "any_ebooking_page",
        periods: {
          "7d": endpoint("/api/pyramid?period=7", { field_paths: { roas: "data.roas" } }),
          "30d": period30d
        }
      }
    }
  };
}

function response(body, contentType = "application/json", status = 200) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    status,
    ok: status >= 200 && status < 300,
    url: `${PAGE_URL}/api/result`,
    type: "basic",
    headers: { get: (name) => name.toLowerCase() === "content-type" ? contentType : "" },
    text: async () => text
  };
}

function installPage() {
  const previous = globalThis.location;
  globalThis.location = {
    href: PAGE_URL,
    protocol: "https:",
    hostname: "ebooking.ctrip.com",
    origin: "https://ebooking.ctrip.com",
    port: ""
  };
  return () => {
    if (previous === undefined) delete globalThis.location;
    else globalThis.location = previous;
  };
}

test("真实端点拒绝 HTTP 200 HTML、错误业务码和不完整字段", async () => {
  const restore = installPage();
  const previous = globalThis.fetch;
  try {
    for (const body of ["<html>loading</html>", { rcode: 500, data: {} }, { rcode: 0, data: { quantity: 1 } }]) {
      globalThis.fetch = async () => response(body);
      const result = await connectorMain({ map: operatingMap(endpoint("/datacenter/api/dataCenter/sale/fetchMarketOverViewV2", { id: "operating_market_overview" })), expectedUrl: PAGE_URL });
      assert.equal(result.modules.operating_report.responses[0].status, "request_failed");
    }
    globalThis.fetch = async () => response({ rcode: 0, data: { quantity: 1, rankOfQuantity: 2, competitorNumber: 3 } });
    const result = await connectorMain({ map: operatingMap(endpoint("/datacenter/api/dataCenter/sale/fetchMarketOverViewV2", { id: "operating_market_overview" })), expectedUrl: PAGE_URL });
    assert.equal(result.modules.operating_report.responses[0].status, "success");
  } finally { globalThis.fetch = previous; restore(); }
});

test("connector 对 JSON 和纯文本响应都脱敏 auth-value", async () => {
  const restorePage = installPage();
  const previousFetch = globalThis.fetch;
  try {
    const calls = [];
    globalThis.fetch = async (url) => {
      calls.push(String(url));
      return response({
        ok: true,
        "auth-value": "JSON_AUTH_SECRET",
        nested: { Auth_Value: "NESTED_AUTH_SECRET" },
        message: "Bearer JSON_BEARER_SECRET"
      });
    };
    const jsonResult = await connectorMain({ map: operatingMap(endpoint("/api/report")), expectedUrl: PAGE_URL });
    const jsonText = JSON.stringify(jsonResult);
    assert.equal(calls.length, 1);
    assert.equal(jsonResult.modules.operating_report.responses[0].status, "success");
    assert.doesNotMatch(jsonText, /JSON_AUTH_SECRET|NESTED_AUTH_SECRET|JSON_BEARER_SECRET/);
    assert.equal(jsonResult.modules.operating_report.responses[0].data["auth-value"], undefined);

    globalThis.fetch = async () => response(
      "auth-value=TEXT_AUTH_SECRET; Bearer TEXT_BEARER_SECRET; ordinary text",
      "text/plain"
    );
    const textResult = await connectorMain({ map: operatingMap(endpoint("/api/text")), expectedUrl: PAGE_URL });
    const text = JSON.stringify(textResult);
    assert.doesNotMatch(text, /TEXT_AUTH_SECRET|TEXT_BEARER_SECRET/);
    assert.match(textResult.modules.operating_report.responses[0].data, /auth-value=\[redacted\]/i);
  } finally {
    if (previousFetch === undefined) delete globalThis.fetch;
    else globalThis.fetch = previousFetch;
    restorePage();
  }
});

test("expectedUrl 不匹配或执行期间页面变化时拒绝请求结果", async () => {
  const restorePage = installPage();
  const previousFetch = globalThis.fetch;
  try {
    let calls = 0;
    globalThis.fetch = async () => {
      calls += 1;
      return response({ ok: true });
    };
    const staleBefore = await connectorMain({
      map: operatingMap(endpoint("/api/report")),
      expectedUrl: `${PAGE_URL}/changed`
    });
    assert.equal(staleBefore.status, "page_state_stale");
    assert.equal(staleBefore.current_page_unchanged, false);
    assert.equal(calls, 0);

    globalThis.fetch = async () => {
      calls += 1;
      globalThis.location.href = `${PAGE_URL}/other-page`;
      return response({ ok: true });
    };
    const staleDuring = await connectorMain({
      map: operatingMap(endpoint("/api/report")),
      expectedUrl: PAGE_URL
    });
    assert.equal(staleDuring.status, "page_state_stale");
    assert.equal(staleDuring.current_page_unchanged, false);
    assert.equal(calls, 1);
  } finally {
    if (previousFetch === undefined) delete globalThis.fetch;
    else globalThis.fetch = previousFetch;
    restorePage();
  }
});

test("本轮 30d 不在范围时仍查询 7d，且不会误调用未批准的 30d", async () => {
  const restorePage = installPage();
  const previousFetch = globalThis.fetch;
  try {
    let calls = 0;
    globalThis.fetch = async () => {
      calls += 1;
      return response({ data: { roas: 1 } });
    };
    const result = await connectorMain({
      map: pyramidMap(endpoint("/api/pyramid?period=30", {
        read_only: false,
        can_call_from_any_ebooking_page: false
      })),
      expectedUrl: PAGE_URL
    });
    assert.equal(calls, 1);
    assert.equal(result.modules.pyramid.periods["7d"].status, "success");
    assert.equal(result.modules.pyramid.periods["30d"].status, "unverified");
    assert.equal(result.modules.pyramid.periods["30d"].error, "not_needed");
  } finally {
    if (previousFetch === undefined) delete globalThis.fetch;
    else globalThis.fetch = previousFetch;
    restorePage();
  }
});
