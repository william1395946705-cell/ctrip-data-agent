"""Playwright BrowserContext response inspector.

The inspector is passive: it registers a ``response`` listener and never
navigates, focuses, clicks, types, reloads, or calls ``bringToFront``.  Raw
request templates are optionally retained in an in-process vault for replay;
only sanitized capture records may be written to disk.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import inspect
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlsplit

from .models import CaptureRecord, Module
from .redaction import (
    UNKNOWN,
    has_unredacted_sensitive_material,
    redact_value,
    safe_error,
    safe_headers,
    sanitize_url,
    schema_of,
)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def hotel_fingerprint(hotel: Any) -> Optional[str]:
    """Return a stable, one-way fingerprint for the current hotel identity.

    Hotel ids/names are business data and are deliberately not copied into
    capture provenance.  An id is preferred when available; the name is only
    a fallback for pages that do not expose an id in harmless DOM state.
    """

    if not isinstance(hotel, Mapping):
        return None
    hotel_id = re.sub(r"\s+", "", str(hotel.get("hotel_id") or "")).strip().lower()
    hotel_name = re.sub(r"\s+", " ", str(hotel.get("hotel_name") or "")).strip().lower()
    if not hotel_id and not hotel_name:
        return None
    identity = f"id:{hotel_id}" if hotel_id else f"name:{hotel_name}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        value = getattr(obj, name)
        return value() if callable(value) else value
    except Exception:
        return default


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def classify_module(*values: Any) -> str:
    """Classify using explicit hints and request/response routes only."""

    if values:
        explicit = str(values[0] or "").strip().lower()
        if explicit in {module.value for module in Module}:
            return explicit
    text = " ".join(str(value or "").lower() for value in values)
    # Avoid matching generic words such as ``report`` in unrelated APIs where
    # an explicit caller hint identifies the module.
    if any(token in text for token in ("pyramid", "jinzita", "金字塔", "roas", "广告投产比", "/cpc/datareport", "toolcenter/api/cpc")):
        return Module.PYRAMID.value
    if any(token in text for token in ("violation", "breach", "contract", "违约", "违规", "punlishment", "punishment", "queryebkpunlishment")):
        return Module.VIOLATION.value
    if any(token in text for token in (
        "operating-report", "operating_report", "经营报告", "operatingreport",
        "/datacenter/api/", "flowanalysis", "hoteladvice", "dayreportserverquantity",
        "capacityoverview", "tensityoverview", "marketoverview", "picturequalityscore",
        "fetchcurrenthotelseqinfo", "列表页曝光", "曝光转化率", "下单转化率",
    )):
        return Module.OPERATING_REPORT.value
    return Module.UNKNOWN.value


def _is_xhr_or_fetch(response: Any) -> bool:
    request = _get(response, "request")
    resource_type = _get(request, "resource_type", _get(request, "resourceType", None))
    if resource_type is None:
        # Fake responses and some Playwright wrappers omit resource_type.  The
        # context listener still only records responses explicitly accepted by
        # callers/tests in that case.
        return True
    return str(resource_type).lower() in {"xhr", "fetch"}


def _is_exact_ebooking_request(response: Any) -> bool:
    """Keep discovery aligned with the Connector's exact same-origin scope."""

    request = _get(response, "request")
    raw_url = _get(request, "url", _get(response, "url", ""))
    try:
        split = urlsplit(str(raw_url or ""))
        return split.scheme.lower() == "https" and (split.hostname or "").lower() == "ebooking.ctrip.com" and split.port in {None, 443}
    except (TypeError, ValueError):
        return False


def _request_url(response: Any) -> str:
    request = _get(response, "request")
    return sanitize_url(_get(request, "url", _get(response, "url", UNKNOWN)))


def _response_url(response: Any) -> str:
    return sanitize_url(_get(response, "url", _request_url(response)))


def _request_method(response: Any) -> str:
    request = _get(response, "request")
    return str(_get(request, "method", "GET") or "GET").upper()


def _request_headers(response: Any) -> Mapping[str, Any]:
    request = _get(response, "request")
    headers = _get(request, "headers", {})
    return headers if isinstance(headers, Mapping) else {}


def _request_post_data(response: Any) -> Any:
    request = _get(response, "request")
    data = _get(request, "post_data", _get(request, "postData", None))
    if data is None:
        return None
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        try:
            return json.loads(data)
        except (ValueError, TypeError):
            return data
    return data


async def _response_body(response: Any, content_type: str) -> Any:
    """Read body using Playwright's body/text/json APIs, tolerating mocks."""

    for method_name in ("json", "text", "body"):
        method = getattr(response, method_name, None)
        if not callable(method):
            continue
        try:
            value = await _maybe_await(method())
            if method_name == "body" and isinstance(value, (bytes, bytearray)):
                value = value.decode("utf-8", errors="replace")
            if method_name == "text" and "json" in content_type.lower():
                try:
                    return json.loads(value)
                except (TypeError, ValueError):
                    pass
            return value
        except Exception:
            continue
    return None


def _page_url(response: Any, trigger_page: Optional[str]) -> Optional[str]:
    if trigger_page:
        return sanitize_url(trigger_page)
    request = _get(response, "request")
    frame = _get(request, "frame") or _get(response, "frame")
    page = _get(frame, "page") if frame else None
    url = _get(page, "url") if page else None
    if not url and frame:
        url = _get(frame, "url")
    return sanitize_url(url) if url else None


def _response_page(response: Any) -> Any:
    request = _get(response, "request")
    frame = _get(request, "frame") or _get(response, "frame")
    return _get(frame, "page") if frame else None


def _pyramid_variant(module: str, request_url: str, payload: Any) -> Optional[str]:
    if module != Module.PYRAMID.value:
        return None
    text = f"{request_url} {payload}".lower()
    if any(token in text for token in ("30d", "30day", "30天", "range=30", "daycount=30", "\"days\": 30", "\"days\":30")):
        return "30d"
    return "7d"


_HOTEL_ID_KEYS = frozenset({
    "hotelid", "hotelcode", "propertyid", "propertycode", "hotelno", "hotelnumber",
})
_HOTEL_ID_TEXT_RE = re.compile(
    r'''(?i)(?:["']?(?:hotel[_-]?id|hotel[_-]?code|property[_-]?id|property[_-]?code)["']?\s*[:=]\s*["']?)([A-Za-z0-9][A-Za-z0-9_-]{0,199})'''
)


def _normal_hotel_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    text = str(value).strip().lower()
    return text[:200] if text else ""


def extract_hotel_ids(value: Any, *, _depth: int = 0) -> set[str]:
    """Best-effort extraction from already-sanitized request/response data.

    This is intentionally limited to explicit id/code keys and URL query
    parameters.  It does not inspect cookies, tokens, or arbitrary text for
    values that merely resemble an id.
    """

    if _depth > 8 or value is None:
        return set()
    if isinstance(value, Mapping):
        ids: set[str] = set()
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in _HOTEL_ID_KEYS and not isinstance(child, (Mapping, list, tuple)):
                hotel_id = _normal_hotel_id(child)
                if hotel_id:
                    ids.add(hotel_id)
            ids.update(extract_hotel_ids(child, _depth=_depth + 1))
        return ids
    if isinstance(value, (list, tuple)):
        ids: set[str] = set()
        for child in value:
            ids.update(extract_hotel_ids(child, _depth=_depth + 1))
        return ids
    if not isinstance(value, str):
        return set()
    text = value[:100_000]
    ids: set[str] = set()
    try:
        split = urlsplit(text)
        if split.scheme and split.netloc:
            for key, child in parse_qsl(split.query, keep_blank_values=True):
                normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
                if normalized_key in _HOTEL_ID_KEYS:
                    hotel_id = _normal_hotel_id(child)
                    if hotel_id:
                        ids.add(hotel_id)
    except ValueError:
        pass
    ids.update(_normal_hotel_id(match.group(1)) for match in _HOTEL_ID_TEXT_RE.finditer(text))
    return {hotel_id for hotel_id in ids if hotel_id}


class NetworkInspector:
    """Passive response capture attached to an existing Playwright context."""

    def __init__(
        self,
        *,
        max_body_chars: int = 100_000,
        on_capture: Optional[Callable[[CaptureRecord], Any]] = None,
        request_vault: Optional[Any] = None,
        module_hint: Optional[str] = None,
        test_a_batch_id: Optional[str] = None,
        hotel_fingerprint: Optional[str] = None,
        capture_enabled: bool = True,
    ) -> None:
        self.max_body_chars = max_body_chars
        self.on_capture = on_capture
        self.request_vault = request_vault
        self.module_hint = module_hint
        self.test_a_batch_id = str(test_a_batch_id).strip()[:100] if test_a_batch_id else None
        self.hotel_fingerprint = str(hotel_fingerprint).strip()[:128] if hotel_fingerprint else None
        self.capture_enabled = bool(capture_enabled)
        self.records: List[CaptureRecord] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._context: Any = None
        self._listener: Any = None
        self._target_page: Any = None
        self._pending_candidates: List[Any] = []

    def attach(self, context: Any, *, module_hint: Optional[str] = None, target_page: Any = None) -> "NetworkInspector":
        """Register the response callback; no browser state is changed."""

        self._context = context
        if module_hint is not None:
            self.module_hint = module_hint
        self._target_page = target_page
        self._listener = self._on_response
        context.on("response", self._listener)
        return self

    def set_module_hint(self, module_hint: Optional[str]) -> None:
        """Set a discovery-phase hint for APIs whose URL is opaque."""

        self.module_hint = module_hint

    def set_capture_identity(self, *, test_a_batch_id: Optional[str], hotel_fingerprint: Optional[str]) -> None:
        """Set the provenance attached to subsequent Test A captures."""

        self.test_a_batch_id = str(test_a_batch_id).strip()[:100] if test_a_batch_id else None
        self.hotel_fingerprint = str(hotel_fingerprint).strip()[:128] if hotel_fingerprint else None

    def set_capture_enabled(self, enabled: bool) -> None:
        """Open or close the passive capture window without touching the page."""

        self.capture_enabled = bool(enabled)

    def detach(self) -> None:
        """Remove the listener where the context supports Playwright's off()."""

        if self._context is not None:
            off = getattr(self._context, "off", None)
            if callable(off):
                off("response", self._listener or self._on_response)
        self._context = None
        self._listener = None
        self._target_page = None

    def _on_response(self, response: Any) -> None:
        if not self.capture_enabled:
            return
        if not _is_xhr_or_fetch(response) or not _is_exact_ebooking_request(response):
            return
        if self._target_page is not None:
            response_page = _response_page(response)
            if response_page is not None and response_page is not self._target_page:
                return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # sync_playwright invokes callbacks without an asyncio loop.  Run
            # the same coroutine synchronously; response.json()/text()/body()
            # are synchronous in that API and _maybe_await handles both APIs.
            try:
                asyncio.run(self.capture_response(response, module_hint=self.module_hint))
            except Exception:
                # Never expose a raw exception (which could contain a URL or
                # request data) through the passive listener.
                pass
            return
        task = loop.create_task(self.capture_response(response, module_hint=self.module_hint))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def capture_response(
        self,
        response: Any,
        *,
        module_hint: Optional[str] = None,
        trigger_page: Optional[str] = None,
        request_time: Optional[str] = None,
    ) -> CaptureRecord:
        """Capture one response and retain only a sanitized record."""

        request_url = _request_url(response)
        response_url = _response_url(response)
        method = _request_method(response)
        request_payload = _request_post_data(response)
        headers = safe_headers(_request_headers(response))
        raw_content_type = _get(response, "headers", {})
        if isinstance(raw_content_type, Mapping):
            content_type = str(raw_content_type.get("content-type", raw_content_type.get("Content-Type", "")) or "")
        else:
            content_type = ""
        status = _get(response, "status", None)
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            status = None
        raw_body = await _response_body(response, content_type)
        observed_page = _page_url(response, trigger_page)
        # Classify from operator hint and request/page routes only.  Response
        # bodies often contain unrelated notification or order text mentioning
        # promotions/violations and previously produced unsafe false positives.
        # The trigger-page route is context evidence only.  Using it for the
        # module would label every notification/order request made by a target
        # page as that page's business API.
        module = classify_module(module_hint, request_url, response_url)
        variant = _pyramid_variant(module, request_url, request_payload)
        safe_payload = redact_value(request_payload, max_string_length=self.max_body_chars)
        safe_body = redact_value(raw_body, max_string_length=self.max_body_chars)
        context = "unknown"
        if module_hint in {Module.OPERATING_REPORT.value, Module.PYRAMID.value, Module.VIOLATION.value}:
            context = "specific_module_page"
        elif observed_page and classify_module(observed_page) != Module.UNKNOWN.value:
            context = "specific_module_page"
        elif observed_page and _is_ebooking_page(observed_page):
            context = "any_ebooking_page"
        record = CaptureRecord(
            module=module,
            request_url=sanitize_url(request_url),
            method=method,
            payload_schema=schema_of(request_payload),
            response_schema=schema_of(raw_body),
            required_page_context=context,
            can_call_from_any_ebooking_page=None,
            result="unverified",
            notes=[],
            variant=variant,
            status=status,
            content_type=content_type,
            response_url=sanitize_url(response_url) if response_url else None,
            trigger_page=sanitize_url(observed_page) if observed_page else None,
            request_time=request_time or _now_iso(),
            headers=headers,
            payload=safe_payload,
            response=safe_body,
            test_a_batch_id=self.test_a_batch_id,
            hotel_fingerprint=self.hotel_fingerprint,
        )
        if status is not None and not 200 <= status < 300:
            record.notes.append("HTTP response is not 2xx; silent capability is unverified.")
        if module == Module.UNKNOWN.value:
            record.notes.append("Business module could not be classified from request/page hints.")
        self.records.append(record)

        # Discovery never makes a request replayable. Raw request candidates
        # remain only in this process until an operator explicitly approves an
        # exact endpoint as read-only in the generated discovery map.
        if self.request_vault is not None and module != Module.UNKNOWN.value:
            try:
                candidate = self.request_vault.candidate_from_response(module, response, variant=variant)
                fingerprint = (candidate.module, candidate.url, candidate.method, str(candidate.body), candidate.variant)
                if not any(
                    (item.module, item.url, item.method, str(item.body), item.variant) == fingerprint
                    for item in self._pending_candidates
                ):
                    self._pending_candidates.append(candidate)
            except Exception:
                # Vault failure must not make the passive inspector fail or log
                # potentially sensitive exception text.
                record.notes.append("In-memory replay template was unavailable.")
        if self.on_capture is not None:
            callback_result = self.on_capture(record)
            if inspect.isawaitable(callback_result):
                await callback_result
        return record

    async def capture_responses(self, responses: Iterable[Any], **kwargs: Any) -> List[CaptureRecord]:
        result = []
        for response in responses:
            if _is_xhr_or_fetch(response) and _is_exact_ebooking_request(response):
                result.append(await self.capture_response(response, **kwargs))
        return result

    def approve_from_api_map(self, api_map: Mapping[str, Any], *, controlled_test: bool = False) -> int:
        """Approve exact in-memory candidates explicitly marked read-only.

        The map itself never contains raw request bodies or credentials.  An
        ambiguous endpoint match is refused so a broad URL cannot approve
        multiple different captured operations.
        """

        if self.request_vault is None or not isinstance(api_map, Mapping):
            return 0
        modules = api_map.get("modules")
        if not isinstance(modules, Mapping):
            return 0
        approvals = 0
        for module_name in (Module.OPERATING_REPORT.value, Module.PYRAMID.value, Module.VIOLATION.value):
            module = modules.get(module_name)
            if not isinstance(module, Mapping):
                continue
            module_any_page = (
                module.get("can_call_from_any_ebooking_page") is True
                and module.get("required_page_context") == "any_ebooking_page"
            )
            endpoint_items: List[tuple[Optional[str], Mapping[str, Any]]] = []
            if module_name == Module.PYRAMID.value:
                periods = module.get("periods")
                if isinstance(periods, Mapping):
                    for variant in ("7d", "30d"):
                        endpoint = periods.get(variant)
                        if isinstance(endpoint, Mapping):
                            endpoint_items.append((variant, endpoint))
            else:
                endpoints = module.get("endpoints")
                if isinstance(endpoints, list):
                    endpoint_items.extend((None, item) for item in endpoints if isinstance(item, Mapping))
                endpoint = module.get("endpoint")
                if isinstance(endpoint, Mapping):
                    endpoint_items.append((None, endpoint))
            for variant, endpoint in endpoint_items:
                if endpoint.get("read_only") is not True:
                    continue
                endpoint_any_page = (
                    endpoint.get("can_call_from_any_ebooking_page") is True
                    and endpoint.get("required_page_context") == "any_ebooking_page"
                )
                controlled_test_approved = controlled_test and endpoint.get("controlled_silent_test") is True
                if not (module_any_page and endpoint_any_page) and not controlled_test_approved:
                    continue
                method = str(endpoint.get("method") or "GET").upper()
                justification = str(endpoint.get("read_only_justification") or "").strip()
                if method not in {"GET", "POST"} or (method == "POST" and not justification):
                    continue
                safe_endpoint_url = sanitize_url(endpoint.get("request_url"))
                matches = [
                    candidate
                    for candidate in self._pending_candidates
                    if candidate.module == module_name
                    and candidate.method == method
                    and sanitize_url(candidate.url) == safe_endpoint_url
                    and (variant is None or candidate.variant == variant)
                ]
                if len(matches) != 1:
                    continue
                try:
                    self.request_vault.approve_candidate(
                        matches[0],
                        read_only_justification=justification or "Observed GET endpoint; operator approved as read-only.",
                    )
                except Exception:
                    continue
                approvals += 1
        return approvals

    def write_jsonl(self, path: str | Path) -> None:
        """Write sanitized captures only; never write vault contents."""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines: List[str] = []
        for record in self.records:
            # Redact again at the final serialization boundary and fail closed
            # before creating a partially written artifact.
            line = json.dumps(redact_value(record.to_dict()), ensure_ascii=False, separators=(",", ":"))
            if has_unredacted_sensitive_material(line):
                raise ValueError("Refusing to write capture log containing credential-shaped material.")
            lines.append(line)
        with target.open("w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")


def _is_ebooking_page(url: str) -> bool:
    try:
        split = urlsplit(url)
        host = (split.hostname or "").lower()
        return split.scheme.lower() == "https" and host == "ebooking.ctrip.com" and split.port in {None, 443}
    except ValueError:
        return False
