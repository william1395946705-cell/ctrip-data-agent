"""Conservative redaction and schema helpers for captured network data."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED = "<redacted>"
UNKNOWN = "<unavailable>"

# This is intentionally a reject-list in addition to the allow-list used for
# headers.  It catches common spelling/casing variations in nested JSON keys.
SENSITIVE_KEY_PARTS = (
    "auth",
    "authorization",
    "cookie",
    "csrf",
    "credential",
    "fingerprint",
    "jwt",
    "password",
    "passwd",
    "secret",
    "session",
    "signature",
    "ticket",
    "token",
    "sso",
    "otp",
    "verificationcode",
    "accesskey",
    "refreshkey",
    "apikey",
    "privatekey",
    "authvalue",
)
SENSITIVE_QUERY_PARTS = SENSITIVE_KEY_PARTS + ("ticket", "code", "state", "sso")

# Short telemetry/session keys are matched exactly instead of as substrings so
# business fields such as ``hotelId`` and ``orderId`` remain available while
# browser/session correlation identifiers never reach serialized artifacts.
SENSITIVE_EXACT_KEYS = {
    "clientid",
    "ctok",
    "deviceid",
    "fp",
    "fxpcqlniredt",
    "logid",
    "oneid",
    "pvid",
    "requestid",
    "sid",
    "spiderkey",
    "traceid",
    "vid",
    "xsid",
    "xtraceid",
}

SAFE_HEADER_NAMES = {
    "accept",
    "content-type",
    "origin",
    "referer",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "x-business-line",
    "x-client-version",
    "x-platform",
    "x-requested-with",
}

_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)\b(authorization|cookie|csrf(?:token)?|session(?:id)?|token|access[_-]?token|refresh[_-]?token)(\s*[:=]\s*)[^,;\s]+"
)
_QUOTED_SECRET_RE = re.compile(
    r'''(?ix)(["']?(?:authorization|cookie|csrf(?:token)?|session(?:id)?|token|access[_-]?token|refresh[_-]?token|sso(?:ticket)?|ticket|signature)["']?\s*[:=]\s*["']?)([^"',;\s<>}]+)'''
)
_ENCODED_SECRET_RE = re.compile(
    r"(?i)((?:authorization|cookie|csrf(?:token)?|session(?:id)?|token|access[_-]?token|refresh[_-]?token|sso(?:ticket)?|ticket|signature)(?:%22)?(?:%3A|%3D)(?:%22)?)([^%&\s]+)"
)
_COOKIE_FRAGMENT_RE = re.compile(r"(?i)\b(sid|sessionid|jsessionid|oneid)(\s*=\s*)[^,;\s<>]+")
_GENERAL_SECRET_NAME = (
    r"[a-z0-9_-]*(?:auth(?:orization)?|cookie|csrf(?:token)?|credential|jwt|password|passwd|secret|"
    r"session(?:id)?|signature|ticket|token|sso(?:ticket)?|otp|verification[-_]?code|"
    r"(?:access|refresh|private|api)[-_]?key)[a-z0-9_-]*"
)
_GENERAL_SECRET_RE = re.compile(
    rf'''(?ix)((?:["']|%22)?{_GENERAL_SECRET_NAME}(?:["']|%22)?\s*(?::|=|%3A|%3D)\s*(?:["']|%22)?)([^"',;\s<>}}%&]+)'''
)


def is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return bool(normalized) and (
        normalized in SENSITIVE_EXACT_KEYS
        or any(part in normalized for part in SENSITIVE_KEY_PARTS)
    )


def sanitize_text(value: str) -> str:
    """Remove token-like fragments without attempting to preserve secrets."""

    value = _JWT_RE.sub(REDACTED, value)
    value = _BEARER_RE.sub("Bearer " + REDACTED, value)
    value = _GENERAL_SECRET_RE.sub(lambda m: m.group(1) + REDACTED, value)
    value = _KEY_VALUE_SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + REDACTED, value)
    value = _QUOTED_SECRET_RE.sub(lambda m: m.group(1) + REDACTED, value)
    value = _ENCODED_SECRET_RE.sub(lambda m: m.group(1) + REDACTED, value)
    value = _COOKIE_FRAGMENT_RE.sub(lambda m: m.group(1) + m.group(2) + REDACTED, value)
    return value


def sanitize_url(url: Any) -> str:
    """Keep route/query names but remove sensitive query values and fragments."""

    if not isinstance(url, str) or not url:
        return UNKNOWN if url is None else str(url)
    try:
        split = urlsplit(url)
        pairs = []
        for key, value in parse_qsl(split.query, keep_blank_values=True):
            if is_sensitive_query_key(key):
                pairs.append((key, REDACTED))
            else:
                pairs.append((key, sanitize_text(value)))
        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(pairs), ""))
    except Exception:
        return sanitize_text(url)


def is_sensitive_query_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return bool(normalized) and (
        normalized in SENSITIVE_EXACT_KEYS
        or any(part in normalized for part in SENSITIVE_QUERY_PARTS)
    )


def redact_value(value: Any, key: Optional[Any] = None, *, max_string_length: int = 100_000) -> Any:
    """Recursively redact JSON-like values and cap arbitrary response text."""

    if key is not None and is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): redact_value(v, k, max_string_length=max_string_length) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v, max_string_length=max_string_length) for v in value]
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        if value.startswith(("https://", "http://")):
            return sanitize_url(value)
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                return redact_value(json.loads(value), max_string_length=max_string_length)
            except (TypeError, ValueError):
                pass
        text = sanitize_text(value)
        if len(text) > max_string_length:
            return text[:max_string_length] + "…"
        return text
    return value


def schema_of(value: Any, *, max_properties: int = 100) -> Any:
    """Describe structure/types only; never includes scalar values."""

    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, Mapping):
        properties: Dict[str, Any] = {}
        redacted_properties = 0
        for index, (key, child) in enumerate(value.items()):
            if index >= max_properties:
                break
            if is_sensitive_key(key):
                redacted_properties += 1
                continue
            properties[str(key)] = schema_of(child)
        result: Dict[str, Any] = {"type": "object", "properties": properties}
        if redacted_properties:
            result["redacted_properties_count"] = redacted_properties
        if len(value) > max_properties:
            result["truncated"] = True
        return result
    if isinstance(value, Sequence):
        first = next(iter(value), None)
        return {"type": "array", "items": schema_of(first) if first is not None else {"type": "unknown"}}
    return {"type": type(value).__name__}


def safe_headers(headers: Any) -> Dict[str, str]:
    """Return business headers only; sensitive names are rejected by default."""

    if not isinstance(headers, Mapping):
        return {}
    result: Dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).lower()
        if name not in SAFE_HEADER_NAMES or is_sensitive_key(name):
            continue
        value = sanitize_text(str(raw_value))
        # Referer/origin may contain a query parameter; run URL sanitation too.
        if name in {"referer", "origin"}:
            value = sanitize_url(value)
        result[name] = value
    return result


def safe_json_text(value: Any) -> str:
    """Serialize only after recursive redaction, useful for JSONL writers."""

    return json.dumps(redact_value(value), ensure_ascii=False, separators=(",", ":"))


def has_unredacted_sensitive_material(value: str) -> bool:
    """Fail-closed scan for known credential shapes before disk output."""

    text = str(value)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return sanitize_text(text) != text

    def unsafe(item: Any, key: Any = None) -> bool:
        if key is not None and is_sensitive_key(key):
            return item != REDACTED
        if isinstance(item, Mapping):
            return any(unsafe(child, child_key) for child_key, child in item.items())
        if isinstance(item, (list, tuple)):
            return any(unsafe(child) for child in item)
        if isinstance(item, str):
            if item.startswith(("https://", "http://")):
                return sanitize_url(item) != item
            return sanitize_text(item) != item
        return False

    return unsafe(parsed)


def safe_error(error: Any) -> str:
    # Exception strings can contain a failed request URL.  URL sanitation is
    # applied before the generic token regex so query credentials are covered.
    return sanitize_url(sanitize_text(str(error)))[:1000]
