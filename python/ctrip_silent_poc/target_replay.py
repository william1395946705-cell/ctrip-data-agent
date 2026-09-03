"""Controlled ordinary-page replay for the six discovered Ctrip endpoints.

The compiler consumes only sanitized, gitignored discovery captures. It
removes dynamic credential/fingerprint fields, approves only an exact static
query allow-list, and never serializes request bodies or response business
values. The browser page is neither navigated nor focused.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlsplit

from .inspector import extract_hotel_ids, hotel_fingerprint
from .models import Module, ReplayResult, ResultStatus
from .redaction import (
    REDACTED,
    has_unredacted_sensitive_material,
    is_sensitive_key,
    safe_error,
    sanitize_url,
)
from .replay import RequestTemplate, inspect_current_page, is_ebooking_url, replay_request


@dataclass(frozen=True)
class EndpointAudit:
    endpoint_id: str
    module: str
    path: str
    justification: str


@dataclass(frozen=True)
class CompiledReplay:
    audit: EndpointAudit
    variant: str
    template: RequestTemplate
    baseline_response: Any
    template_sha256: str
    omitted_dynamic_field_count: int


ENDPOINT_AUDITS = (
    EndpointAudit(
        "operating_advice",
        Module.OPERATING_REPORT.value,
        "/datacenter/api/dataCenter/report/getHotelAdvice",
        "Query-only operating advice report observed on the business report page; no mutation effect was observed.",
    ),
    EndpointAudit(
        "operating_market_overview",
        Module.OPERATING_REPORT.value,
        "/datacenter/api/dataCenter/sale/fetchMarketOverViewV2",
        "Query-only market overview report observed on the business report page; no mutation effect was observed.",
    ),
    EndpointAudit(
        "operating_scores",
        Module.OPERATING_REPORT.value,
        "/datacenter/api/dataCenter/report/getDayReportServerQuantity",
        "Query-only daily score report observed on the business report page; no mutation effect was observed.",
    ),
    EndpointAudit(
        "operating_flow",
        Module.OPERATING_REPORT.value,
        "/datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1",
        "Query-only flow analysis report observed on the business report page; no mutation effect was observed.",
    ),
    EndpointAudit(
        "pyramid_7d",
        Module.PYRAMID.value,
        "/toolcenter/api/cpc/queryCampaignReportList",
        "Query-only campaign report list observed on the ROAS page; no campaign mutation effect was observed.",
    ),
    EndpointAudit(
        "violation_list",
        Module.VIOLATION.value,
        "/toolcenter/api/psi/queryEbkPunlishMent",
        "Query-only violation list observed on the violation dashboard; no mutation effect was observed.",
    ),
)

_AUDIT_BY_PATH = {audit.path: audit for audit in ENDPOINT_AUDITS}
_TARGET_MODULE_PATH_MARKERS = (
    "/datacenter/inland/businessreport",
    "/toolcenter/cpc/",
    "/toolcenter/psi/",
)
_ORDINARY_PAGE_RE = re.compile(r"(?:order|ebkorder)", re.I)
_SAFE_REPLAY_HEADERS = {"accept", "content-type", "x-requested-with", "x-business-line", "x-client-version", "x-platform"}
_SAFE_QUERY_VALUE_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
_CAPTURE_BINDING_FILE = ".capture-set-binding.sanitized.json"
_PAYLOAD_SPECS: Mapping[str, Mapping[str, Mapping[str, str]]] = {
    "operating_advice": {"default": {}},
    "operating_market_overview": {"default": {
        "platform": "integer", "startDateType": "integer", "startDate": "date",
        "needRank": "boolean", "spiderVersion": "version",
    }},
    "operating_scores": {"default": {"spiderVersion": "version"}},
    "operating_flow": {"default": {
        "platform": "string", "startDate": "date", "endDate": "date", "spiderVersion": "version",
    }},
    "pyramid_7d": {
        "summary": {
            "startDate": "date", "endDate": "date", "convertPeriod": "integer",
            "isSummary": "boolean", "pageIdx": "integer", "pageSize": "integer",
        },
        "daily": {
            "campaignId": "string", "startDate": "date", "endDate": "date", "keyword": "string",
            "keywordType": "string", "pageIdx": "integer", "pageSize": "integer",
            "isSummary": "boolean", "convertPeriod": "integer", "premiumCodes": "array[scalar]",
            "isChart": "boolean",
        },
    },
    "violation_list": {"default": {
        "selectedCategory": "array[integer]", "pageIndex": "integer", "pageSize": "string",
        "selectedStatus": "string", "defectCategoryId": "integer", "subCategoryId": "integer",
    }},
}
_QUERY_SPECS: Mapping[str, frozenset[str]] = {
    "operating_advice": frozenset(),
    "operating_market_overview": frozenset(),
    "operating_scores": frozenset(),
    "operating_flow": frozenset({"hostType", "v"}),
    "pyramid_7d": frozenset({"hostType", "v"}),
    "violation_list": frozenset({"hostType", "v"}),
}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _drop_dynamic(value: Any) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        output = {}
        omitted = 0
        for key, child in value.items():
            if is_sensitive_key(key) or child == REDACTED:
                omitted += 1
                continue
            safe_child, child_omitted = _drop_dynamic(child)
            output[str(key)] = safe_child
            omitted += child_omitted
        return output, omitted
    if isinstance(value, (list, tuple)):
        output = []
        omitted = 0
        for child in value:
            safe_child, child_omitted = _drop_dynamic(child)
            output.append(safe_child)
            omitted += child_omitted
        return output, omitted
    return value, 0


def _safe_request_headers(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key).lower(): str(child)
        for key, child in value.items()
        if str(key).lower() in _SAFE_REPLAY_HEADERS and not is_sensitive_key(key)
    }


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array[integer]":
        return isinstance(value, list) and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    if expected == "array[scalar]":
        return isinstance(value, list) and all(isinstance(item, (str, int, float)) and not isinstance(item, bool) for item in value)
    if expected == "date":
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return False
        try:
            _dt.date.fromisoformat(value)
            return True
        except ValueError:
            return False
    if expected == "version":
        return isinstance(value, str) and len(value) <= 64 and bool(_SAFE_QUERY_VALUE_RE.fullmatch(value))
    return False


def _validate_exact_capture_shape(
    audit: EndpointAudit,
    map_endpoint: Mapping[str, Any],
    raw_url: str,
    payload: Any,
    variant: str,
) -> None:
    """Reject any query/body shape outside the six reviewed request variants."""

    split = urlsplit(raw_url)
    if split.fragment:
        raise ValueError(f"URL fragments are not allowed for reviewed endpoint: {audit.path}")
    pairs = parse_qsl(split.query, keep_blank_values=True)
    query = dict(pairs)
    expected_query = _QUERY_SPECS[audit.endpoint_id]
    if len(query) != len(pairs) or set(query) != expected_query:
        raise ValueError(f"Unexpected query parameters for reviewed endpoint: {audit.path}")
    if expected_query:
        if (
            not isinstance(query.get("hostType"), str)
            or not query["hostType"]
            or len(query["hostType"]) > 32
            or not _SAFE_QUERY_VALUE_RE.fullmatch(query["hostType"])
            or not _type_matches(query.get("v"), "version")
        ):
            raise ValueError(f"Unexpected query values for reviewed endpoint: {audit.path}")
    query_schema = map_endpoint.get("query_schema")
    map_query_fields = set((query_schema or {}).get("fields") or {}) if isinstance(query_schema, Mapping) else set()
    if map_query_fields != expected_query:
        raise ValueError(f"API map query schema differs from the exact allow-list: {audit.path}")

    spec = _PAYLOAD_SPECS[audit.endpoint_id].get(variant)
    if spec is None:
        raise ValueError(f"Unapproved request variant: {audit.path}")
    if audit.endpoint_id == "operating_advice":
        map_payload = map_endpoint.get("payload_schema")
        map_fields = (map_payload or {}).get("fields") if isinstance(map_payload, Mapping) else None
        if payload != {} or not isinstance(map_fields, Mapping) or dict(map_fields):
            raise ValueError(f"Unexpected request body for reviewed endpoint: {audit.path}")
        return
    if not isinstance(payload, Mapping) or set(payload) != set(spec):
        raise ValueError(f"Unexpected request body fields for reviewed endpoint: {audit.path}")
    if any(not _type_matches(payload.get(key), kind) for key, kind in spec.items()):
        raise ValueError(f"Unexpected request body types for reviewed endpoint: {audit.path}")

    map_payload = map_endpoint.get("payload_schema")
    map_fields = (map_payload or {}).get("fields") if isinstance(map_payload, Mapping) else None
    base_spec = _PAYLOAD_SPECS[audit.endpoint_id].get("summary", spec)
    normalized_base = {key: "string" if kind in {"date", "version"} else kind for key, kind in base_spec.items()}
    if not isinstance(map_fields, Mapping) or dict(map_fields) != normalized_base:
        raise ValueError(f"API map payload schema differs from the exact allow-list: {audit.path}")

    if "startDate" in payload and "endDate" in payload and payload["startDate"] > payload["endDate"]:
        raise ValueError(f"Invalid date range for reviewed endpoint: {audit.path}")
    if audit.endpoint_id == "pyramid_7d":
        expected_summary = variant == "summary"
        expected_page_size = 10 if expected_summary else 500
        if (
            payload["convertPeriod"] != 3
            or payload["isSummary"] is not expected_summary
            or payload["pageIdx"] != 1
            or payload["pageSize"] != expected_page_size
            or (
                variant == "daily"
                and (
                    payload["isChart"] is not True
                    or payload["campaignId"] != ""
                    or payload["keyword"] != ""
                    or payload["keywordType"] != ""
                    or payload["premiumCodes"] != []
                )
            )
        ):
            raise ValueError("ROAS request constants differ from the reviewed 7-day query.")
    if audit.endpoint_id == "violation_list" and (
        payload["pageIndex"] != 1
        or not payload["pageSize"].isdigit()
        or not 1 <= int(payload["pageSize"]) <= 100
        or len(payload["selectedStatus"]) > 64
        or payload["defectCategoryId"] < 0
        or payload["subCategoryId"] < 0
    ):
        raise ValueError("Violation request filters differ from the reviewed safe query shape.")


def load_sanitized_captures(root: str | Path) -> list[dict[str, Any]]:
    """Load sanitized JSONL only and fail closed on credential-shaped data."""

    base = Path(root)
    records: list[dict[str, Any]] = []
    for path in sorted(base.glob("*/captures.sanitized.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            if has_unredacted_sensitive_material(line):
                raise ValueError("A discovery capture contains unredacted sensitive material.")
            value = json.loads(line)
            if isinstance(value, Mapping):
                records.append(dict(value))
    return records


def _map_endpoints(api_map: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    modules = api_map.get("modules")
    if not isinstance(modules, Mapping):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for module_name, module in modules.items():
        if not isinstance(module, Mapping):
            continue
        endpoints: Iterable[Any]
        if module_name == Module.PYRAMID.value:
            periods = module.get("periods")
            endpoints = periods.values() if isinstance(periods, Mapping) else ()
        else:
            endpoints = module.get("endpoints") if isinstance(module.get("endpoints"), list) else ()
        for endpoint in endpoints:
            if not isinstance(endpoint, Mapping):
                continue
            raw_url = endpoint.get("request_url")
            if isinstance(raw_url, str):
                result[urlsplit(raw_url).path] = endpoint
    return result


def audit_discovery_map(api_map: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Require all six exact query endpoints and reject write ambiguity."""

    if api_map.get("map_kind") != "discovery" or api_map.get("map_status") not in {"discovered", "verified"}:
        raise ValueError("The target API map is not a discovered/verified discovery map.")
    mapped = _map_endpoints(api_map)
    if set(mapped) != set(_AUDIT_BY_PATH):
        raise ValueError("The API map does not contain exactly the six reviewed target endpoints.")
    for path, audit in _AUDIT_BY_PATH.items():
        endpoint = mapped[path]
        if str(endpoint.get("module")) != audit.module:
            raise ValueError(f"Module mismatch for reviewed endpoint: {path}")
        if str(endpoint.get("method") or "").upper() != "POST":
            raise ValueError(f"Only the reviewed query POST is allowed: {path}")
        if endpoint.get("write_operation_observed") is not False:
            raise ValueError(f"Write-safety observation is missing: {path}")
        if endpoint.get("read_only") is not True or not str(endpoint.get("read_only_justification") or "").strip():
            raise ValueError(f"Explicit read-only approval is missing: {path}")
        if endpoint.get("result") not in {"discovered", "verified"}:
            raise ValueError(f"Endpoint is not discovered/verified: {path}")
    return mapped


def _payload_signature(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _record_template(
    audit: EndpointAudit,
    map_endpoint: Mapping[str, Any],
    record: Mapping[str, Any],
    variant: str,
) -> CompiledReplay:
    raw_url = str(record.get("request_url") or "")
    if not is_ebooking_url(raw_url) or urlsplit(raw_url).path != audit.path:
        raise ValueError(f"Capture URL is outside the reviewed endpoint: {audit.path}")
    if str(record.get("method") or "").upper() != "POST":
        raise ValueError(f"Capture method differs from the reviewed query POST: {audit.path}")
    payload, omitted = _drop_dynamic(record.get("payload"))
    serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if REDACTED in serialized_payload or has_unredacted_sensitive_material(serialized_payload):
        raise ValueError(f"Replay payload is not safe after dynamic-field removal: {audit.path}")
    _validate_exact_capture_shape(audit, map_endpoint, raw_url, payload, variant)
    template = RequestTemplate(
        module=audit.module,
        url=raw_url,
        method="POST",
        body=payload,
        headers=_safe_request_headers(record.get("headers")),
        variant="7d" if audit.module == Module.PYRAMID.value else None,
        read_only=True,
        read_only_justification=audit.justification,
    )
    signature_source = {
        "path": audit.path,
        "query_keys": sorted(key for key, _ in parse_qsl(urlsplit(raw_url).query)),
        "payload": payload,
        "variant": variant,
    }
    return CompiledReplay(
        audit=audit,
        variant=variant,
        template=template,
        baseline_response=record.get("response"),
        template_sha256=_payload_signature(signature_source),
        omitted_dynamic_field_count=omitted,
    )


def compile_target_replays(
    api_map: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
) -> dict[str, tuple[CompiledReplay, ...]]:
    """Compile exact controlled templates without persisting request values."""

    mapped = audit_discovery_map(api_map)
    usable: dict[str, list[Mapping[str, Any]]] = {audit.path: [] for audit in ENDPOINT_AUDITS}
    for record in records:
        raw_url = record.get("request_url")
        path = urlsplit(str(raw_url or "")).path
        if path in usable and str(record.get("method") or "").upper() == "POST":
            status = record.get("status")
            if status is None or (isinstance(status, int) and 200 <= status < 300):
                usable[path].append(record)

    compiled: dict[str, tuple[CompiledReplay, ...]] = {}
    for audit in ENDPOINT_AUDITS:
        candidates = usable[audit.path]
        if not candidates:
            raise ValueError(f"No sanitized discovery capture is available for {audit.path}")
        if audit.endpoint_id == "pyramid_7d":
            summary = [
                item for item in candidates
                if isinstance(item.get("payload"), Mapping) and item["payload"].get("isSummary") is True
            ]
            daily = [
                item for item in candidates
                if isinstance(item.get("payload"), Mapping)
                and item["payload"].get("isSummary") is False
                and item["payload"].get("isChart") is True
            ]
            if not summary or not daily:
                raise ValueError("The 7-day ROAS summary and daily query variants are both required.")
            compiled[audit.endpoint_id] = (
                _record_template(audit, mapped[audit.path], max(summary, key=lambda item: str(item.get("request_time") or "")), "summary"),
                _record_template(audit, mapped[audit.path], max(daily, key=lambda item: str(item.get("request_time") or "")), "daily"),
            )
        else:
            latest = max(candidates, key=lambda item: str(item.get("request_time") or ""))
            compiled[audit.endpoint_id] = (_record_template(audit, mapped[audit.path], latest, "default"),)
    return compiled


def retarget_replay_dates(
    compiled: Mapping[str, tuple[CompiledReplay, ...]],
    as_of_date: str,
) -> dict[str, tuple[CompiledReplay, ...]]:
    """Update only reviewed report dates while preserving request shape."""

    try:
        end_date = _dt.date.fromisoformat(as_of_date)
    except (TypeError, ValueError):
        raise ValueError("as_of_date must be an ISO calendar date.") from None
    start_7d = end_date - _dt.timedelta(days=6)
    output: dict[str, tuple[CompiledReplay, ...]] = {}
    for endpoint_id, steps in compiled.items():
        updated_steps = []
        for step in steps:
            body = dict(step.template.body) if isinstance(step.template.body, Mapping) else step.template.body
            if isinstance(body, dict):
                if endpoint_id == "operating_market_overview":
                    body["startDate"] = end_date.isoformat()
                elif endpoint_id == "operating_flow":
                    body["startDate"] = end_date.isoformat()
                    body["endDate"] = end_date.isoformat()
                elif endpoint_id == "pyramid_7d":
                    body["startDate"] = start_7d.isoformat()
                    body["endDate"] = end_date.isoformat()
            template = replace(step.template, body=body)
            signature = {
                "path": step.audit.path,
                "payload": body,
                "variant": step.variant,
            }
            updated_steps.append(replace(step, template=template, template_sha256=_payload_signature(signature)))
        output[endpoint_id] = tuple(updated_steps)
    return output


def _capture_set_sha256(compiled: Mapping[str, tuple[CompiledReplay, ...]]) -> str:
    evidence = []
    for endpoint_id in sorted(compiled):
        for step in compiled[endpoint_id]:
            projection = _business_projection(endpoint_id, step.baseline_response, step.variant)
            evidence.append({
                "endpoint_id": endpoint_id,
                "variant": step.variant,
                "template_sha256": step.template_sha256,
                "baseline_sha256": _payload_signature(projection),
            })
    return _payload_signature(evidence)


def ensure_capture_set_binding(
    capture_root: str | Path,
    compiled: Mapping[str, tuple[CompiledReplay, ...]],
    *,
    current_hotel_fingerprint: str,
    runtime_flow_hotel_ids: Optional[set[str]] = None,
    allow_create: bool = False,
) -> None:
    """Bind the exact sanitized capture set to the current hotel, locally only."""

    root = Path(capture_root)
    target = root / _CAPTURE_BINDING_FILE
    capture_sha = _capture_set_sha256(compiled)
    if target.exists():
        raw = target.read_text(encoding="utf-8")
        if has_unredacted_sensitive_material(raw):
            raise ValueError("Capture-set binding contains sensitive material.")
        binding = json.loads(raw)
        if not isinstance(binding, Mapping):
            raise ValueError("Capture-set binding is malformed.")
        if binding.get("capture_set_sha256") != capture_sha:
            raise ValueError("Sanitized capture set changed after hotel binding.")
        if binding.get("hotel_identity_digest") != current_hotel_fingerprint:
            raise ValueError("Sanitized capture set is bound to a different hotel.")
        if binding.get("flow_anchor") != "runtime_ids_match_discovery_baseline":
            raise ValueError("Capture-set hotel anchor is missing.")
        return

    if not allow_create:
        raise ValueError("Capture set is not bound to the current hotel; explicit confirmation is required.")
    flow_steps = compiled.get("operating_flow") or ()
    baseline_flow_ids = extract_hotel_ids(flow_steps[0].baseline_response) if flow_steps else set()
    runtime_ids = {str(value).strip().lower() for value in (runtime_flow_hotel_ids or set()) if str(value).strip()}
    if not baseline_flow_ids or runtime_ids != baseline_flow_ids:
        raise ValueError("Runtime flow hotel identifiers do not match the discovery baseline.")
    binding = {
        "version": 1,
        "capture_set_sha256": capture_sha,
        "hotel_identity_digest": current_hotel_fingerprint,
        "flow_anchor": "runtime_ids_match_discovery_baseline",
        "bound_at": _now_iso(),
    }
    serialized = json.dumps(binding, ensure_ascii=False, indent=2)
    if has_unredacted_sensitive_material(serialized):
        raise ValueError("Refusing to write an unsafe capture-set binding.")
    target.write_text(serialized + "\n", encoding="utf-8")


def _schema_matches(value: Any, schema: Mapping[str, Any]) -> bool:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, Mapping):
            return False
        if not set(schema.get("top_level_fields") or ()).issubset(value):
            return False
        data_fields = set(schema.get("data_fields") or ())
        if data_fields:
            data = value.get("data")
            return isinstance(data, Mapping) and data_fields.issubset(data)
        return True
    if expected_type == "array":
        if not isinstance(value, list):
            return False
        row_fields = set(schema.get("row_fields") or ())
        return not row_fields or bool(value) and all(isinstance(row, Mapping) and row_fields.issubset(row) for row in value)
    return False


def _business_code_ok(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return True
    code = value.get("rcode", value.get("code"))
    return code is None or str(code) in {"0", "200"}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        return float(match.group(0)) if match else None
    return None


def _ratio(numerator: Any, denominator: Any) -> Optional[float]:
    top, bottom = _number(numerator), _number(denominator)
    if top is None or bottom is None:
        return None
    if bottom == 0:
        return 0.0 if top == 0 else None
    return top / bottom


def _business_projection(endpoint_id: str, value: Any, variant: str) -> Any:
    if endpoint_id == "operating_advice":
        data = value.get("data") if isinstance(value, Mapping) else None
        if not isinstance(data, Mapping):
            return None
        good, bad = data.get("goodhotelAdviceEntityList"), data.get("badhotelAdviceEntityList")
        if not isinstance(good, list) or not isinstance(bad, list):
            return None
        return {"good": good, "bad": bad}
    if endpoint_id == "operating_market_overview":
        data = value.get("data") if isinstance(value, Mapping) else None
        fields = ("quantity", "rankOfQuantity", "competitorNumber")
        if not isinstance(data, Mapping) or any(data.get(key) is None for key in fields):
            return None
        return {key: data.get(key) for key in fields}
    if endpoint_id == "operating_scores":
        data = value.get("data") if isinstance(value, Mapping) else None
        fields = ("serviceScore", "ctripRatingall")
        if not isinstance(data, Mapping) or any(_number(data.get(key)) is None for key in fields):
            return None
        return {key: data.get(key) for key in fields}
    if endpoint_id == "operating_flow":
        fields = ("date", "listExposure", "detailExposure", "orderFillingNum", "orderSubmitNum", "hotelId")
        if not isinstance(value, list) or len(value) < 2:
            return None
        if any(not isinstance(row, Mapping) or any(row.get(key) is None for key in fields) for row in value[:2]):
            return None
        projected = [{key: row.get(key) for key in fields} for row in value[:2]]
        ratios = []
        for row in value[:2]:
            exposure_conversion = _ratio(row.get("detailExposure"), row.get("listExposure"))
            order_conversion = _ratio(row.get("orderFillingNum"), row.get("detailExposure"))
            if exposure_conversion is None or order_conversion is None:
                return None
            ratios.append({
                "exposure_conversion": exposure_conversion,
                "order_conversion": order_conversion,
            })
        return {"rows": projected, "derived_ratios": ratios}
    if endpoint_id == "pyramid_7d":
        data = value.get("data") if isinstance(value, Mapping) else None
        records = data.get("records") if isinstance(data, Mapping) else None
        if not isinstance(records, list) or not records:
            return None
        fields = ("effectTime", "roas", "cashCost", "bonusCost", "orderAmount")
        projected = [{key: row.get(key) for key in fields} for row in records if isinstance(row, Mapping)]
        if len(projected) != len(records) or any(_number(row.get("roas")) is None for row in records):
            return None
        total = data.get("totalRecords")
        return {"variant": variant, "records": projected, "totalRecords": total}
    if endpoint_id == "violation_list":
        data = value.get("data") if isinstance(value, Mapping) else None
        if not isinstance(data, Mapping):
            return None
        total, records = data.get("totalRecords"), data.get("records")
        if _number(total) != 0 or records not in (None, []):
            return None
        return {"totalRecords": 0, "recordsEmpty": True}
    return None


def _records_complete(endpoint_id: str, value: Any, template: RequestTemplate) -> Optional[bool]:
    if endpoint_id not in {"pyramid_7d", "violation_list"}:
        return None
    data = value.get("data") if isinstance(value, Mapping) else None
    if not isinstance(data, Mapping):
        return False
    records = data.get("records")
    count = 0 if records is None else len(records) if isinstance(records, list) else -1
    total = _number(data.get("totalRecords"))
    body = template.body if isinstance(template.body, Mapping) else {}
    page_size = _number(body.get("pageSize"))
    if total is None or count < 0 or page_size is None:
        return False
    if total > page_size:
        return False
    return count == int(total)


def _hotel_id_sequence(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(row.get("hotelId") or "").strip().lower()
        for row in value
        if isinstance(row, Mapping) and str(row.get("hotelId") or "").strip()
    ]


def _endpoint_schema(api_map_endpoints: Mapping[str, Mapping[str, Any]], path: str) -> Mapping[str, Any]:
    endpoint = api_map_endpoints[path]
    schema = endpoint.get("response_schema")
    return schema if isinstance(schema, Mapping) else {}


def _verified_fields(endpoint_id: str) -> list[str]:
    return {
        "operating_advice": ["operating_reminder"],
        "operating_market_overview": ["departed_room_nights", "room_night_rank"],
        "operating_scores": ["review_score", "psi_score"],
        "operating_flow": [
            "hotel_list_exposure",
            "comp_list_exposure",
            "hotel_exposure_conversion",
            "comp_exposure_conversion",
            "hotel_order_conversion",
            "comp_order_conversion",
        ],
        "pyramid_7d": ["roas_7d", "daily_rows", "totalRecords", "records_complete"],
        "violation_list": ["totalRecords_zero", "records_empty", "status_no_violation"],
    }.get(endpoint_id, [])


def build_silent_comparison_snapshot(
    projections: Mapping[str, list[Any]],
    *,
    hotel: Mapping[str, Any],
    collected_at: str,
    baseline_row_order_confirmed: bool = False,
) -> dict[str, Any]:
    """Reduce in-memory endpoint projections to the legacy comparison schema.

    The returned value contains business metrics but no request data or
    authentication material. Callers must keep it under a gitignored local
    artifact directory.
    """

    advice = (projections.get("operating_advice") or [None])[0]
    market = (projections.get("operating_market_overview") or [None])[0]
    scores = (projections.get("operating_scores") or [None])[0]
    flow = (projections.get("operating_flow") or [None])[0]
    pyramid_steps = projections.get("pyramid_7d") or []
    violation = (projections.get("violation_list") or [None])[0]

    operating_reminder = None
    if isinstance(advice, Mapping) and isinstance(advice.get("bad"), list):
        reminder_count = len(advice["bad"])
        operating_reminder = "无" if reminder_count == 0 else f"经营提醒{reminder_count}项，需点开查看"

    departed_room_nights = market.get("quantity") if isinstance(market, Mapping) else None
    room_night_rank = None
    if isinstance(market, Mapping):
        rank = _number(market.get("rankOfQuantity"))
        competitor_count = _number(market.get("competitorNumber"))
        if rank is not None and competitor_count is not None:
            room_night_rank = f"{int(rank)} / {int(competitor_count)}"

    page_hotel_id = str(hotel.get("hotel_id") or "").strip().lower()
    hotel_row = None
    comp_row = None
    if isinstance(flow, Mapping) and isinstance(flow.get("rows"), list):
        rows = [row for row in flow["rows"] if isinstance(row, Mapping)]
        matching = [row for row in rows if str(row.get("hotelId") or "").strip().lower() == page_hotel_id]
        others = [row for row in rows if row not in matching]
        if len(matching) == 1 and len(others) == 1:
            hotel_row, comp_row = matching[0], others[0]
        elif baseline_row_order_confirmed and len(rows) == 2:
            # Some eBooking shells expose a page hotel identifier from a
            # different namespace. The controlled replay gate has already
            # required the complete ordered projection to equal the manually
            # verified same-hotel discovery baseline, so its reviewed row
            # order is admissible only in that exact case.
            hotel_row, comp_row = rows

    def display_ratio(row: Any, numerator: str, denominator: str) -> Optional[float]:
        if not isinstance(row, Mapping):
            return None
        value = _ratio(row.get(numerator), row.get(denominator))
        # The old collector parses the two-decimal percentage displayed by the
        # page and stores it as a decimal. Match that presentation precision.
        return None if value is None else round(value, 4)

    roas_7d = None
    summary_steps = [
        item for item in pyramid_steps
        if isinstance(item, Mapping) and item.get("variant") == "summary"
    ]
    if len(summary_steps) == 1 and isinstance(summary_steps[0].get("records"), list):
        records = summary_steps[0]["records"]
        if len(records) == 1 and isinstance(records[0], Mapping):
            roas_7d = _number(records[0].get("roas"))

    violation_status = None
    if isinstance(violation, Mapping) and violation.get("totalRecords") == 0 and violation.get("recordsEmpty") is True:
        violation_status = "无违约"

    operating = {
        "operating_reminder": operating_reminder,
        "departed_room_nights": departed_room_nights,
        "room_night_rank": room_night_rank,
        "review_score": scores.get("ctripRatingall") if isinstance(scores, Mapping) else None,
        "psi_score": scores.get("serviceScore") if isinstance(scores, Mapping) else None,
        "hotel_list_exposure": hotel_row.get("listExposure") if isinstance(hotel_row, Mapping) else None,
        "comp_list_exposure": comp_row.get("listExposure") if isinstance(comp_row, Mapping) else None,
        "hotel_exposure_conversion": display_ratio(hotel_row, "detailExposure", "listExposure"),
        "comp_exposure_conversion": display_ratio(comp_row, "detailExposure", "listExposure"),
        "hotel_order_conversion": display_ratio(hotel_row, "orderFillingNum", "detailExposure"),
        "comp_order_conversion": display_ratio(comp_row, "orderFillingNum", "detailExposure"),
    }
    failed_modules = []
    if any(value is None for value in operating.values()):
        failed_modules.append(Module.OPERATING_REPORT.value)
    if roas_7d is None:
        failed_modules.append(Module.PYRAMID.value)
    if violation_status is None:
        failed_modules.append(Module.VIOLATION.value)
    return {
        "platform": "ctrip",
        "hotel": {
            "hotel_id": str(hotel.get("hotel_id") or "")[:200],
            "hotel_name": str(hotel.get("hotel_name") or "")[:200],
        },
        "collected_at": collected_at,
        "operating_report": operating,
        "pyramid": {"roas_7d": roas_7d},
        "violation": {"status": violation_status},
        "collector": {
            "mode": "silent_replay_comparison",
            "failed_modules": failed_modules,
            "warnings": [],
        },
    }


def _page_kind_ok(test_id: str, url: str) -> bool:
    path = urlsplit(url).path.lower()
    if any(marker in path for marker in _TARGET_MODULE_PATH_MARKERS):
        return False
    if test_id == "B":
        return "home" in path
    if test_id in {"C", "D"}:
        return bool(_ORDINARY_PAGE_RE.search(path))
    return False


async def _runtime_page_snapshot(page: Any) -> Mapping[str, Any]:
    script = """
    () => ({
      href: window.location.href,
      hasFocus: document.hasFocus(),
      visibility: document.visibilityState,
      activeTag: document.activeElement ? document.activeElement.tagName : '',
      activeType: document.activeElement ? (document.activeElement.getAttribute('type') || '') : '',
      formCount: document.forms ? document.forms.length : 0
    })
    """
    value = await page.evaluate(script)
    return value if isinstance(value, Mapping) else {}


async def _select_existing_page(browser: Any, page_index: int) -> tuple[Any, Any]:
    candidates = [
        (context, page)
        for context in browser.contexts
        for page in context.pages
        if is_ebooking_url(page.url)
    ]
    if not candidates:
        raise RuntimeError("No existing eBooking page is available.")
    if page_index < 0 or page_index >= len(candidates):
        raise RuntimeError("The requested eBooking page index is unavailable.")
    return candidates[page_index]


def _safe_result_status(results: Iterable[ReplayResult]) -> str:
    statuses = {result.status for result in results}
    if ResultStatus.BLOCKED in statuses:
        return "BLOCKED"
    if ResultStatus.LOGIN_EXPIRED in statuses:
        return "FAIL"
    if statuses.issubset({ResultStatus.SUCCESS, ResultStatus.NO_DATA}):
        return "PASS"
    return "FAIL"


async def run_target_replay(
    *,
    cdp_url: str,
    test_id: str,
    api_map_path: str | Path,
    capture_root: str | Path,
    output_path: str | Path,
    page_index: int = 0,
    manual_refresh_confirmed: bool = False,
    confirm_capture_set_current_hotel: bool = False,
    as_of_date: Optional[str] = None,
    _comparison_snapshot_sink: Optional[list[Mapping[str, Any]]] = None,
) -> Mapping[str, Any]:
    """Run one B/C/D stage without navigating, focusing, clicking, or typing."""

    if test_id not in {"B", "C", "D"}:
        raise ValueError("test_id must be B, C, or D")
    if test_id == "D" and not manual_refresh_confirmed:
        raise ValueError("Test D requires explicit operator confirmation of a manual refresh.")
    api_map = json.loads(Path(api_map_path).read_text(encoding="utf-8"))
    mapped = audit_discovery_map(api_map)
    capture_records = load_sanitized_captures(capture_root)

    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise RuntimeError("Playwright is required for controlled target replay.") from error

    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        context, page = await _select_existing_page(browser, page_index)
        state_before = await inspect_current_page(page)
        if not state_before.is_ebooking or state_before.is_logged_in is not True or not state_before.initialized:
            raise RuntimeError("The selected eBooking page is not logged in and fully initialized.")
        page_hotel = {"hotel_id": state_before.hotel_id, "hotel_name": state_before.hotel_name}
        identity_hash = hotel_fingerprint(page_hotel)
        if not identity_hash:
            raise RuntimeError("Current hotel identity is unavailable.")
        capture_compiled = compile_target_replays(api_map, capture_records)
        binding_path = Path(capture_root) / _CAPTURE_BINDING_FILE
        if binding_path.exists() or not confirm_capture_set_current_hotel:
            ensure_capture_set_binding(
                capture_root,
                capture_compiled,
                current_hotel_fingerprint=identity_hash,
            )
        compiled = retarget_replay_dates(capture_compiled, as_of_date) if as_of_date else capture_compiled

        before = await _runtime_page_snapshot(page)
        exact_before = str(before.get("href") or "")
        if not _page_kind_ok(test_id, exact_before):
            raise RuntimeError(f"Current page does not satisfy the manually confirmed Test {test_id} page state.")
        page_objects_before = tuple(id(item) for item in context.pages)

        endpoint_reports = []
        endpoint_projections: dict[str, list[Any]] = {}
        runtime_flow_hotel_ids: set[str] = set()
        for audit in ENDPOINT_AUDITS:
            steps = compiled[audit.endpoint_id]
            outcomes: list[ReplayResult] = []
            step_checks = []
            for step in steps:
                outcome = await replay_request(page, step.template, before_url=sanitize_url(exact_before))
                outcomes.append(outcome)
                schema_match = _schema_matches(outcome.data, _endpoint_schema(mapped, audit.path))
                projection = _business_projection(audit.endpoint_id, outcome.data, step.variant)
                endpoint_projections.setdefault(audit.endpoint_id, []).append(projection)
                baseline_projection = _business_projection(audit.endpoint_id, step.baseline_response, step.variant)
                data_valid = projection is not None
                baseline_match = (
                    None
                    if as_of_date
                    else data_valid and baseline_projection is not None and projection == baseline_projection
                )
                records_complete = _records_complete(audit.endpoint_id, outcome.data, step.template)
                response_ids = extract_hotel_ids(outcome.data)
                if audit.endpoint_id == "operating_flow":
                    runtime_flow_hotel_ids.update(response_ids)
                baseline_ids = extract_hotel_ids(step.baseline_response)
                page_id = str(state_before.hotel_id or "").strip().lower()
                # Flow analysis can include both the current hotel and a
                # competition row. Accept direct page-id evidence, or an exact
                # identifier-set match to the same-hotel discovery baseline.
                response_hotel_match = None if not response_ids else bool(
                    (page_id and page_id in response_ids)
                    or (baseline_ids and response_ids == baseline_ids)
                )
                response_row_order_match = None
                if audit.endpoint_id == "operating_flow":
                    response_row_order_match = bool(
                        len(_hotel_id_sequence(outcome.data)) == 2
                        and _hotel_id_sequence(outcome.data) == _hotel_id_sequence(step.baseline_response)
                    )
                step_checks.append({
                    "variant": step.variant,
                    "http_status": outcome.http_status,
                    "http_ok": outcome.http_ok,
                    "business_code_ok": _business_code_ok(outcome.data),
                    "response_schema_match": schema_match,
                    "target_data_valid": data_valid,
                    "discovery_baseline_match": baseline_match,
                    "records_complete": records_complete,
                    "response_hotel_match": response_hotel_match,
                    "response_row_order_match": response_row_order_match,
                    "redirected": outcome.redirected,
                    "login_expired": outcome.status == ResultStatus.LOGIN_EXPIRED,
                    "template_sha256": step.template_sha256,
                    "dynamic_fields_omitted_count": step.omitted_dynamic_field_count,
                    "status": outcome.status.value,
                })

            after_step = await _runtime_page_snapshot(page)
            page_unchanged = exact_before == str(after_step.get("href") or "")
            no_new_target = page_objects_before == tuple(id(item) for item in context.pages)
            focus_unchanged = (
                before.get("hasFocus") == after_step.get("hasFocus")
                and before.get("visibility") == after_step.get("visibility")
                and before.get("activeTag") == after_step.get("activeTag")
                and before.get("activeType") == after_step.get("activeType")
            )
            checks_pass = all(
                check["http_ok"]
                and check["business_code_ok"]
                and check["response_schema_match"]
                and check["target_data_valid"]
                and check["discovery_baseline_match"] is not False
                and check["records_complete"] is not False
                and check["response_hotel_match"] is not False
                and check["response_row_order_match"] is not False
                and not check["redirected"]
                and not check["login_expired"]
                for check in step_checks
            )
            verdict = _safe_result_status(outcomes)
            if verdict == "PASS" and not (checks_pass and page_unchanged and no_new_target and focus_unchanged):
                verdict = "FAIL"
            endpoint_reports.append({
                "endpoint_id": audit.endpoint_id,
                "module": audit.module,
                "request_path": audit.path,
                "method": "POST",
                "query_post_audit": "approved_for_controlled_replay",
                "verdict": verdict,
                "verified_fields": _verified_fields(audit.endpoint_id),
                "steps": step_checks,
                "page_url_unchanged": page_unchanged,
                "focus_state_unchanged": focus_unchanged,
                "new_target_opened": not no_new_target,
                "write_side_effect_observed": None,
                "server_side_mutation_check": "NOT_MEASURED",
            })

        after = await _runtime_page_snapshot(page)
        state_after = await inspect_current_page(page)
        same_identity = hotel_fingerprint({"hotel_id": state_after.hotel_id, "hotel_name": state_after.hotel_name}) == identity_hash
        final_focus_unchanged = (
            before.get("hasFocus") == after.get("hasFocus")
            and before.get("visibility") == after.get("visibility")
            and before.get("activeTag") == after.get("activeTag")
            and before.get("activeType") == after.get("activeType")
        )
        replay_pass = bool(
            all(item["verdict"] == "PASS" for item in endpoint_reports)
            and exact_before == str(after.get("href") or "")
            and final_focus_unchanged
            and state_after.is_logged_in is True
            and same_identity
            and page_objects_before == tuple(id(item) for item in context.pages)
        )
        if replay_pass:
            ensure_capture_set_binding(
                capture_root,
                capture_compiled,
                current_hotel_fingerprint=identity_hash,
                runtime_flow_hotel_ids=runtime_flow_hotel_ids,
                allow_create=confirm_capture_set_current_hotel,
            )
        overall_pass = replay_pass and binding_path.exists()
        tested_at = _now_iso()
        if overall_pass and _comparison_snapshot_sink is not None:
            flow_reports = [item for item in endpoint_reports if item["endpoint_id"] == "operating_flow"]
            flow_row_order_confirmed = bool(
                len(flow_reports) == 1
                and all(step.get("response_row_order_match") is True for step in flow_reports[0]["steps"])
            )
            snapshot = build_silent_comparison_snapshot(
                endpoint_projections,
                hotel=page_hotel,
                collected_at=tested_at,
                baseline_row_order_confirmed=flow_row_order_confirmed,
            )
            if snapshot["collector"]["failed_modules"]:
                raise RuntimeError("Silent comparison snapshot is missing required business fields.")
            _comparison_snapshot_sink.append(snapshot)
        report = {
            "test_id": test_id,
            "result": "PASS" if overall_pass else "FAIL",
            "tested_at": tested_at,
            "page_kind": "homepage" if test_id == "B" else "ordinary_order_page",
            "manual_refresh_confirmed": manual_refresh_confirmed if test_id == "D" else None,
            "requested_data_date": as_of_date,
            "page_url_before": sanitize_url(exact_before),
            "page_url_after": sanitize_url(str(after.get("href") or "")),
            "page_url_unchanged": exact_before == str(after.get("href") or ""),
            "focus_state_unchanged": final_focus_unchanged,
            "new_target_opened": page_objects_before != tuple(id(item) for item in context.pages),
            "login_state_valid_after": state_after.is_logged_in is True,
            "hotel_identity_unchanged": same_identity,
            "capture_set_bound_to_current_hotel": True,
            "endpoint_count": len(endpoint_reports),
            "endpoints": endpoint_reports,
            "write_side_effect_observed": None,
            "server_side_mutation_check": "NOT_MEASURED",
            "notes": [
                "Only exact reviewed query POST endpoints were replayed with browser-managed same-origin credentials.",
                "No navigation, reload, focus, click, typing, new target, form interaction, or credential export is performed.",
                "Business values and request payloads are compared in memory and omitted from this report.",
                "When requested_data_date is set, only approved date fields are retargeted and value equality is measured against the current control rather than the older discovery values.",
                "Read-only approval is based on endpoint semantics and observed query behavior; this run does not independently prove absence of server-side mutation.",
            ],
        }
        serialized = json.dumps(report, ensure_ascii=False, indent=2)
        if has_unredacted_sensitive_material(serialized):
            raise ValueError("Refusing to save a replay report containing sensitive material.")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(serialized + "\n", encoding="utf-8")
        return report
    except Exception as error:
        raise RuntimeError(safe_error(error)) from None
    finally:
        # Disconnect the Playwright driver only; never close the user's Chrome.
        await playwright.stop()
