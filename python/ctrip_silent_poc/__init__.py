"""Ctrip eBooking silent-collector proof of concept.

The package deliberately keeps the existing Playwright collector independent.
It provides small adapters that can be attached to an existing BrowserContext
and uses the current page's authenticated fetch context for replay.
"""

from .api_map import build_api_map, write_api_map
from .comparator import compare_results, compute_category
from .inspector import NetworkInspector
from .legacy_bridge import attach_inspector, attach_to_browser_manager
from .legacy_control import adapt_legacy_batch_result, legacy_control_ready, run_legacy_control_from_authorized_page
from .models import (
    ApiMapEntry,
    CaptureRecord,
    CollectorResult,
    Module,
    ReplayResult,
    ResultStatus,
)
from .replay import (
    InMemoryRequestVault,
    RequestTemplate,
    SilentCollector,
    classify_replay_response,
    inspect_current_page,
    is_ebooking_url,
    replay_request,
)
from .testsuite import SilentTestRunner

__all__ = [
    "ApiMapEntry",
    "adapt_legacy_batch_result",
    "attach_inspector",
    "attach_to_browser_manager",
    "CaptureRecord",
    "CollectorResult",
    "InMemoryRequestVault",
    "Module",
    "NetworkInspector",
    "ReplayResult",
    "RequestTemplate",
    "ResultStatus",
    "SilentCollector",
    "SilentTestRunner",
    "build_api_map",
    "compare_results",
    "classify_replay_response",
    "compute_category",
    "inspect_current_page",
    "is_ebooking_url",
    "legacy_control_ready",
    "run_legacy_control_from_authorized_page",
    "replay_request",
    "write_api_map",
]
