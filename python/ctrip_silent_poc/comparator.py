"""Old Playwright vs silent collector field comparison."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional


OPERATING_FIELDS = (
    "operating_reminder",
    "room_night_rank",
    "review_score",
    "psi_score",
    "hotel_list_exposure",
    "comp_list_exposure",
    "hotel_exposure_conversion",
    "comp_exposure_conversion",
    "hotel_order_conversion",
    "comp_order_conversion",
    "category",
)
PYRAMID_FIELDS = ("roas_7d", "roas_30d", "no_investment")
VIOLATION_FIELDS = ("status",)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = value.replace(",", "")
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def _equal(left: Any, right: Any) -> bool:
    if left == right:
        return True
    lnum, rnum = _number(left), _number(right)
    if lnum is not None and rnum is not None:
        return math.isclose(lnum, rnum, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(left, str) and isinstance(right, str):
        return re.sub(r"\s+", "", left).lower() == re.sub(r"\s+", "", right).lower()
    return False


def compute_category(hotel_exposure: Any, comp_exposure: Any, hotel_order_conversion: Any, comp_order_conversion: Any) -> Optional[str]:
    """Compute the four-quadrant category from hotel-vs-competition metrics."""

    hotel_exposure_num, comp_exposure_num = _number(hotel_exposure), _number(comp_exposure)
    hotel_conversion_num, comp_conversion_num = _number(hotel_order_conversion), _number(comp_order_conversion)
    if None in (hotel_exposure_num, comp_exposure_num, hotel_conversion_num, comp_conversion_num):
        return None
    high_exposure = hotel_exposure_num >= comp_exposure_num
    high_conversion = hotel_conversion_num >= comp_conversion_num
    if high_exposure and high_conversion:
        return "高曝高转"
    if high_exposure:
        return "高曝低转"
    if high_conversion:
        return "低曝高转"
    return "低曝低转"


@dataclass
class ComparisonReport:
    equal: bool
    fields: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"equal": self.equal, "fields": self.fields, "mismatches": self.mismatches, "missing": self.missing}


def _section(result: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = result.get(key, {})
    return value if isinstance(value, Mapping) else {}


def compare_results(old: Mapping[str, Any], silent: Mapping[str, Any]) -> ComparisonReport:
    """Compare every required metric and independently recompute categories."""

    report = ComparisonReport(equal=True)
    sections = (("operating_report", OPERATING_FIELDS), ("pyramid", PYRAMID_FIELDS), ("violation", VIOLATION_FIELDS))
    for section_name, fields in sections:
        left_section = _section(old, section_name)
        right_section = _section(silent, section_name)
        for field_name in fields:
            left = left_section.get(field_name)
            right = right_section.get(field_name)
            path = f"{section_name}.{field_name}"
            present_left = field_name in left_section
            present_right = field_name in right_section
            same = present_left and present_right and _equal(left, right)
            report.fields[path] = {"equal": same, "old": left, "silent": right}
            if not present_left or not present_right:
                report.missing.append(path)
            if not same:
                report.mismatches.append(path)
    # Category is derived and must be compared to the derived values even when
    # one collector omitted it in its payload.
    for label, result in (("old", old), ("silent", silent)):
        operating = _section(result, "operating_report")
        derived = compute_category(
            operating.get("hotel_list_exposure"),
            operating.get("comp_list_exposure"),
            operating.get("hotel_order_conversion"),
            operating.get("comp_order_conversion"),
        )
        given = operating.get("category")
        if derived is not None and given is not None and not _equal(derived, given):
            key = f"operating_report.category_{label}_derived"
            report.fields[key] = {"equal": False, "expected": derived, "actual": given}
            report.mismatches.append(key)
    report.equal = not report.mismatches and not report.missing
    return report
