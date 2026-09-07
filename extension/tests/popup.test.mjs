import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

test("弹窗直接显示版本、地图门槛和全部失败原因", async () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { textContent: "", addEventListener() {} });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: { getElementById: element },
    chrome: { runtime: { getManifest: () => ({ version: "0.2.3" }), sendMessage() {} } }
  });
  vm.runInContext(await readFile(new URL("../popup.js", import.meta.url), "utf8"), context);
  const state = {
    map_status: "unverified",
    page: { state: { hotel: { hotel_name: "测试酒店" } } },
    last_result: { collector: {
      failed_modules: [],
      warnings: ["当前页面未提供可绑定酒店 ID"],
      hotel_identity_source: "observed_order_bound",
      hotel_identity_verification: "manual_check_required",
      current_page_unchanged: true
    }, hotel: { hotel_id: "H-1", hotel_name: "测试酒店" } },
  };
  context.state = state;
  vm.runInContext("render(state)", context);
  assert.equal(element("version").textContent, "0.2.3");
  assert.equal(element("map-status").textContent, "unverified");
  assert.equal(element("last-status").textContent, "采集完成，门店需人工复核");
  assert.equal(element("warnings").textContent, "当前页面未提供可绑定酒店 ID");
  assert.equal(element("result-hotel-id").textContent, "H-1");
  assert.equal(element("identity-source").textContent, "observed_order_bound");
});
