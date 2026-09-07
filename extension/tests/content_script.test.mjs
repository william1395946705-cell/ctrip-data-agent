import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const contentScript = await readFile(new URL("../src/content_script.js", import.meta.url), "utf8");

test("扩展重载导致上下文失效时，旧页面脚本安静停止并清理监听器", async () => {
  const timers = new Set();
  let disconnected = 0;
  let messageCount = 0;
  const runtime = {
    lastError: undefined,
    onMessage: { addListener() {} },
    sendMessage(_message, callback) {
      messageCount += 1;
      if (messageCount === 1) {
        callback({ config: { quietWindowMs: 250 } });
        return;
      }
      runtime.lastError = { message: "Extension context invalidated." };
      callback(undefined);
      runtime.lastError = undefined;
    }
  };
  const document = {
    readyState: "complete",
    body: { innerText: "退出登录" },
    documentElement: {},
    querySelector() { return null; }
  };
  const window = {
    addEventListener() {}
  };
  const context = vm.createContext({
    chrome: { runtime },
    document,
    window,
    location: { hostname: "ebooking.ctrip.com", href: "https://ebooking.ctrip.com/home/mainland", pathname: "/home/mainland" },
    MutationObserver: class {
      observe() {}
      disconnect() { disconnected += 1; }
    },
    setTimeout(callback) {
      const id = { callback, kind: "timeout" };
      timers.add(id);
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    setInterval(callback) {
      const id = { callback, kind: "interval" };
      timers.add(id);
      return id;
    },
    clearInterval(id) { timers.delete(id); },
    Date
  });

  assert.doesNotThrow(() => vm.runInContext(contentScript, context));
  assert.equal(messageCount, 1);
  assert.equal(disconnected, 0);

  // Run the deferred state report. The fake runtime reports the invalidated
  // context through lastError, matching Chrome's callback contract.
  const pending = [...timers].find(timer => timer.kind === "timeout");
  assert.ok(pending);
  assert.doesNotThrow(() => pending.callback());
  assert.equal(messageCount, 2);
  assert.equal(disconnected, 1);
  assert.equal(timers.size, 0);
});

test("非携程页面不会注册任何扩展监听器", async () => {
  let added = false;
  const context = vm.createContext({
    chrome: { runtime: { onMessage: { addListener() { added = true; } } } },
    location: { hostname: "example.com" }
  });
  assert.doesNotThrow(() => vm.runInContext(contentScript, context));
  assert.equal(added, false);
});
