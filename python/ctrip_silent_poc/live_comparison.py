"""Fail-closed comparison for one legacy run bracketed by Silent Replay."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any


CORE_FIELDS = (
    ("经营提醒", "operating_report", "operating_reminder"),
    ("昨日离店间夜", "operating_report", "departed_room_nights"),
    ("竞争圈排名", "operating_report", "room_night_rank"),
    ("点评分", "operating_report", "review_score"),
    ("PSI", "operating_report", "psi_score"),
    ("本店曝光", "operating_report", "hotel_list_exposure"),
    ("竞争圈曝光", "operating_report", "comp_list_exposure"),
    ("本店曝光转化率", "operating_report", "hotel_exposure_conversion"),
    ("竞争圈曝光转化率", "operating_report", "comp_exposure_conversion"),
    ("本店下单转化率", "operating_report", "hotel_order_conversion"),
    ("竞争圈下单转化率", "operating_report", "comp_order_conversion"),
    ("7 天 ROAS", "pyramid", "roas_7d"),
    ("违约状态", "violation", "status"),
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        return float(match.group(0)) if match else None
    return None


def _rank(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _strict_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(%)?\s*", value.replace(",", ""))
    if not match:
        return None
    number = float(match.group(1))
    return number / 100 if match.group(2) else number


def values_equal(left: Any, right: Any) -> bool:
    if left == right:
        return True
    left_rank, right_rank = _rank(left), _rank(right)
    if left_rank is not None or right_rank is not None:
        return left_rank is not None and right_rank is not None and left_rank == right_rank
    left_number, right_number = _strict_number(left), _strict_number(right)
    if left_number is not None and right_number is not None:
        return math.isclose(left_number, right_number, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(left, str) and isinstance(right, str):
        return re.sub(r"\s+", "", left).lower() == re.sub(r"\s+", "", right).lower()
    return False


def _field(result: Mapping[str, Any], section: str, name: str) -> tuple[bool, Any]:
    section_value = result.get(section)
    if not isinstance(section_value, Mapping):
        return False, None
    return name in section_value, section_value.get(name)


def _hotel_identity(result: Mapping[str, Any]) -> tuple[str, str]:
    hotel = result.get("hotel")
    if not isinstance(hotel, Mapping):
        return "", ""
    hotel_id = str(hotel.get("hotel_id") or "").strip().lower()
    hotel_name = re.sub(r"\s+", "", str(hotel.get("hotel_name") or "")).lower()
    return hotel_id, hotel_name


def _same_hotel(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_id, left_name = _hotel_identity(left)
    right_id, right_name = _hotel_identity(right)
    if left_id and right_id:
        return left_id == right_id
    return bool(left_name and right_name and left_name == right_name)


def _time_drift_supported(old: Any, before: Any, after: Any) -> bool:
    if values_equal(before, after):
        return False
    if values_equal(old, before) or values_equal(old, after):
        return True
    if any(_rank(value) is not None for value in (old, before, after)):
        return False
    old_number, before_number, after_number = _strict_number(old), _strict_number(before), _strict_number(after)
    if None in (old_number, before_number, after_number):
        return False
    return min(before_number, after_number) <= old_number <= max(before_number, after_number)


def compare_legacy_with_bracketed_silent(
    legacy: Mapping[str, Any],
    silent_before: Mapping[str, Any],
    silent_after: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare 13 requested fields without inferring drift from timestamps alone."""

    if (
        not _same_hotel(silent_before, silent_after)
        or not _same_hotel(legacy, silent_before)
        or not _same_hotel(legacy, silent_after)
    ):
        raise ValueError("Legacy and Silent snapshots do not identify the same hotel.")

    fields: dict[str, dict[str, Any]] = {}
    mismatches: list[str] = []
    time_drift: list[str] = []
    exact = 0
    for label, section, name in CORE_FIELDS:
        old_present, old_value = _field(legacy, section, name)
        before_present, before_value = _field(silent_before, section, name)
        after_present, after_value = _field(silent_after, section, name)
        path = f"{section}.{name}"
        reason = None
        if not old_present:
            status = "MISMATCH"
            reason = "legacy_field_unavailable"
        elif not before_present or not after_present:
            status = "MISMATCH"
            reason = "silent_field_unavailable"
        elif values_equal(old_value, after_value):
            status = "EQUAL"
            exact += 1
        elif _time_drift_supported(old_value, before_value, after_value):
            status = "TIME_DRIFT"
            time_drift.append(path)
        else:
            status = "MISMATCH"
            reason = "stable_value_difference" if values_equal(before_value, after_value) else "unexplained_value_difference"
        if status == "MISMATCH":
            mismatches.append(path)
        fields[path] = {
            "label": label,
            "old": old_value,
            "silent": after_value,
            "silent_before": before_value,
            "equal": status == "EQUAL",
            "status": status,
            "reason": reason,
        }

    notes = [
        "TIME_DRIFT requires a changed Silent before/after value that brackets or equals the control value.",
        "A timestamp gap alone never converts a mismatch into TIME_DRIFT.",
    ]
    if not _field(legacy, "operating_report", "departed_room_nights")[0]:
        notes.append("The old collector does not currently expose departed_room_nights as a standalone output field.")
    return {
        "result": "PASS" if not mismatches else "FAIL",
        "hotel_identity_match": True,
        "field_count": len(CORE_FIELDS),
        "exact_match_count": exact,
        "time_drift_count": len(time_drift),
        "mismatch_count": len(mismatches),
        "fields": fields,
        "time_drift_fields": time_drift,
        "mismatches": mismatches,
        "collection_times": {
            "silent_before": silent_before.get("collected_at"),
            "legacy": legacy.get("collected_at"),
            "silent_after": silent_after.get("collected_at"),
        },
        "notes": notes,
    }
