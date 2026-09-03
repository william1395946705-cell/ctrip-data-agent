from __future__ import annotations

import copy
import unittest

from ctrip_silent_poc.live_comparison import CORE_FIELDS, compare_legacy_with_bracketed_silent, values_equal


def _silent() -> dict:
    return {
        "hotel": {"hotel_id": "H-1", "hotel_name": "测试酒店"},
        "collected_at": "2026-08-29T01:00:00+00:00",
        "operating_report": {
            "operating_reminder": "无",
            "departed_room_nights": 8,
            "room_night_rank": "2 / 10",
            "review_score": 4.7,
            "psi_score": 4.8,
            "hotel_list_exposure": 100,
            "comp_list_exposure": 200,
            "hotel_exposure_conversion": 0.1,
            "comp_exposure_conversion": 0.1,
            "hotel_order_conversion": 0.2,
            "comp_order_conversion": 0.15,
        },
        "pyramid": {"roas_7d": 3.0},
        "violation": {"status": "无违约"},
    }


def _legacy() -> dict:
    value = _silent()
    value["hotel"] = {"hotel_id": "", "hotel_name": "测 试 酒 店"}
    value["collected_at"] = "2026-08-29T01:01:00+00:00"
    del value["operating_report"]["departed_room_nights"]
    return value


class LiveComparisonTests(unittest.TestCase):
    def test_rank_and_reminder_text_are_not_reduced_to_first_number(self):
        self.assertTrue(values_equal("7 / 19", "7/19"))
        self.assertFalse(values_equal("7 / 19", 7))
        self.assertFalse(values_equal("经营提醒3项，需点开查看", 3))
        self.assertTrue(values_equal("18.59%", 0.1859))

    def test_exact_fields_and_legacy_schema_gap_are_counted(self):
        report = compare_legacy_with_bracketed_silent(_legacy(), _silent(), _silent())
        self.assertEqual(report["field_count"], len(CORE_FIELDS))
        self.assertEqual(report["exact_match_count"], 12)
        self.assertEqual(report["time_drift_count"], 0)
        self.assertEqual(report["mismatch_count"], 1)
        departed = report["fields"]["operating_report.departed_room_nights"]
        self.assertEqual(departed["status"], "MISMATCH")
        self.assertEqual(departed["reason"], "legacy_field_unavailable")

    def test_time_drift_requires_changed_bracketing_silent_values(self):
        before = _silent()
        after = copy.deepcopy(before)
        legacy = _legacy()
        after["operating_report"]["hotel_list_exposure"] = 110
        legacy["operating_report"]["hotel_list_exposure"] = 105
        report = compare_legacy_with_bracketed_silent(legacy, before, after)
        field = report["fields"]["operating_report.hotel_list_exposure"]
        self.assertEqual(field["status"], "TIME_DRIFT")
        self.assertFalse(field["equal"])

    def test_stable_difference_is_a_real_mismatch(self):
        legacy = _legacy()
        legacy["operating_report"]["review_score"] = 4.6
        report = compare_legacy_with_bracketed_silent(legacy, _silent(), _silent())
        field = report["fields"]["operating_report.review_score"]
        self.assertEqual(field["status"], "MISMATCH")
        self.assertEqual(field["reason"], "stable_value_difference")

    def test_hotel_mismatch_is_rejected(self):
        after = _silent()
        after["hotel"]["hotel_id"] = "H-2"
        with self.assertRaisesRegex(ValueError, "same hotel"):
            compare_legacy_with_bracketed_silent(_legacy(), _silent(), after)


if __name__ == "__main__":
    unittest.main()
