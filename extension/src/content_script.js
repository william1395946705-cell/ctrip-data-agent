(function () {
  "use strict";

  if (location.hostname !== "ebooking.ctrip.com") return;

  let config = { quietWindowMs: 1500 };
  let quietTimer = null;
  let urlTimer = null;
  let observer = null;
  let contextInvalidated = false;
  let lastUrl = location.href;
  let lastReportedSignature = "";

  const isContextInvalidated = (error) => /extension context invalidated/i.test(
    String(error?.message || error || "")
  );

  const stop = () => {
    if (contextInvalidated) return;
    contextInvalidated = true;
    clearTimeout(quietTimer);
    quietTimer = null;
    clearInterval(urlTimer);
    urlTimer = null;
    observer?.disconnect();
    observer = null;
  };

  // Reloading an unpacked extension invalidates content-script contexts that
  // are still attached to open pages. Treat that lifecycle event as a normal
  // stop condition so the old page does not emit an uncaught console error.
  const safeRuntimeSendMessage = (message, callback) => {
    if (contextInvalidated) return false;
    try {
      chrome.runtime.sendMessage(message, (response) => {
        const runtimeError = chrome.runtime.lastError;
        if (runtimeError) {
          if (isContextInvalidated(runtimeError)) stop();
          callback?.(null, runtimeError);
          return;
        }
        callback?.(response, null);
      });
      return true;
    } catch (error) {
      if (isContextInvalidated(error)) stop();
      return false;
    }
  };

  const safeText = (element) => {
    if (!element || ["INPUT", "TEXTAREA", "SELECT", "FORM"].includes(element.tagName)) return "";
    return String(element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 200);
  };

  const firstAttribute = (selectors, attributes) => {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (!element) continue;
      for (const attribute of attributes) {
        const value = element.getAttribute(attribute);
        if (value && value.trim()) return value.trim().slice(0, 160);
      }
    }
    return "";
  };

  const firstText = (selectors) => {
    for (const selector of selectors) {
      const value = safeText(document.querySelector(selector));
      if (value) return value;
    }
    return "";
  };

  const pageState = () => {
    const hotelId = firstAttribute(
      ["[data-hotel-id]", "[data-hotelid]", "[data-hotel-code]", "[data-property-id]", "meta[name='hotel-id']", "meta[name='hotel-code']", "meta[name='property-id']"],
      ["data-hotel-id", "data-hotelid", "data-hotel-code", "data-property-id", "content"]
    );
    const hotelName = firstAttribute(
      ["[data-hotel-name]", "[data-property-name]", "meta[name='hotel-name']", "meta[name='property-name']"],
      ["data-hotel-name", "data-property-name", "content"]
    ) || firstText([
      "#he-micro-html-inline-hotel-name",
      ".he-ctrip-hotel-title-link",
      ".he-ctrip-hotel-title",
      "[data-testid='hotel-name']",
      ".hotel-name",
      ".hotelName",
      "[class*='hotel-name']",
      "[class*='hotelName']"
    ]);

    const logoutNode = document.querySelector(
      "[data-action='logout'], [data-testid='logout'], [aria-label*='退出'], [aria-label*='Logout']"
    );
    const bodySample = document.body ? String(document.body.innerText || "").slice(0, 6000) : "";
    const onLoginUrl = /(?:\/login|\/signin|passport|auth)/i.test(location.pathname);
    const hasLoginForm = Boolean(document.querySelector("input[type='password'], form[action*='login'], [data-testid*='login']"));
    const hasHotelIdentity = Boolean(hotelId || hotelName);
    const loginState = Boolean(logoutNode) || hasHotelIdentity || /退出登录|退出|登出|切换酒店|酒店管理/i.test(bodySample)
      ? true
      : (onLoginUrl || hasLoginForm || /请登录|账号登录|密码登录/i.test(bodySample) ? false : null);

    const busy = Boolean(document.querySelector(
      "[aria-busy='true'], [data-loading='true'], [data-initializing='true'], .global-loading, .page-loading"
    ));
    const initialized = document.readyState === "complete" && Boolean(document.body) && !busy;
    return {
      is_ebooking: true,
      logged_in: loginState,
      hotel: { hotel_id: hotelId, hotel_name: hotelName },
      initialized,
      stable: false,
      url: location.href,
      observed_at: new Date().toISOString()
    };
  };

  const sendState = () => {
    if (contextInvalidated) return;
    const state = pageState();
    state.stable = state.initialized;
    const signature = [state.url, state.logged_in, state.hotel.hotel_id, state.hotel.hotel_name, state.initialized].join("|");
    if (!state.initialized || signature === lastReportedSignature) return;
    lastReportedSignature = signature;
    safeRuntimeSendMessage({ type: "CTRIP_PAGE_STATE", state });
  };

  try {
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (contextInvalidated) return;
      if (message?.type === "CTRIP_READ_PAGE_STATE") {
        const state = pageState();
        state.stable = state.initialized;
        sendResponse(state);
      }
    });
  } catch (error) {
    if (isContextInvalidated(error)) stop();
    else throw error;
  }

  const scheduleStableReport = () => {
    if (contextInvalidated) return;
    clearTimeout(quietTimer);
    if (document.readyState !== "complete") return;
    quietTimer = setTimeout(sendState, Math.max(250, Number(config.quietWindowMs) || 1500));
  };

  const resetForUrl = () => {
    if (contextInvalidated) return;
    if (location.href === lastUrl) return;
    lastUrl = location.href;
    lastReportedSignature = "";
    scheduleStableReport();
  };

  const onLoad = () => scheduleStableReport();

  window.addEventListener("load", onLoad, { once: true });
  observer = new MutationObserver(() => scheduleStableReport());
  observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true });
  urlTimer = setInterval(resetForUrl, 1000);

  safeRuntimeSendMessage({ type: "CTRIP_PAGE_CONFIG_REQUEST" }, (response, error) => {
    if (contextInvalidated) return;
    if (!error && response && response.config) config = response.config;
    scheduleStableReport();
  });
  scheduleStableReport();
})();
