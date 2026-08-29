"""Silent in-page replay and the normalized collector result."""

from __future__ import annotations

import asyncio
import datetime as _dt
import inspect
import re
import time
from dataclasses import dataclass, replace
from collections.abc import Mapping
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from .models import CollectorResult, Module, ReplayResult, ResultStatus
from .redaction import REDACTED, redact_value, safe_error, safe_headers, sanitize_url


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _page_url(page: Any) -> Optional[str]:
    try:
        value = getattr(page, "url")
        value = value() if callable(value) else value
        return sanitize_url(value) if value else None
    except Exception:
        return None


def is_ebooking_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        split = urlsplit(url)
        host = (split.hostname or "").lower()
        port = split.port
    except Exception:
        return False
    return split.scheme.lower() == "https" and host == "ebooking.ctrip.com" and port in {None, 443}


@dataclass
class PageState:
    current_url: Optional[str]
    is_ebooking: bool
    is_logged_in: Optional[bool]
    hotel_id: str = ""
    hotel_name: str = ""
    initialized: bool = False
    warnings: List[str] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_url": self.current_url,
            "is_ebooking": self.is_ebooking,
            "is_logged_in": self.is_logged_in,
            "hotel_id": self.hotel_id,
            "hotel_name": self.hotel_name,
            "initialized": self.initialized,
            "warnings": list(self.warnings or []),
        }


async def inspect_current_page(page: Any) -> PageState:
    """Read harmless DOM state only; never accesses storage or credentials."""

    url = _page_url(page)
    if not is_ebooking_url(url):
        return PageState(url, False, False, warnings=["Current page is not ebooking.ctrip.com."])
    probe = r"""
    () => {
      const body = document.body;
      const text = body ? (body.innerText || '').slice(0, 20000) : '';
      const lower = text.toLowerCase();
      const loginWords = ['登录', 'login', 'sign in'];
      const logoutWords = ['退出', '注销', 'logout', '酒店管理'];
      const hasLogin = loginWords.some(word => lower.includes(word.toLowerCase()));
      const hasLoggedIn = logoutWords.some(word => lower.includes(word.toLowerCase()));
      const idElement = document.querySelector('[data-hotel-id], [data-hotelid], [data-hotel-code], [data-property-id]');
      const nameElement = document.querySelector(
        '#he-micro-html-inline-hotel-name, .he-ctrip-hotel-title-link, .he-ctrip-hotel-title, '
        + '[data-hotel-name], [data-hotelname], [data-property-name], .hotel-name, .hotelName'
      );
      const hotelId = idElement ? String(
        idElement.getAttribute('data-hotel-id') || idElement.getAttribute('data-hotelid')
        || idElement.getAttribute('data-hotel-code') || idElement.getAttribute('data-property-id') || ''
      ) : '';
      const hotelName = nameElement ? String(nameElement.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 200) : '';
      return {
        ready: document.readyState === 'complete',
        has_body: !!body,
        has_app: !!document.querySelector('#app, #root, [id*="app"], [class*="app"]'),
        is_logged_in: (hasLoggedIn || hotelId || hotelName) ? true : (hasLogin ? false : null),
        hotel_id: hotelId,
        hotel_name: hotelName
      };
    }
    """
    try:
        value = await _maybe_await(page.evaluate(probe))
    except Exception as error:
        return PageState(url, True, None, initialized=False, warnings=["Page state probe failed: " + safe_error(error)])
    value = value if isinstance(value, Mapping) else {}
    return PageState(
        url,
        True,
        value.get("is_logged_in"),
        str(value.get("hotel_id") or "")[:200],
        str(value.get("hotel_name") or "")[:200],
        bool(value.get("ready") and value.get("has_body") and value.get("has_app")),
        [],
    )


@dataclass(frozen=True, repr=False)
class RequestTemplate:
    """Raw request template retained only in the owning process memory."""

    module: str
    url: str
    method: str = "GET"
    body: Any = None
    headers: Dict[str, str] | None = None
    trigger_page: Optional[str] = None
    variant: Optional[str] = None
    read_only: bool = False
    read_only_justification: str = ""


class InMemoryRequestVault:
    """A deliberately non-serializable holder for same-session request data.

    The vault has no JSON conversion method.  Callers should pass it directly
    to :class:`SilentCollector`; it is never written by the inspector.
    """

    def __init__(self) -> None:
        self._templates: Dict[str, RequestTemplate] = {}
        self._module_templates: Dict[str, List[RequestTemplate]] = {}

    def put_approved(self, template: RequestTemplate, *, key: Optional[str] = None) -> None:
        """Store only a human-reviewed read-only template."""

        method = str(template.method or "GET").upper()
        if not template.read_only:
            raise ValueError("Request template has not been approved as read-only.")
        if method not in {"GET", "POST"}:
            raise ValueError("Only approved GET/POST requests may be replayed.")
        if method == "POST" and not template.read_only_justification.strip():
            raise ValueError("Approved POST requests require a read-only justification.")
        if not is_ebooking_url(template.url):
            raise ValueError("Replay endpoint must use the exact eBooking HTTPS origin.")
        self._templates[key or template.module] = template
        module_key = str(template.module)
        existing = self._module_templates.setdefault(module_key, [])
        fingerprint = (template.url, method, str(template.body))
        if not any((item.url, item.method, str(item.body)) == fingerprint for item in existing):
            existing.append(template)

    def get(self, module: str) -> Optional[RequestTemplate]:
        return self._templates.get(str(module))

    def get_all(self, module: str) -> tuple[RequestTemplate, ...]:
        """Return in-memory templates for replay; callers must never serialize them."""

        return tuple(self._module_templates.get(str(module), ()))

    @staticmethod
    def candidate_from_response(module: str, response: Any, *, variant: Optional[str] = None) -> RequestTemplate:
        """Create an unapproved in-memory candidate without making it replayable."""

        request = getattr(response, "request", None)
        if callable(request):
            request = request()
        if request is None:
            request = response
        url = getattr(request, "url", "")
        url = url() if callable(url) else url
        method = getattr(request, "method", "GET")
        method = method() if callable(method) else method
        body = getattr(request, "post_data", None)
        body = body() if callable(body) else body
        headers = getattr(request, "headers", {})
        headers = headers() if callable(headers) else headers
        # Keep values in memory.  The fetch invocation uses them in this
        # process only; inspector records use safe_headers() independently.
        template = RequestTemplate(
            module=str(module),
            url=str(url),
            method=str(method or "GET").upper(),
            body=body,
            headers=_replay_headers(headers),
            variant=variant,
        )
        request_text = f"{url} {body}".lower()
        if variant is None and str(module) == Module.PYRAMID.value:
            variant = "30d" if any(token in request_text for token in ("30d", "30day", "30天", "range=30")) else "7d"
        return replace(template, variant=variant)

    def approve_candidate(
        self,
        candidate: RequestTemplate,
        *,
        read_only_justification: str,
    ) -> RequestTemplate:
        approved = replace(
            candidate,
            read_only=True,
            read_only_justification=str(read_only_justification or "").strip(),
        )
        module_key = str(approved.module)
        key = f"{module_key}_{approved.variant}" if approved.variant and module_key == Module.PYRAMID.value else module_key
        self.put_approved(approved, key=key)
        if module_key == Module.PYRAMID.value and approved.variant == "7d":
            self._templates[module_key] = approved
        return approved

    def clear(self) -> None:
        self._templates.clear()
        self._module_templates.clear()


def _replay_headers(headers: Any) -> Dict[str, str]:
    """Drop browser-managed/forbidden headers while retaining in-memory API headers."""

    if not isinstance(headers, Mapping):
        return {}
    blocked = {
        "accept-encoding", "connection", "content-length", "cookie", "host",
        "origin", "referer", "user-agent",
    }
    result: Dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).lower()
        if name in blocked or name.startswith("sec-") or name.startswith(":"):
            continue
        result[str(raw_name)] = str(raw_value)
    return result


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


FETCH_SCRIPT = """
async ({url, method, headers, body, timeoutMs}) => {
  const init = {method: method || 'GET', credentials: 'include', redirect: 'manual', headers: headers || {}};
  if (body !== null && body !== undefined && method !== 'GET' && method !== 'HEAD') {
    init.body = typeof body === 'string' ? body : JSON.stringify(body);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(1, Number(timeoutMs) || 15000));
  init.signal = controller.signal;
  try {
    const response = await fetch(url, init);
    const contentType = response.headers.get('content-type') || '';
    const text = await response.text();
    let data = text;
    if (contentType.toLowerCase().includes('json')) {
      try { data = JSON.parse(text); } catch (_) { data = text; }
    }
    return {
      status: response.status,
      url: response.url,
      redirected: response.redirected,
      responseType: response.type,
      location: response.headers.get('location') || '',
      contentType,
      data
    };
  } finally {
    clearTimeout(timer);
  }
}
"""


def _text_for_signals(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, Mapping):
        return " ".join(_text_for_signals(k) + " " + _text_for_signals(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(_text_for_signals(v) for v in value)
    return str(value or "").lower()


def _contains_signal(value: Any, signals: Iterable[str]) -> bool:
    text = _text_for_signals(value)
    return any(signal.lower() in text for signal in signals)


def _is_explicit_no_data(value: Any) -> bool:
    if value is None or value in ([], ""):
        return False
    if isinstance(value, Mapping):
        # An explicit message, count, or declared collection is needed.
        # Empty bodies are not proof of no investment; they can be loading or
        # a schema mismatch.
        if not value:
            return False
        if _contains_signal(value, ("暂无数据", "没有数据", "无数据", "no data", "no records", "empty")):
            return True
        for key in ("total", "count", "totalCount", "recordCount"):
            if key in value and value[key] == 0:
                return True
        for key in ("list", "items", "records"):
            if key in value and value[key] in (None, [], ""):
                return True
    return False


def _business_ok(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return True
    for key in ("success", "ok", "isSuccess"):
        if key in value and value[key] is False:
            return False
    # Only unambiguously non-zero numeric error codes count as business error;
    # many Ctrip APIs use 0 or the string "0" for success.
    for key in ("errorCode", "errCode"):
        if key in value and str(value[key]) not in {"0", "200", "", "None", "null"}:
            return False
    return True


def classify_replay_response(module: str, response: Any, *, error: Any = None) -> ReplayResult:
    """Map transport/business state without conflating failures and no-data."""

    if error is not None:
        return ReplayResult(module, ResultStatus.REQUEST_FAILED, error=safe_error(error), http_ok=False, business_ok=False)
    if not isinstance(response, Mapping):
        return ReplayResult(module, ResultStatus.REQUEST_FAILED, error="Malformed fetch result.")
    raw_status = response.get("status")
    try:
        http_status = int(raw_status)
    except (TypeError, ValueError):
        http_status = None
    content_type = str(response.get("contentType") or "")
    response_url = sanitize_url(response.get("url")) if response.get("url") else None
    redirected = bool(response.get("redirected"))
    response_type = str(response.get("responseType") or "")
    redirect_location = sanitize_url(response.get("location")) if response.get("location") else ""
    data = redact_value(response.get("data"))
    if response_type == "opaqueredirect" or (http_status is not None and 300 <= http_status < 400) or redirected:
        target = redirect_location or response_url or ""
        status = ResultStatus.LOGIN_EXPIRED if re.search(r"/(?:login|signin)(?:/|$)|passport", target, re.I) else ResultStatus.BLOCKED
        return ReplayResult(module, status, http_status, content_type, response_url, data, error="Redirect was not followed.", http_ok=False, business_ok=False, redirected=True)
    login_url = bool(response_url and re.search(r"/(?:login|signin)(?:/|$)|passport", response_url, re.I))
    if http_status in {401} or (redirected and login_url) or _contains_signal(data, ("登录已失效", "请先登录", "login required", "session expired", "未登录")):
        return ReplayResult(module, ResultStatus.LOGIN_EXPIRED, http_status, content_type, response_url, data, http_ok=False, business_ok=False)
    if http_status in {403, 429} or _contains_signal(data, ("captcha", "验证码", "access denied", "blocked", "forbidden", "风控")):
        return ReplayResult(module, ResultStatus.BLOCKED, http_status, content_type, response_url, data, http_ok=False, business_ok=False)
    http_ok = http_status is not None and 200 <= http_status < 300
    if not http_ok:
        return ReplayResult(module, ResultStatus.REQUEST_FAILED, http_status, content_type, response_url, data, http_ok=False, business_ok=False)
    if http_status in {202, 204} or _contains_signal(data, ("loading", "pending", "加载中", "查询中", "处理中")):
        return ReplayResult(module, ResultStatus.LOADING, http_status, content_type, response_url, data, http_ok=True, business_ok=False)
    business_ok = _business_ok(data)
    if not business_ok:
        return ReplayResult(module, ResultStatus.REQUEST_FAILED, http_status, content_type, response_url, data, http_ok=True, business_ok=False)
    if module != Module.PYRAMID.value and _is_explicit_no_data(data):
        return ReplayResult(module, ResultStatus.NO_DATA, http_status, content_type, response_url, data, http_ok=True, business_ok=True)
    return ReplayResult(module, ResultStatus.SUCCESS, http_status, content_type, response_url, data, http_ok=True, business_ok=True)


async def replay_request(page: Any, template: RequestTemplate, *, before_url: Optional[str] = None, timeout_ms: int = 15_000) -> ReplayResult:
    """Fetch a captured endpoint in the current tab's JS context."""

    before = before_url if before_url is not None else _page_url(page)
    method = str(template.method or "GET").upper()
    block_reason = None
    if not is_ebooking_url(template.url):
        block_reason = "Replay endpoint is outside the exact eBooking HTTPS origin."
    elif method not in {"GET", "POST"}:
        block_reason = "Replay method is not an approved GET/POST."
    elif not template.read_only:
        block_reason = "Replay template has not been approved as read-only."
    elif method == "POST" and not template.read_only_justification.strip():
        block_reason = "Approved POST replay lacks a read-only justification."
    if block_reason:
        return ReplayResult(
            template.module,
            ResultStatus.BLOCKED,
            error=block_reason,
            current_page_url_before=before,
            current_page_url_after=before,
            current_page_unchanged=True,
            elapsed_ms=0,
        )
    args = {
        "url": template.url,
        "method": method,
        "headers": template.headers or {},
        "body": template.body,
        "timeoutMs": max(1, int(timeout_ms)),
    }
    started = time.monotonic()
    try:
        raw = await _maybe_await(page.evaluate(FETCH_SCRIPT, args))
        outcome = classify_replay_response(template.module, raw)
    except Exception as error:
        outcome = classify_replay_response(template.module, None, error=error)
    after = _page_url(page)
    outcome.current_page_url_before = before
    outcome.current_page_url_after = after
    outcome.current_page_unchanged = before == after
    outcome.elapsed_ms = int((time.monotonic() - started) * 1000)
    if not outcome.current_page_unchanged:
        outcome.warnings.append("Current page URL changed during replay.")
    return outcome


def _norm_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(key).lower())


def find_first(value: Any, aliases: Iterable[str]) -> Any:
    wanted = {_norm_key(alias) for alias in aliases}
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _norm_key(key) in wanted:
                return child
        for child in value.values():
            found = find_first(child, wanted)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = find_first(child, wanted)
            if found is not None:
                return found
    return None


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                pass
    return None


def normalize_operating(data: Any) -> Dict[str, Any]:
    aliases = {
        "operating_reminder": ("operating_reminder", "operatingReminder", "经营提醒", "reminder"),
        "room_night_rank": ("room_night_rank", "roomNightRank", "昨日离店间夜竞争圈排名", "rank"),
        "review_score": ("review_score", "reviewScore", "点评分", "点评分"),
        "psi_score": ("psi_score", "psiScore", "PSI分", "psi"),
        "hotel_list_exposure": ("hotel_list_exposure", "hotelListExposure", "本店列表页曝光"),
        "comp_list_exposure": ("comp_list_exposure", "compListExposure", "竞争圈列表页曝光"),
        "hotel_exposure_conversion": ("hotel_exposure_conversion", "hotelExposureConversion", "本店曝光转化率"),
        "comp_exposure_conversion": ("comp_exposure_conversion", "compExposureConversion", "竞争圈曝光转化率"),
        "hotel_order_conversion": ("hotel_order_conversion", "hotelOrderConversion", "本店下单转化率"),
        "comp_order_conversion": ("comp_order_conversion", "compOrderConversion", "竞争圈下单转化率"),
    }
    output = {key: find_first(data, names) for key, names in aliases.items()}
    return output


def normalize_violation(data: Any) -> Optional[str]:
    value = find_first(data, ("status", "violationStatus", "违约状态", "是否违约", "hasViolation", "有违约", "violation"))
    if isinstance(value, bool):
        return "有违约" if value else "无违约"
    if value is None:
        if _contains_signal(data, ("无违约", "no violation", "no breach")):
            return "无违约"
        if _contains_signal(data, ("有违约", "violation", "breach")):
            return "有违约"
        return None
    text = str(value).lower()
    if any(token in text for token in ("无", "none", "no", "false", "正常")):
        return "无违约"
    if any(token in text for token in ("有", "yes", "true", "violation", "breach", "违约")):
        return "有违约"
    return None


def _template_one(templates: Any, key: str) -> Optional[RequestTemplate]:
    getter = getattr(templates, "get", None)
    if not callable(getter):
        return None
    value = getter(key)
    if isinstance(value, RequestTemplate):
        return value
    if isinstance(value, (list, tuple)):
        return next((item for item in value if isinstance(item, RequestTemplate)), None)
    return None


def _template_many(templates: Any, key: str) -> tuple[RequestTemplate, ...]:
    get_all = getattr(templates, "get_all", None)
    if callable(get_all):
        values = get_all(key)
        if values:
            return tuple(item for item in values if isinstance(item, RequestTemplate))
    getter = getattr(templates, "get", None)
    value = getter(key) if callable(getter) else None
    if isinstance(value, RequestTemplate):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, RequestTemplate))
    return ()


class SilentCollector:
    """Collect modules through in-page fetch, with configurable cooldown."""

    def __init__(
        self,
        *,
        cooldown_seconds: int = 30 * 60,
        stability_delay_seconds: float = 0.0,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self.stability_delay_seconds = max(0.0, float(stability_delay_seconds))
        self._now = now or time.time
        self._last_success_by_hotel: Dict[str, float] = {}

    def can_collect(self, hotel_id: str) -> bool:
        last = self._last_success_by_hotel.get(str(hotel_id))
        return last is None or self._now() - last >= self.cooldown_seconds

    async def collect(
        self,
        page: Any,
        templates: Any,
        *,
        hotel: Optional[Mapping[str, Any]] = None,
        force: bool = False,
    ) -> CollectorResult:
        state = await inspect_current_page(page)
        result = CollectorResult(collected_at=_now_iso())
        if hotel:
            result.hotel.update({key: str(value or "")[:200] for key, value in hotel.items() if key in {"hotel_id", "hotel_name"}})
        else:
            result.hotel.update({"hotel_id": state.hotel_id, "hotel_name": state.hotel_name})
        if not state.is_ebooking:
            result.collector["failed_modules"] = [module.value for module in (Module.OPERATING_REPORT, Module.PYRAMID, Module.VIOLATION)]
            result.collector["warnings"].append("Current page is not an eBooking page.")
            return result
        if state.is_logged_in is False:
            result.collector["failed_modules"] = [module.value for module in (Module.OPERATING_REPORT, Module.PYRAMID, Module.VIOLATION)]
            result.collector["warnings"].append("Current eBooking page is not logged in.")
            return result
        if not state.initialized:
            result.collector["failed_modules"] = [module.value for module in (Module.OPERATING_REPORT, Module.PYRAMID, Module.VIOLATION)]
            result.collector["warnings"].append("Current eBooking page has not completed initialization.")
            return result
        if not str(result.hotel.get("hotel_id") or "").strip() and not str(result.hotel.get("hotel_name") or "").strip():
            result.collector["failed_modules"] = [module.value for module in (Module.OPERATING_REPORT, Module.PYRAMID, Module.VIOLATION)]
            result.collector["warnings"].append("Current hotel identity could not be confirmed from the page DOM.")
            return result
        hotel_id = str(result.hotel.get("hotel_id") or "unknown")
        if not force and not self.can_collect(hotel_id):
            result.collector["warnings"].append(f"Cooldown active for hotel {hotel_id}; collection skipped.")
            return result
        if self.stability_delay_seconds:
            await asyncio.sleep(self.stability_delay_seconds)
        before = _page_url(page)
        outcomes: Dict[str, ReplayResult] = {}

        operating_templates = _template_many(templates, Module.OPERATING_REPORT.value)
        if not operating_templates:
            result.collector["failed_modules"].append(Module.OPERATING_REPORT.value)
            result.collector["warnings"].append("No in-memory template for operating_report.")
        else:
            for index, template in enumerate(operating_templates):
                outcomes[f"operating_report_{index}"] = await replay_request(page, template, before_url=before)
            operating_outcomes = [outcomes[f"operating_report_{index}"] for index in range(len(operating_templates))]
            for outcome in operating_outcomes:
                if outcome.status in {ResultStatus.SUCCESS, ResultStatus.NO_DATA}:
                    partial = normalize_operating(outcome.data)
                    for key, value in partial.items():
                        if value is not None:
                            result.operating_report[key] = value
                else:
                    if Module.OPERATING_REPORT.value not in result.collector["failed_modules"]:
                        result.collector["failed_modules"].append(Module.OPERATING_REPORT.value)
                    result.collector["warnings"].append(f"operating_report: {outcome.status.value}")
            missing_operating = [
                key for key in result.operating_report
                if key != "category" and result.operating_report.get(key) is None
            ]
            if missing_operating:
                if Module.OPERATING_REPORT.value not in result.collector["failed_modules"]:
                    result.collector["failed_modules"].append(Module.OPERATING_REPORT.value)
                result.collector["warnings"].append("Operating report fields missing: " + ", ".join(missing_operating))

        pyramid_template = _template_one(templates, Module.PYRAMID.value) or _template_one(templates, "pyramid_7d")
        pyramid = None
        if pyramid_template is None:
            result.collector["failed_modules"].append(Module.PYRAMID.value)
            result.collector["warnings"].append("No in-memory template for pyramid_7d.")
        else:
            pyramid = await replay_request(page, pyramid_template, before_url=before)
            outcomes[Module.PYRAMID.value] = pyramid
        if pyramid:
            value_7d = find_first(pyramid.data, ("roas_7d", "roas7d", "近7天ROAS", "7dRoas", "roas"))
            result.pyramid["roas_7d"] = value_7d
            # Only a complete HTTP/business response explicitly showing 0 or
            # no data permits the required 30-day fallback.
            if pyramid.status in {ResultStatus.SUCCESS, ResultStatus.NO_DATA} and _roas_explicitly_empty(pyramid, value_7d, window="7d"):
                template_30d = _template_one(templates, "pyramid_30d")
                if template_30d is not None:
                    fallback = await replay_request(page, template_30d, before_url=before)
                    outcomes["pyramid_30d"] = fallback
                    value_30d = find_first(fallback.data, ("roas_30d", "roas30d", "近30天ROAS", "30dRoas", "roas"))
                    if value_30d is None:
                        # Some endpoints reuse a generic/7d field name and
                        # distinguish the window only through the request.
                        value_30d = find_first(fallback.data, ("roas_7d", "roas7d", "近7天ROAS", "7dRoas"))
                    result.pyramid["roas_30d"] = value_30d
                    result.pyramid["no_investment"] = _confirmed_no_investment(pyramid, value_7d, fallback, value_30d)
                    if fallback.status not in {ResultStatus.SUCCESS, ResultStatus.NO_DATA}:
                        if Module.PYRAMID.value not in result.collector["failed_modules"]:
                            result.collector["failed_modules"].append(Module.PYRAMID.value)
                        result.collector["warnings"].append(f"30-day ROAS: {fallback.status.value}; no-investment was not inferred.")
                    elif value_30d is None and not _roas_explicitly_empty(fallback, value_30d, window="30d"):
                        if Module.PYRAMID.value not in result.collector["failed_modules"]:
                            result.collector["failed_modules"].append(Module.PYRAMID.value)
                        result.collector["warnings"].append("30-day ROAS field was not recognized; no-investment was not inferred.")
                else:
                    result.collector["warnings"].append("7-day ROAS is zero/no-data but no 30-day template is available.")
                    if Module.PYRAMID.value not in result.collector["failed_modules"]:
                        result.collector["failed_modules"].append(Module.PYRAMID.value)
            elif pyramid.status not in {ResultStatus.SUCCESS, ResultStatus.NO_DATA}:
                result.collector["warnings"].append("7-day ROAS was not complete; 30-day fallback was not inferred.")
                if Module.PYRAMID.value not in result.collector["failed_modules"]:
                    result.collector["failed_modules"].append(Module.PYRAMID.value)
            elif value_7d is None:
                result.collector["warnings"].append("7-day ROAS field was not recognized; 30-day fallback was not inferred.")
                if Module.PYRAMID.value not in result.collector["failed_modules"]:
                    result.collector["failed_modules"].append(Module.PYRAMID.value)

        violation_templates = _template_many(templates, Module.VIOLATION.value)
        if not violation_templates:
            result.collector["failed_modules"].append(Module.VIOLATION.value)
            result.collector["warnings"].append("No in-memory template for violation.")
        else:
            statuses = []
            for index, template in enumerate(violation_templates):
                outcome = await replay_request(page, template, before_url=before)
                outcomes[f"violation_{index}"] = outcome
                if outcome.status in {ResultStatus.SUCCESS, ResultStatus.NO_DATA}:
                    status = normalize_violation(outcome.data)
                    if status is not None:
                        statuses.append(status)
                else:
                    if Module.VIOLATION.value not in result.collector["failed_modules"]:
                        result.collector["failed_modules"].append(Module.VIOLATION.value)
                    result.collector["warnings"].append(f"violation: {outcome.status.value}")
            unique_statuses = list(dict.fromkeys(statuses))
            if len(unique_statuses) == 1:
                result.violation["status"] = unique_statuses[0]
            elif len(unique_statuses) > 1:
                result.collector["warnings"].append("Violation endpoints returned conflicting statuses.")
                if Module.VIOLATION.value not in result.collector["failed_modules"]:
                    result.collector["failed_modules"].append(Module.VIOLATION.value)
            elif Module.VIOLATION.value not in result.collector["failed_modules"]:
                result.collector["failed_modules"].append(Module.VIOLATION.value)
                result.collector["warnings"].append("Violation response had no explicit status.")
        after = _page_url(page)
        result.collector["current_page_unchanged"] = before == after and all(outcome.current_page_unchanged for outcome in outcomes.values())
        if not result.collector["current_page_unchanged"]:
            result.collector["warnings"].append("Current page URL changed during silent collection.")
        if not result.collector["failed_modules"] and result.collector["current_page_unchanged"]:
            self._last_success_by_hotel[hotel_id] = self._now()
        result.operating_report["category"] = _category_from_operating(result.operating_report)
        return result


def _confirmed_no_investment(first: ReplayResult, value_7d: Any, second: ReplayResult, value_30d: Any) -> bool:
    first_explicit = first.http_ok and first.business_ok and _roas_explicitly_empty(first, value_7d, window="7d")
    second_explicit = second.http_ok and second.business_ok and _roas_explicitly_empty(second, value_30d, window="30d")
    return bool(first_explicit and second_explicit)


def _has_key(value: Any, aliases: Iterable[str]) -> bool:
    wanted = {_norm_key(alias) for alias in aliases}
    if isinstance(value, Mapping):
        if any(_norm_key(key) in wanted for key in value):
            return True
        return any(_has_key(child, aliases) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_key(child, aliases) for child in value)
    return False


def _roas_explicitly_empty(result: ReplayResult, value: Any, *, window: str) -> bool:
    if _as_number(value) == 0:
        return True
    if isinstance(value, str) and _contains_signal(value, ("暂无数据", "没有数据", "无数据", "no data", "empty")):
        return True
    # A successful response with a known ROAS field set to null is explicit
    # unavailable data.  A response with no recognizable ROAS field remains
    # unverified and must not trigger the 30-day fallback.
    aliases = ("roas_7d", "roas7d", "近7天ROAS", "7dRoas", "roas") if window == "7d" else ("roas_30d", "roas30d", "近30天ROAS", "30dRoas", "roas")
    return value is None and _has_key(result.data, aliases)


def _category_from_operating(operating: Mapping[str, Any]) -> Optional[str]:
    from .comparator import compute_category

    return compute_category(
        operating.get("hotel_list_exposure"),
        operating.get("comp_list_exposure"),
        operating.get("hotel_order_conversion"),
        operating.get("comp_order_conversion"),
    )
