import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import * as core from "../src/core.js";
import * as automatic from "../src/automatic.js";

test("daily limit, cooldown, next day and disabled policy", () => {
  const now = new Date(2026, 8, 7, 15);
  const record = { day: automatic.localDay(now), attemptedAt: +now, attempts: 1 };
  const policy = automatic.autoPolicy();
  assert.equal(automatic.automaticDecision(record, policy, now), "retry_cooldown");
  assert.equal(automatic.automaticDecision({ ...record, collected: true }, policy, now), "collected_today");
  assert.equal(automatic.automaticDecision({ ...record, attempts: 3 }, policy, now), "daily_limit");
  assert.equal(automatic.automaticDecision(record, policy, new Date(2026, 8, 8, 15)), "run");
  assert.equal(automatic.automaticDecision(null, { ...policy, enabled: false }, now), "disabled");
});

async function harness(storage = {}) {
  const state = { stable: true, initialized: true, logged_in: true, url: "https://ebooking.ctrip.com/home/mainland", hotel: { hotel_name: "Synthetic Hotel" } };
  let calls = 0;
  const listener = { addListener() {} };
  const context = vm.createContext({ ...core, ...automatic, connectorMain() {}, console, URL, setTimeout,
    chrome: { storage: { local: {
      async get() { return structuredClone(storage); },
      async set(values) { Object.assign(storage, structuredClone(values)); }
    } }, runtime: { onInstalled: listener, onStartup: listener, onMessage: listener },
    tabs: { onRemoved: listener, async sendMessage() { return state; } } },
    async fakeCollect() {
      calls++;
      await new Promise(resolve => setTimeout(resolve, 5));
      return { ok: true, result: { collector: { current_page_unchanged: true, failed_modules: [], hotel_identity_verification: "manual_check_required" } } };
    }
  });
  const source = (await readFile(new URL("../src/service_worker.js", import.meta.url), "utf8")).replace(/import[\s\S]*?from "\.\/[^"\n]+";/g, "");
  vm.runInContext(source + "\nexecuteCollection = fakeCollect;", context);
  return { state, storage, context, calls: () => calls, run: () => context.runCollection(1, state) };
}

test("concurrent tabs and restarted worker collect only once without promoting identity", async () => {
  const h = await harness();
  await Promise.all([h.run(), h.run(), h.run()]);
  assert.equal(h.calls(), 1);
  assert.equal(h.storage.lastSuccessByHotel, undefined);
  const restarted = await harness(h.storage);
  assert.equal((await restarted.run()).reason, "collected_today");
  assert.equal(restarted.calls(), 0);
});

test("failure reserves attempt before execution; next normal visit obeys cooldown", async () => {
  const h = await harness();
  vm.runInContext("executeCollection = async () => { throw new Error('network'); };", h.context);
  await assert.rejects(h.run(), /network/);
  assert.equal(Object.values(h.storage.automaticLedger)[0].attempts, 1);
  assert.equal((await h.run()).reason, "retry_cooldown");
});

test("hotel switch or logged-out page cannot execute queued collection", async () => {
  const h = await harness();
  const old = structuredClone(h.state);
  h.state.hotel.hotel_name = "Different Synthetic Hotel";
  assert.equal((await h.context.runCollection(1, old)).reason, "page_changed");
  h.state.logged_in = false;
  assert.equal((await h.run()).reason, "page_changed");
  assert.equal(h.calls(), 0);
});
