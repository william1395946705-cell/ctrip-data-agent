"""Stable, serialization-safe data contracts for the POC.

No model in this module stores cookies, passwords, sessions or authorization
headers.  The only object allowed to retain such values is the explicit
in-memory request vault in :mod:`replay`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


class Module(str, Enum):
    OPERATING_REPORT = "operating_report"
    PYRAMID = "pyramid"
    VIOLATION = "violation"
    UNKNOWN = "unknown"


class ResultStatus(str, Enum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    LOADING = "loading"
    REQUEST_FAILED = "request_failed"
    LOGIN_EXPIRED = "login_expired"
    BLOCKED = "blocked"
    UNVERIFIED = "unverified"


@dataclass
class CaptureRecord:
    """A fully sanitized request/response observation."""

    module: str
    request_url: str
    method: str
    payload_schema: Any
    response_schema: Any
    required_page_context: str
    can_call_from_any_ebooking_page: Optional[bool]
    result: str
    notes: List[str] = field(default_factory=list)
    variant: Optional[str] = None
    status: Optional[int] = None
    content_type: Optional[str] = None
    response_url: Optional[str] = None
    trigger_page: Optional[str] = None
    request_time: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    payload: Any = None
    response: Any = None
    # Test A provenance.  The batch id and one-way hotel fingerprint are safe
    # to serialize and prevent a later comparison from mixing sessions or
    # hotels.  Raw identity values remain in the browser/process only.
    test_a_batch_id: Optional[str] = None
    hotel_fingerprint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ApiMapEntry:
    module: str
    request_url: str
    method: str
    payload_schema: Any
    response_schema: Any
    required_page_context: str
    can_call_from_any_ebooking_page: Optional[bool]
    result: str
    notes: List[str] = field(default_factory=list)
    variant: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReplayResult:
    """Outcome of one in-page ``fetch`` without navigating the tab."""

    module: str
    status: ResultStatus
    http_status: Optional[int] = None
    content_type: Optional[str] = None
    response_url: Optional[str] = None
    data: Any = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    http_ok: bool = False
    business_ok: bool = False
    current_page_url_before: Optional[str] = None
    current_page_url_after: Optional[str] = None
    current_page_unchanged: bool = True
    elapsed_ms: Optional[int] = None

    def to_safe_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe result; error text is already sanitized upstream."""

        return {
            "module": self.module,
            "status": self.status.value,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "response_url": self.response_url,
            "data": self.data,
            "error": self.error,
            "warnings": list(self.warnings),
            "http_ok": self.http_ok,
            "business_ok": self.business_ok,
            "current_page_url_before": self.current_page_url_before,
            "current_page_url_after": self.current_page_url_after,
            "current_page_unchanged": self.current_page_unchanged,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class CollectorResult:
    """The stable output shape consumed by the extension/debug UI."""

    platform: str = "ctrip"
    hotel: Dict[str, Any] = field(default_factory=lambda: {"hotel_id": "", "hotel_name": ""})
    collected_at: Optional[str] = None
    operating_report: Dict[str, Any] = field(
        default_factory=lambda: {
            "operating_reminder": None,
            "room_night_rank": None,
            "review_score": None,
            "psi_score": None,
            "hotel_list_exposure": None,
            "comp_list_exposure": None,
            "hotel_exposure_conversion": None,
            "comp_exposure_conversion": None,
            "hotel_order_conversion": None,
            "comp_order_conversion": None,
            "category": None,
        }
    )
    pyramid: Dict[str, Any] = field(
        default_factory=lambda: {"roas_7d": None, "roas_30d": None, "no_investment": False}
    )
    violation: Dict[str, Any] = field(default_factory=lambda: {"status": None})
    collector: Dict[str, Any] = field(
        default_factory=lambda: {
            "mode": "silent",
            "current_page_unchanged": True,
            "failed_modules": [],
            "warnings": [],
        }
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def as_mapping(value: Any) -> Mapping[str, Any]:
    """Narrow helper used by extractors for JSON-like values."""

    return value if isinstance(value, Mapping) else {}
