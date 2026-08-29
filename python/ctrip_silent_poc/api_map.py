"""Build a conservative, extension-compatible API map from safe captures."""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .models import CaptureRecord, Module
from .redaction import is_sensitive_key, redact_value, sanitize_url


DISCOVERY_METADATA_FIELDS = (
    "business_fields",
    "date_parameters",
    "hotel_identifier_fields",
    "page_sample_checks",
    "pagination",
    "query_schema",
    "read_only_observation",
    "write_operation_observed",
)

SAFE_REQUEST_CONTEXT_TYPES = {
    "same_origin_session",
    "browser_managed_credentials",
    "cookie_header_observed",
    "authorization_header_observed",
    "csrf_header_observed",
    "csrf_request_field_observed",
    "header_absence_not_requirement_proof",
}
SAFE_RESULTS = {
    "success",
    "verified",
    "discovered",
    "failed",
    "no_data",
    "loading",
    "request_failed",
    "login_expired",
    "blocked",
    "unverified",
}
SAFE_PAGE_CONTEXTS = {"any_ebooking_page", "specific_module_page", "unknown", "mixed"}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _safe_map_value(value: Any) -> Any:
    """Remove sensitive mapping keys before discovery metadata is serialized."""

    if isinstance(value, Mapping):
        return {
            str(key): _safe_map_value(child)
            for key, child in value.items()
            if not is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_map_value(child) for child in value]
    return redact_value(value)


def _safe_field_paths(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            if is_sensitive_key(key):
                continue
            safe_child = _safe_field_paths(child)
            if safe_child is not None:
                result[str(key)] = safe_child
        return result
    if isinstance(value, (list, tuple)):
        return [safe for child in value if (safe := _safe_field_paths(child)) is not None]
    if isinstance(value, str):
        parts = [part for part in re.split(r"[.\[\]]+", value) if part]
        return None if any(is_sensitive_key(part) for part in parts) else redact_value(value)
    return redact_value(value)


def _measurement_for(
    measured_results: Mapping[str, Mapping[str, Any] | bool | str],
    key: tuple[str, str, str],
) -> Any:
    candidates = ("|".join(key), key[1])
    return next((measured_results[candidate] for candidate in candidates if candidate in measured_results), None)


def _safe_entry(raw: Mapping[str, Any], measured_results: Mapping[str, Mapping[str, Any] | bool | str]) -> dict[str, Any]:
    module = str(raw.get("module") or Module.UNKNOWN.value)
    url = sanitize_url(raw.get("request_url")) if raw.get("request_url") else ""
    method = str(raw.get("method") or "GET").upper()
    notes = raw.get("notes") or []
    notes = [notes] if isinstance(notes, str) else notes
    notes = [str(redact_value(note)) for note in notes]
    context_types = raw.get("request_context_types") or []
    context_types = context_types if isinstance(context_types, (list, tuple)) else []
    entry: dict[str, Any] = {
        "module": module,
        "variant": raw.get("variant"),
        "request_url": url,
        "method": method,
        "payload_schema": _safe_map_value(raw.get("payload_schema", {"type": "unknown"})),
        "response_schema": _safe_map_value(raw.get("response_schema", {"type": "unknown"})),
        "request_context_types": [
            value for value in context_types
            if isinstance(value, str) and value in SAFE_REQUEST_CONTEXT_TYPES
        ],
        "required_page_context": raw.get("required_page_context") or "unknown",
        "can_call_from_any_ebooking_page": None,
        "result": "unverified",
        "notes": list(dict.fromkeys(notes)),
        "field_paths": {},
        "read_only": False,
        "read_only_justification": None,
        "controlled_silent_test": False,
    }
    measurement = _measurement_for(measured_results, (module, url, method))
    if measurement is None:
        entry["notes"].append("Observed on a page, but silent replay has not been measured.")
        return entry
    if isinstance(measurement, Mapping):
        result = str(measurement.get("result") or "unverified")
        entry["result"] = result if result in SAFE_RESULTS else "unverified"
        value = measurement.get("can_call_from_any_ebooking_page")
        entry["can_call_from_any_ebooking_page"] = value if isinstance(value, bool) else None
        context = str(measurement.get("required_page_context") or "")
        if context in SAFE_PAGE_CONTEXTS:
            entry["required_page_context"] = context
        if measurement.get("variant") in {"7d", "30d"}:
            entry["variant"] = measurement["variant"]
        for field in DISCOVERY_METADATA_FIELDS:
            if field in measurement:
                entry[field] = _safe_map_value(measurement[field])
        if isinstance(measurement.get("field_paths"), Mapping):
            entry["field_paths"] = _safe_field_paths(measurement["field_paths"])
        extra_notes = measurement.get("notes") or []
        extra_notes = [extra_notes] if isinstance(extra_notes, str) else extra_notes
        entry["notes"] = list(dict.fromkeys(
            entry["notes"] + [str(redact_value(note)) for note in extra_notes]
        ))
    elif isinstance(measurement, bool):
        entry["can_call_from_any_ebooking_page"] = measurement
        entry["result"] = "success" if measurement else "failed"
    else:
        entry["result"] = str(measurement)
        entry["can_call_from_any_ebooking_page"] = entry["result"] in {"success", "verified"}
    return entry


def _module_summary(module: str, entries: list[dict[str, Any]], *, map_status: str) -> dict[str, Any]:
    calls = [entry.get("can_call_from_any_ebooking_page") for entry in entries]
    can_call = all(value is True for value in calls) if entries and all(isinstance(value, bool) for value in calls) else None
    if entries and all(entry.get("result") in {"success", "verified"} for entry in entries) and can_call is True:
        result = "success"
    elif any(entry.get("result") in {"failed", "blocked", "login_expired", "request_failed"} for entry in entries):
        result = "failed"
    elif entries and all(entry.get("result") == "discovered" for entry in entries):
        result = "discovered"
    else:
        result = "unverified"
    contexts = {str(entry.get("required_page_context") or "unknown") for entry in entries}
    required_context = contexts.pop() if len(contexts) == 1 else ("mixed" if contexts else "unknown")
    return {
        "module": module,
        # This builder only emits discovery metadata. Enabling execution is a
        # separate, controlled review step and is never inferred from capture.
        "enabled": False,
        "result": result,
        "required_page_context": required_context,
        "can_call_from_any_ebooking_page": can_call,
        "notes": [],
    }


def build_api_map(
    captures: Iterable[CaptureRecord | Mapping[str, Any]],
    *,
    measured_results: Optional[Mapping[str, Mapping[str, Any] | bool | str]] = None,
    map_status: str = "unverified",
) -> dict[str, Any]:
    """Deduplicate observations and never claim unmeasured silent success.

    The result is a discovery map, not an executable request configuration.
    It may be imported for review, but all endpoints remain ``read_only:false``
    and all modules remain disabled until a separate human-controlled compile
    step supplies safe static payloads, field paths and read-only approval.
    """

    if map_status not in {"verified", "discovered", "unverified", "blocked"}:
        raise ValueError("map_status must be verified, discovered, unverified, or blocked")
    measured_results = measured_results or {}
    grouped: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
    for capture in captures:
        raw = capture.to_dict() if isinstance(capture, CaptureRecord) else dict(capture)
        module = str(raw.get("module") or Module.UNKNOWN.value)
        url = sanitize_url(raw.get("request_url")) if raw.get("request_url") else ""
        method = str(raw.get("method") or "GET").upper()
        if not url or module == Module.UNKNOWN.value:
            continue
        key = (module, url, method)
        if key not in grouped:
            grouped[key] = _safe_entry(raw, measured_results)
        else:
            notes = raw.get("notes") or []
            notes = [notes] if isinstance(notes, str) else notes
            grouped[key]["notes"] = list(dict.fromkeys(
                grouped[key]["notes"] + [str(redact_value(note)) for note in notes]
            ))

    by_module = {module.value: [] for module in (Module.OPERATING_REPORT, Module.PYRAMID, Module.VIOLATION)}
    for entry in grouped.values():
        if entry["module"] in by_module:
            by_module[entry["module"]].append(entry)

    modules: dict[str, Any] = {}
    for module, entries in by_module.items():
        summary = _module_summary(module, entries, map_status=map_status)
        if module == Module.PYRAMID.value:
            periods: dict[str, Any] = {"7d": None, "30d": None}
            for entry in entries:
                variant = entry.get("variant") if entry.get("variant") in periods else "7d"
                if periods[variant] is None:
                    periods[variant] = entry
                else:
                    summary["notes"].append(f"Multiple {variant} candidates captured; annotate the intended endpoint before verification.")
                    summary["enabled"] = False
                    summary["result"] = "unverified"
            summary["periods"] = periods
            if periods["7d"] is None or periods["30d"] is None:
                summary["enabled"] = False
                if not (
                    map_status == "discovered"
                    and periods["7d"] is not None
                    and periods["7d"].get("result") == "discovered"
                ):
                    summary["result"] = "unverified"
                summary["can_call_from_any_ebooking_page"] = None
                summary["notes"].append("Both 7d and 30d endpoints are required before Pyramid can be enabled.")
        else:
            summary["endpoints"] = entries
        modules[module] = summary

    return {
        "version": 1,
        "map_kind": "discovery",
        "map_status": map_status,
        "generated_at": _now_iso(),
        "modules": modules,
    }


def write_api_map(
    path: str | Path,
    captures: Iterable[CaptureRecord | Mapping[str, Any]],
    *,
    measured_results: Optional[Mapping[str, Mapping[str, Any] | bool | str]] = None,
    map_status: str = "unverified",
) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = build_api_map(captures, measured_results=measured_results, map_status=map_status)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return result
