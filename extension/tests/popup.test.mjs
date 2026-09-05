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
    chrome: { runtime: { getManifest: () => ({ version: "0.2.1" }), sendMessage() {} } }
  });
  vm.runInContext(await readFile(new URL("../popup.js", import.meta.url), "utf8"), context);
  const state = {
    map_status: "unverified",
    last_result: { collector: {
      failed_modules: ["operating_report", "pyramid", "violation"],
      warnings: ["经营报告状态为 unverified"]
    } }
  };
  context.state = state;
  vm.runInContext("render(state)", context);
  assert.equal(element("version").textContent, "0.2.1");
  assert.equal(element("map-status").textContent, "unverified");
  assert.match(element("last-status").textContent, /^全部模块失败/);
  assert.equal(element("warnings").textContent, "经营报告状态为 unverified");
});
