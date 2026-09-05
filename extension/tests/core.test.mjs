import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import {
  assertControlledTestMap,
  clampConfig,
  computeCategory,
  createCollectionResult,
  derivePyramidOutput,
  getModuleEndpoints,
  isLoginExpiredResponse,
  isModuleCallable,
  materializeApiMap,
  mergeOperatingSources,
  normalizeOperatingData,
  normalizePyramidPeriod,
  normalizeViolationData,
  sanitizeForStorage,
  shouldReplaceBundledMap,
  validateApiMap
} from "../src/core.js";

test("受控地图必须精确匹配内置审核模板，不接受自声明只读的修改", async () => {
  const bundled = validateApiMap(JSON.parse(await readFile(new URL("../config/ctrip_api_map.json", import.meta.url))));
  assert.doesNotThrow(() => assertControlledTestMap(structuredClone(bundled), bundled));
  for (const mutation of [
    endpoint => { endpoint.request_url = "/modifyInventory"; },
    endpoint => { endpoint.payload = { change: true }; },
    endpoint => { endpoint.headers = { "x-custom": "value" }; }
  ]) {
    const altered = structuredClone(bundled);
    mutation(altered.modules.operating_report.endpoints[0]);
    assert.throws(() => assertControlledTestMap(altered, bundled), /拒绝执行/);
  }
});

test("升级迁移空默认地图但保留用户自定义端点", () => {
  const bundled = { revision: 1 };
  const old = { map_kind: "discovery", map_status: "unverified", generated_at: null, modules: Object.fromEntries(["operating_report", "pyramid", "violation"].map(name => [name, { enabled: false, result: "unverified", endpoints: [], periods: { "7d": null, "30d": null } }])) };
  assert.equal(shouldReplaceBundledMap(old, bundled), true);
  const custom = structuredClone(old);
  custom.modules.operating_report.endpoints.push({ request_url: "/custom" });
  assert.equal(shouldReplaceBundledMap(custom, bundled), false);
  assert.equal(shouldReplaceBundledMap({ distribution: "bundled", revision: 1 }, bundled), false);
});

test("打包接口地图仅启用 6 个已审核同源只读查询", async () => {
  const map = JSON.parse(await readFile(new URL("../config/ctrip_api_map.json", import.meta.url)));
  const normalized = validateApiMap(map);
  assert.equal(normalized.map_status, "controlled_test");
  assert.equal(normalized.map_kind, "controlled_test");
  assert.equal(normalized.distribution, "bundled");
  const endpoints = [
    ...normalized.modules.operating_report.endpoints,
    normalized.modules.pyramid.periods["7d"],
    ...normalized.modules.violation.endpoints
  ];
  assert.equal(endpoints.length, 6);
  for (const module of Object.values(normalized.modules)) {
    assert.equal(module.enabled, true);
    assert.equal(module.result, "discovered");
    assert.equal(module.can_call_from_any_ebooking_page, true);
  }
  for (const endpoint of endpoints) {
    assert.equal(endpoint.method, "POST");
    assert.equal(endpoint.read_only, true);
    assert.equal(endpoint.can_call_from_any_ebooking_page, true);
    assert.match(endpoint.request_url, /^\//);
    assert.equal(new URL(endpoint.request_url, "https://ebooking.ctrip.com").origin, "https://ebooking.ctrip.com");
  }
  assert.equal(normalized.modules.pyramid.periods["30d"], null);
  const dated = materializeApiMap(normalized, new Date(2026, 8, 3, 12));
  assert.equal(dated.modules.operating_report.endpoints[1].payload.startDate, "2026-09-02");
  assert.equal(dated.modules.operating_report.endpoints[3].payload.endDate, "2026-09-02");
  assert.equal(dated.modules.pyramid.periods["7d"].payload.startDate, "2026-08-27");
  assert.equal(dated.modules.pyramid.periods["7d"].payload.endDate, "2026-09-02");
});

test("根接口地图记录逐端点 replay PASS 但保持 discovery/禁用", async () => {
  const map = JSON.parse(await readFile(new URL("../../ctrip_api_map.json", import.meta.url)));
  const normalized = validateApiMap(map);
  assert.equal(normalized.map_status, "discovered");
  assert.equal(normalized.map_kind, "discovery");
  assert.equal(normalized.modules.operating_report.endpoints.length, 4);
  assert.equal(normalized.modules.operating_report.result, "discovered");
  assert.equal(normalized.modules.pyramid.result, "discovered");
  assert.equal(normalized.modules.pyramid.periods["7d"].result, "discovered");
  assert.equal(normalized.modules.pyramid.periods["30d"], null);
  assert.equal(normalized.modules.violation.result, "discovered");
  assert.equal(normalized.modules.violation.endpoints.length, 1);
  for (const module of Object.values(normalized.modules)) {
    assert.equal(module.enabled, false);
    assert.equal(isModuleCallable(module, normalized.map_status, null, normalized.map_kind), false);
    const endpoints = module.module === "pyramid"
      ? Object.values(module.periods).filter(Boolean)
      : module.endpoints;
    for (const endpoint of endpoints) {
      assert.equal(endpoint.result, "discovered");
      assert.equal(endpoint.read_only, true);
      assert.equal(endpoint.controlled_silent_test, true);
      assert.equal(endpoint.required_page_context, "mixed");
      assert.equal(endpoint.can_call_from_any_ebooking_page, null);
      assert.deepEqual(
        [endpoint.silent_replay_tests.B, endpoint.silent_replay_tests.C, endpoint.silent_replay_tests.D],
        ["PASS", "PASS", "PASS"]
      );
      assert.equal(endpoint.silent_replay_tests.write_side_effect_observed, null);
      assert.equal(endpoint.silent_replay_tests.server_side_mutation_check, "NOT_MEASURED");
    }
  }
});

test("接口地图拒绝认证敏感字段和外部端点", () => {
  assert.throws(() => validateApiMap({ map_status: "verified", modules: {
    operating_report: { enabled: true, result: "verified", endpoint: { request_url: "/x", method: "GET", read_only: true, headers: { Authorization: "x" } } }
  } }), /敏感字段/);
  assert.throws(() => validateApiMap({ map_status: "verified", modules: {
    operating_report: { enabled: true, result: "verified", endpoint: { request_url: "https://example.com/x", method: "GET", read_only: true } }
  } }), /ebooking\.ctrip\.com/);
  assert.throws(() => validateApiMap({ map_status: "verified", modules: {
    operating_report: { enabled: true, result: "verified", endpoint: { request_url: "https://accounts.ctrip.com/x", method: "GET", read_only: true } }
  } }), /ebooking\.ctrip\.com/);
  assert.throws(() => validateApiMap({ map_status: "verified", modules: {
    operating_report: { enabled: true, result: "verified", endpoint: { request_url: "/x", method: "POST", read_only: true, read_only_justification: "仅查询数据", payload: { value: "Bearer abc.def.ghi" } } }
  } }), /敏感值/);
  assert.throws(() => validateApiMap({ map_status: "verified", modules: {
    operating_report: { enabled: true, result: "verified", endpoint: { request_url: "/x", method: "PUT", read_only: true } }
  } }), /只允许 GET\/POST/);
  assert.throws(() => validateApiMap({ map_status: "verified", modules: {
    operating_report: { enabled: true, result: "verified", endpoint: { request_url: "/x", method: "GET" } }
  } }), /read_only/);
  assert.throws(() => validateApiMap({ map_status: "verified", modules: {
    operating_report: { enabled: true, result: "verified", endpoint: { request_url: "/x", method: "POST", read_only: true } }
  } }), /read_only_justification/);
  assert.throws(() => validateApiMap({ map_kind: "discovery", map_status: "unverified", modules: {
    operating_report: { enabled: false, result: "unverified", endpoints: [{ request_url: "/x", method: "GET", read_only: false, note: "data-api-key=LEAK" }] }
  } }), /敏感值/);
});

test("只有明确证明任意 eBooking 页面可调用的模块和端点才能执行", () => {
  const endpoint = {
    request_url: "/report", method: "GET", read_only: true,
    can_call_from_any_ebooking_page: true, required_page_context: "any_ebooking_page"
  };
  const module = {
    enabled: true, result: "verified", endpoints: [endpoint],
    can_call_from_any_ebooking_page: true, required_page_context: "any_ebooking_page"
  };
  assert.equal(isModuleCallable(module, "verified", null, "verified"), true);
  assert.equal(isModuleCallable({ ...module, can_call_from_any_ebooking_page: false }, "verified", null, "verified"), false);
  assert.equal(isModuleCallable({ ...module, required_page_context: "specific_module_page" }, "verified", null, "verified"), false);
  assert.equal(isModuleCallable({ ...module, endpoints: [{ ...endpoint, can_call_from_any_ebooking_page: false }] }, "verified", null, "verified"), false);
  assert.equal(isModuleCallable({ ...module, endpoints: [{ ...endpoint, required_page_context: "unknown" }] }, "verified", null, "verified"), false);
});

test("已验证接口地图可以规范化并保留模块边界", () => {
  const map = validateApiMap({ map_status: "verified", modules: {
    operating_report: { enabled: true, result: "verified", endpoint: { request_url: "/report", method: "POST", read_only: true, read_only_justification: "仅查询报告数据", payload: { date: "today" }, field_paths: { review_score: "data.score" } } },
    pyramid: { enabled: true, result: "verified", periods: {
      "7d": { request_url: "/pyramid?period=7", method: "GET", read_only: true, field_paths: { roas: "data.roas" } },
      "30d": { request_url: "/pyramid?period=30", method: "GET", read_only: true, field_paths: { roas: "data.roas" } }
    } }
  } });
  assert.equal(map.modules.operating_report.endpoint.method, "POST");
  assert.equal(getModuleEndpoints(map.modules.operating_report).length, 1);
  assert.equal(map.modules.pyramid.periods["7d"].request_url, "/pyramid?period=7");
  assert.equal(map.modules.violation.enabled, false);
});

test("discovery 地图允许记录 read_only=false，但永不允许调用", () => {
  const map = validateApiMap({ map_kind: "discovery", map_status: "discovered", modules: {
    operating_report: { enabled: false, result: "discovered", endpoints: [{ request_url: "/discovered", method: "POST", read_only: false }] }
  } });
  assert.equal(map.map_status, "discovered");
  assert.equal(map.modules.operating_report.endpoints[0].read_only, false);
  assert.equal(isModuleCallable(map.modules.operating_report, map.map_status, null, map.map_kind), false);
});

test("发现地图拒绝认证、动态会话和指纹字段", () => {
  for (const key of ["auth", "authKey", "userAuth", "oneId", "fingerPrintKeys", "deviceFingerprint", "spiderkey", "x-traceID"]) {
    assert.throws(() => validateApiMap({ map_kind: "discovery", map_status: "discovered", modules: {
      operating_report: { enabled: false, result: "discovered", endpoints: [{ request_url: "/x", method: "POST", read_only: false, payload: { [key]: "LEAK" } }] }
    } }), /敏感字段/);
  }
});

test("经营报告字段和四象限按本店对竞争圈计算", () => {
  const value = normalizeOperatingData({ data: { reminder: "提高转化", rank: "3", score: "4.8", psi: "92", hotelExp: "60", compExp: "40", hotelEC: "8%", compEC: "6%", hotelOC: "5%", compOC: "7%" } }, {
    field_paths: {
      operating_reminder: "data.reminder", room_night_rank: "data.rank", review_score: "data.score", psi_score: "data.psi",
      hotel_list_exposure: "data.hotelExp", comp_list_exposure: "data.compExp", hotel_exposure_conversion: "data.hotelEC",
      comp_exposure_conversion: "data.compEC", hotel_order_conversion: "data.hotelOC", comp_order_conversion: "data.compOC"
    }
  });
  assert.equal(value.operating_reminder, "提高转化");
  assert.equal(value.room_night_rank, "3");
  assert.equal(value.hotel_exposure_conversion, 8);
  assert.equal(value.category, "高曝低转");
});

test("经营报告可合并多个接口且保持排名文本", () => {
  const module = {
    endpoints: [
      { request_url: "/a", method: "GET", field_paths: { room_night_rank: "data.rank", review_score: "data.score" } },
      { request_url: "/b", method: "GET", field_paths: { hotel_list_exposure: "data.h", comp_list_exposure: "data.c", hotel_order_conversion: "data.ho", comp_order_conversion: "data.co" } }
    ]
  };
  const value = mergeOperatingSources([
    { data: { data: { rank: "3 / 20", score: "4.8" } }, endpoint: module.endpoints[0] },
    { data: { data: { h: 10, c: 8, ho: "5%", co: "4%" } }, endpoint: module.endpoints[1] }
  ], module);
  assert.equal(value.room_night_rank, "3 / 20");
  assert.equal(value.review_score, 4.8);
  assert.equal(value.category, "高曝高转");
});

test("真实 4 接口适配器生成完整经营指标且按已核对行序计算", () => {
  const module = {
    endpoints: [
      { response_adapter: "ctrip_operating_advice_v1" },
      { response_adapter: "ctrip_operating_market_overview_v1" },
      { response_adapter: "ctrip_operating_scores_v1" },
      { response_adapter: "ctrip_operating_flow_v1", flow_row_order_confirmed: true }
    ]
  };
  const value = mergeOperatingSources([
    { data: { data: { badhotelAdviceEntityList: [{}, {}, {}] } }, endpoint: module.endpoints[0] },
    { data: { data: { quantity: 19, rankOfQuantity: 7, competitorNumber: 19 } }, endpoint: module.endpoints[1] },
    { data: { data: { serviceScore: 5.43, ctripRatingall: 4.56 } }, endpoint: module.endpoints[2] },
    { data: [
      { hotelId: "hotel", listExposure: 312, detailExposure: 58, orderFillingNum: 5 },
      { hotelId: "competition", listExposure: 481, detailExposure: 74, orderFillingNum: 8 }
    ], endpoint: module.endpoints[3] }
  ], module, { hotel: {} });
  assert.deepEqual(value, {
    operating_reminder: "经营提醒3项，需点开查看",
    departed_room_nights: 19,
    room_night_rank: "7 / 19",
    review_score: 4.56,
    psi_score: 5.43,
    hotel_list_exposure: 312,
    comp_list_exposure: 481,
    hotel_exposure_conversion: 0.1859,
    comp_exposure_conversion: 0.1538,
    hotel_order_conversion: 0.0862,
    comp_order_conversion: 0.1081,
    category: "低曝低转"
  });
});

test("金字塔仅在 7 天明确 0/暂无时使用 30 天，失败不判未投流", () => {
  const zero = derivePyramidOutput({ status: "success", roas: 0, explicit_no_data: false }, { status: "success", roas: 2, explicit_no_data: false });
  assert.deepEqual(zero.output, { roas_7d: 0, roas_30d: 2, no_investment: false });
  const noInvestment = derivePyramidOutput({ status: "no_data", roas: null, explicit_no_data: true }, { status: "success", roas: 0, explicit_no_data: false });
  assert.equal(noInvestment.output.no_investment, true);
  const failed = derivePyramidOutput({ status: "loading", roas: null, explicit_no_data: false }, null);
  assert.equal(failed.output.no_investment, false);
  assert.equal(failed.output.roas_7d, null);
  const normalized = normalizePyramidPeriod({ data: { message: "暂无数据" } }, { field_paths: { roas: "data.value" } });
  assert.equal(normalized.status, "no_data");
  assert.equal(normalized.explicit_no_data, false);
  const declared = normalizePyramidPeriod({ data: { status: "暂无数据", message: "其他提示" } }, { field_paths: { roas: "data.value", status: "data.status" } });
  assert.equal(declared.explicit_no_data, true);
});

test("违约只能由明确状态/计数输出，缺失不猜测", () => {
  assert.deepEqual(normalizeViolationData({ data: { count: 0 } }, { field_paths: { violation_count: "data.count" } }), { status: "无违约", explicit: true });
  assert.deepEqual(normalizeViolationData({ data: { status: "有违规" } }, { field_paths: { status: "data.status" } }), { status: "有违约", explicit: true });
  assert.equal(normalizeViolationData({ data: {} }, { field_paths: { status: "data.status" } }).status, null);
});

test("统一结果 schema 和认证失效状态", () => {
  const result = createCollectionResult({ hotel_id: "H1", hotel_name: "测试酒店" }, "2026-08-28T00:00:00.000Z");
  assert.equal(result.platform, "ctrip");
  assert.equal(result.collector.mode, "silent");
  assert.equal(result.collector.current_page_unchanged, true);
  assert.deepEqual(Object.keys(result.operating_report), [
    "operating_reminder", "departed_room_nights", "room_night_rank", "review_score", "psi_score", "hotel_list_exposure", "comp_list_exposure",
    "hotel_exposure_conversion", "comp_exposure_conversion", "hotel_order_conversion", "comp_order_conversion", "category"
  ]);
  assert.equal(isLoginExpiredResponse(401), true);
  assert.equal(isLoginExpiredResponse(403), false);
  assert.equal(isLoginExpiredResponse(403, "https://ebooking.ctrip.com/login", true), true);
  assert.equal(isLoginExpiredResponse(403, "https://ebooking.ctrip.com/api/report", false, "请登录后重试"), true);
  assert.equal(isLoginExpiredResponse(200, "https://ebooking.ctrip.com/login", true), true);
  assert.equal(isLoginExpiredResponse(500), false);
});

test("配置可调且有安全上限", () => {
  const config = clampConfig({ cooldownMinutes: 45, quietWindowMs: 2000, requestTimeoutMs: 30000, maxHistory: 200 });
  assert.deepEqual(config, { cooldownMinutes: 45, quietWindowMs: 2000, requestTimeoutMs: 30000, maxHistory: 100 });
  assert.equal(clampConfig({ cooldownMinutes: -1 }).cooldownMinutes, 30);
});

test("写入插件存储前脱敏 URL 查询和令牌文本", () => {
  const safe = sanitizeForStorage({
    url: "https://ebooking.ctrip.com/home?token=SECRET&date=2026-08-28#frag",
    message: "Bearer abc.def.ghi",
    csrfToken: "DROP",
    note: "data-api-key=LEAK_API auth-value:LEAK_AUTH"
  });
  assert.equal(safe.csrfToken, undefined);
  assert.doesNotMatch(JSON.stringify(safe), /SECRET|abc\.def\.ghi|LEAK_API|LEAK_AUTH|#frag/);
  assert.match(safe.url, /date=2026-08-28/);
});

test("四象限缺数据时不猜测", () => {
  assert.equal(computeCategory(1, null, 2, 1), null);
});

test("四象限相等时按高曝高转", () => {
  assert.equal(computeCategory(10, 10, 5, 5), "高曝高转");
  assert.equal(computeCategory(10, 10, 4, 5), "高曝低转");
});
