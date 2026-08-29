"""Manual-state-gated Test A-D harness.

The harness intentionally does not navigate or refresh a tab.  The operator
must put the authorized account into the requested state and confirm it; the
runner then calls an injected executor (normally :class:`SilentCollector`).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from .comparator import ComparisonReport, compare_results
from .redaction import safe_error, sanitize_url


@dataclass(frozen=True)
class SilentTestCase:
    test_id: str
    name: str
    required_page_state: str
    mode: str
    instruction: str


TEST_CASES = (
    SilentTestCase("A", "natural business-page capture", "target business page", "natural", "请人工进入目标业务页面并等待自然请求完成，然后确认。"),
    SilentTestCase("B", "homepage silent replay", "eBooking home page", "silent", "请人工回到 eBooking 首页；不要由程序切页，然后确认。"),
    SilentTestCase("C", "ordinary-page silent replay", "orders/room/price/other page", "silent", "请人工停留在订单、房态、价格或其他普通页面，然后确认。"),
    SilentTestCase("D", "ordinary-page after manual refresh", "ordinary page after refresh", "silent", "请人工刷新普通 eBooking 页面并等待稳定；程序不会刷新，然后确认。"),
)


@dataclass
class SilentTestCaseResult:
    test_id: str
    name: str
    confirmed: bool
    skipped: bool = False
    result: Any = None
    current_page_url_before: Optional[str] = None
    current_page_url_after: Optional[str] = None
    current_page_unchanged: bool = True
    warnings: list[str] = field(default_factory=list)
    comparison: Optional[ComparisonReport] = None

    def to_dict(self) -> Dict[str, Any]:
        value = {
            "test_id": self.test_id,
            "name": self.name,
            "confirmed": self.confirmed,
            "skipped": self.skipped,
            "result": self.result.to_dict() if hasattr(self.result, "to_dict") else self.result,
            "current_page_url_before": self.current_page_url_before,
            "current_page_url_after": self.current_page_url_after,
            "current_page_unchanged": self.current_page_unchanged,
            "warnings": list(self.warnings),
        }
        if self.comparison is not None:
            value["comparison"] = self.comparison.to_dict()
        return value


def _read_url(page: Any) -> Optional[str]:
    try:
        value = getattr(page, "url")
        value = value() if callable(value) else value
        return sanitize_url(value) if value else None
    except Exception:
        return None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class SilentTestRunner:
    """Execute manually confirmed test states without browser control."""

    def __init__(self, cases=TEST_CASES) -> None:
        self.cases = tuple(cases)

    async def run_case(
        self,
        case: SilentTestCase,
        page: Any,
        execute: Callable[[SilentTestCase], Any],
        *,
        confirmed: bool = False,
        old_result: Optional[Mapping[str, Any]] = None,
    ) -> SilentTestCaseResult:
        before = _read_url(page)
        if not confirmed:
            return SilentTestCaseResult(case.test_id, case.name, False, skipped=True, current_page_url_before=before, current_page_url_after=before, warnings=["Manual page-state confirmation was not provided."])
        try:
            value = await _maybe_await(execute(case))
        except Exception as error:
            after = _read_url(page)
            return SilentTestCaseResult(case.test_id, case.name, True, result=None, current_page_url_before=before, current_page_url_after=after, current_page_unchanged=before == after, warnings=["Test executor failed: " + safe_error(error)])
        after = _read_url(page)
        unchanged = before == after
        warnings = [] if unchanged else ["Current page URL changed; silent test is invalid."]
        comparison = None
        value_dict = value.to_dict() if hasattr(value, "to_dict") else value
        if old_result is not None and isinstance(value_dict, Mapping):
            comparison = compare_results(old_result, value_dict)
        return SilentTestCaseResult(case.test_id, case.name, True, result=value_dict, current_page_url_before=before, current_page_url_after=after, current_page_unchanged=unchanged, warnings=warnings, comparison=comparison)

    async def run_all(
        self,
        page: Any,
        execute: Callable[[SilentTestCase], Any],
        *,
        confirmations: Optional[Mapping[str, bool]] = None,
        old_result: Optional[Mapping[str, Any]] = None,
    ) -> list[SilentTestCaseResult]:
        confirmations = confirmations or {}
        results = []
        for case in self.cases:
            results.append(await self.run_case(case, page, execute, confirmed=bool(confirmations.get(case.test_id, False)), old_result=old_result))
        return results


def interactive_confirmation(case: SilentTestCase, input_fn: Callable[[str], str] = input) -> bool:
    """Optional CLI adapter; pressing Enter confirms, any other answer skips."""

    answer = input_fn(case.instruction + " 按回车继续，输入 n 跳过：")
    return answer.strip().lower() not in {"n", "no", "否", "跳过"}
