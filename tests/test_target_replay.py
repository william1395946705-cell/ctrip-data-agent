from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from ctrip_silent_poc.cli import _build_parser
from ctrip_silent_poc.redaction import has_unredacted_sensitive_material
from ctrip_silent_poc.replay import RequestTemplate
from ctrip_silent_poc.target_replay import (
    _page_kind_ok,
    _records_complete,
    audit_discovery_map,
    build_silent_comparison_snapshot,
    compile_target_replays,
    ensure_capture_set_binding,
    retarget_replay_dates,
    run_target_replay,
)


def _responses():
    return {
        "/datacenter/api/dataCenter/report/getHotelAdvice": {
            "rcode": 0, "msg": "", "totalPage": 0, "totalRecords": 1,
            "data": {
                "goodhotelAdviceEntityList": [],
                "badhotelAdviceEntityList": [{"kind": "query-only"}],
                "score": 1,
                "scorelevel": 1,
            },
        },
        "/datacenter/api/dataCenter/sale/fetchMarketOverViewV2": {
            "rcode": 0, "msg": "",
            "data": {"quantity": 8, "rankOfQuantity": 2, "competitorNumber": 10},
        },
        "/datacenter/api/dataCenter/report/getDayReportServerQuantity": {
            "rcode": 0, "msg": "",
            "data": {"serviceScore": 4.8, "serviceScoreRank": 2, "ctripRatingall": 4.7},
        },
        "/datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1": [
            {
                "date": "2026-08-28", "listExposure": 100, "detailExposure": 10,
                "flowRate": 10, "orderFillingNum": 2, "orderSubmitNum": 2, "hotelId": "H-1",
            },
            {
                "date": "2026-08-28", "listExposure": 200, "detailExposure": 20,
                "flowRate": 10, "orderFillingNum": 3, "orderSubmitNum": 3, "hotelId": "COMP",
            },
        ],
        "/toolcenter/api/psi/queryEbkPunlishMent": {
            "code": 0, "message": "",
            "data": {
                "records": None, "totalPages": 0, "totalRecords": 0,
                "starNum": 0, "delay": False, "starHotel": False,
            },
        },
    }


def _pyramid_response(summary: bool):
    records = [{
        "cashCost": 1,
        "bonusCost": 0,
        "orderAmount": 3,
        "roas": 3,
        "effectTime": "2026-08-28" if not summary else "2026-08-22/2026-08-28",
    }]
    return {"code": 0, "message": "", "data": {"records": records, "totalPages": 0, "totalRecords": 1}}


def _capture_records():
    responses = _responses()
    records = []

    def add(path, payload, response, *, query=""):
        records.append({
            "module": "pyramid" if "queryCampaign" in path else "violation" if "Punlish" in path else "operating_report",
            "request_url": "https://ebooking.ctrip.com" + path + query,
            "method": "POST",
            "status": 200,
            "headers": {"accept": "application/json", "content-type": "application/json"},
            "payload": payload,
            "response": response,
            "request_time": "2026-08-29T01:00:00+00:00",
        })

    add("/datacenter/api/dataCenter/report/getHotelAdvice", {}, responses["/datacenter/api/dataCenter/report/getHotelAdvice"])
    add(
        "/datacenter/api/dataCenter/sale/fetchMarketOverViewV2",
        {
            "platform": 1, "startDateType": 1, "startDate": "2026-08-28", "needRank": True,
            "spiderVersion": "1", "fingerPrintKeys": "<redacted>", "spiderkey": "<redacted>",
        },
        responses["/datacenter/api/dataCenter/sale/fetchMarketOverViewV2"],
    )
    add(
        "/datacenter/api/dataCenter/report/getDayReportServerQuantity",
        {"spiderVersion": "1", "fingerPrintKeys": "<redacted>", "spiderkey": "<redacted>"},
        responses["/datacenter/api/dataCenter/report/getDayReportServerQuantity"],
    )
    add(
        "/datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1",
        {
            "platform": "1", "startDate": "2026-08-28", "endDate": "2026-08-28",
            "spiderVersion": "1", "fingerPrintKeys": "<redacted>", "spiderkey": "<redacted>",
        },
        responses["/datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1"],
        query="?hostType=1&v=1",
    )
    add(
        "/toolcenter/api/cpc/queryCampaignReportList",
        {
            "startDate": "2026-08-22", "endDate": "2026-08-28", "convertPeriod": 3,
            "isSummary": True, "pageIdx": 1, "pageSize": 10,
        },
        _pyramid_response(True),
        query="?hostType=1&v=1",
    )
    add(
        "/toolcenter/api/cpc/queryCampaignReportList",
        {
            "campaignId": "",
            "startDate": "2026-08-22", "endDate": "2026-08-28", "convertPeriod": 3,
            "keyword": "", "keywordType": "", "isSummary": False, "isChart": True,
            "pageIdx": 1, "pageSize": 500, "premiumCodes": [],
        },
        _pyramid_response(False),
        query="?hostType=1&v=1",
    )
    add(
        "/toolcenter/api/psi/queryEbkPunlishMent",
        {
            "selectedCategory": [], "pageIndex": 1, "pageSize": "30",
            "selectedStatus": "", "defectCategoryId": 0, "subCategoryId": 0,
        },
        responses["/toolcenter/api/psi/queryEbkPunlishMent"],
        query="?hostType=1&v=1",
    )
    return records


class FakePage:
    url = "https://ebooking.ctrip.com/home/mainland"

    def __init__(self):
        self.forbidden_calls = []
        self.responses = _responses()

    async def evaluate(self, script, args=None):
        if args is not None:
            path = urlsplit(args["url"]).path
            if path == "/toolcenter/api/cpc/queryCampaignReportList":
                body = args.get("body") or {}
                return {
                    "status": 200,
                    "url": args["url"],
                    "redirected": False,
                    "responseType": "basic",
                    "location": "",
                    "contentType": "application/json",
                    "data": _pyramid_response(bool(body.get("isSummary"))),
                }
            return {
                "status": 200,
                "url": args["url"],
                "redirected": False,
                "responseType": "basic",
                "location": "",
                "contentType": "application/json",
                "data": self.responses[path],
            }
        if "formCount" in script:
            return {
                "href": self.url,
                "hasFocus": False,
                "visibility": "visible",
                "activeTag": "BODY",
                "activeType": "",
                "formCount": 0,
            }
        return {
            "ready": True,
            "has_body": True,
            "has_app": True,
            "is_logged_in": True,
            "hotel_id": "H-1",
            "hotel_name": "测试酒店",
        }

    async def goto(self, *args, **kwargs):
        self.forbidden_calls.append("goto")

    async def reload(self, *args, **kwargs):
        self.forbidden_calls.append("reload")

    async def bring_to_front(self, *args, **kwargs):
        self.forbidden_calls.append("bring_to_front")

    async def click(self, *args, **kwargs):
        self.forbidden_calls.append("click")

    async def type(self, *args, **kwargs):
        self.forbidden_calls.append("type")


class TargetReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api_map = json.loads(Path("ctrip_api_map.json").read_text(encoding="utf-8"))

    def test_compiler_allows_exact_six_queries_and_drops_dynamic_fields(self):
        mapped = audit_discovery_map(self.api_map)
        compiled = compile_target_replays(self.api_map, _capture_records())
        self.assertEqual(len(mapped), 6)
        self.assertEqual(len(compiled), 6)
        self.assertEqual(len(compiled["pyramid_7d"]), 2)
        market = compiled["operating_market_overview"][0]
        self.assertNotIn("fingerPrintKeys", market.template.body)
        self.assertNotIn("spiderkey", market.template.body)
        self.assertEqual(market.omitted_dynamic_field_count, 2)
        self.assertTrue(market.template.read_only)

    def test_retarget_dates_changes_only_reviewed_date_fields(self):
        compiled = compile_target_replays(self.api_map, _capture_records())
        changed = retarget_replay_dates(compiled, "2026-09-02")
        market = changed["operating_market_overview"][0].template.body
        flow = changed["operating_flow"][0].template.body
        pyramid = changed["pyramid_7d"][0].template.body
        self.assertEqual(market["startDate"], "2026-09-02")
        self.assertEqual(flow["startDate"], "2026-09-02")
        self.assertEqual(flow["endDate"], "2026-09-02")
        self.assertEqual(pyramid["startDate"], "2026-08-27")
        self.assertEqual(pyramid["endDate"], "2026-09-02")
        self.assertEqual(market["platform"], compiled["operating_market_overview"][0].template.body["platform"])
        self.assertNotEqual(
            changed["operating_market_overview"][0].template_sha256,
            compiled["operating_market_overview"][0].template_sha256,
        )
        with self.assertRaisesRegex(ValueError, "ISO calendar date"):
            retarget_replay_dates(compiled, "09/02/2026")

    def test_compiler_rejects_query_and_body_shape_injection(self):
        records = _capture_records()
        flow = next(item for item in records if "queryFlowTransfor" in item["request_url"])
        flow["request_url"] += "&unexpected=1"
        with self.assertRaisesRegex(ValueError, "Unexpected query parameters"):
            compile_target_replays(self.api_map, records)

        records = _capture_records()
        flow = next(item for item in records if "queryFlowTransfor" in item["request_url"])
        flow["request_url"] = flow["request_url"].replace("hostType=1", "hostType=../unexpected")
        with self.assertRaisesRegex(ValueError, "Unexpected query values"):
            compile_target_replays(self.api_map, records)

        records = _capture_records()
        violation = next(item for item in records if "queryEbkPunlishMent" in item["request_url"])
        violation["payload"]["unexpected"] = "query-only"
        with self.assertRaisesRegex(ValueError, "Unexpected request body fields"):
            compile_target_replays(self.api_map, records)

        records = _capture_records()
        market = next(item for item in records if "fetchMarketOverViewV2" in item["request_url"])
        market["payload"]["startDateType"] = "1"
        with self.assertRaisesRegex(ValueError, "Unexpected request body types"):
            compile_target_replays(self.api_map, records)

    def test_pagination_completeness_fails_closed(self):
        template = RequestTemplate(
            module="violation",
            url="https://ebooking.ctrip.com/toolcenter/api/psi/queryEbkPunlishMent",
            method="POST",
            body={"pageSize": "30"},
            read_only=True,
            read_only_justification="Reviewed query",
        )
        self.assertTrue(_records_complete("violation_list", {"data": {"records": [], "totalRecords": 0}}, template))
        self.assertFalse(_records_complete("violation_list", {"data": {"records": [], "totalRecords": 31}}, template))
        self.assertFalse(_records_complete("violation_list", {"data": {"records": None}}, template))

    def test_capture_set_binding_rejects_wrong_hotel_or_changed_capture(self):
        compiled = compile_target_replays(self.api_map, _capture_records())
        with tempfile.TemporaryDirectory() as directory:
            ensure_capture_set_binding(
                directory,
                compiled,
                current_hotel_fingerprint="digest-one",
                runtime_flow_hotel_ids={"H-1", "COMP"},
                allow_create=True,
            )
            ensure_capture_set_binding(
                directory,
                compiled,
                current_hotel_fingerprint="digest-one",
            )
            with self.assertRaisesRegex(ValueError, "different hotel"):
                ensure_capture_set_binding(
                    directory,
                    compiled,
                    current_hotel_fingerprint="digest-two",
                )

            changed = _capture_records()
            market = next(item for item in changed if "fetchMarketOverViewV2" in item["request_url"])
            market["payload"]["startDate"] = "2026-08-27"
            with self.assertRaisesRegex(ValueError, "changed after hotel binding"):
                ensure_capture_set_binding(
                    directory,
                    compile_target_replays(self.api_map, changed),
                    current_hotel_fingerprint="digest-one",
                )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "do not match the discovery baseline"):
                ensure_capture_set_binding(
                    directory,
                    compiled,
                    current_hotel_fingerprint="digest-two",
                    runtime_flow_hotel_ids={"H-2", "COMP"},
                    allow_create=True,
                )

    def test_map_audit_rejects_write_ambiguity(self):
        copied = json.loads(json.dumps(self.api_map))
        copied["modules"]["violation"]["endpoints"][0]["write_operation_observed"] = None
        with self.assertRaisesRegex(ValueError, "Write-safety"):
            audit_discovery_map(copied)

        copied = json.loads(json.dumps(self.api_map))
        copied["modules"]["violation"]["endpoints"][0]["read_only"] = False
        with self.assertRaisesRegex(ValueError, "read-only approval"):
            audit_discovery_map(copied)

    def test_cli_requires_explicit_test_id_and_manual_refresh_flag(self):
        args = _build_parser().parse_args([
            "replay-targets",
            "--cdp-url", "http://127.0.0.1:9223",
            "--test-id", "D",
            "--manual-refresh-confirmed",
            "--confirm-capture-set-current-hotel",
        ])
        self.assertEqual(args.test_id, "D")
        self.assertTrue(args.manual_refresh_confirmed)
        self.assertTrue(args.confirm_capture_set_current_hotel)

        live = _build_parser().parse_args([
            "compare-live",
            "--cdp-url", "http://127.0.0.1:9223",
            "--runtime-dir", "/local/legacy/runtime",
        ])
        self.assertEqual(live.command, "compare-live")

        manual = _build_parser().parse_args([
            "compare-live",
            "--cdp-url", "http://127.0.0.1:9223",
            "--manual-control", "artifacts/manual.json",
            "--test-id", "B",
        ])
        self.assertEqual(manual.manual_control, "artifacts/manual.json")
        self.assertEqual(manual.test_id, "B")

        stability = _build_parser().parse_args([
            "stability-test",
            "--cdp-url", "http://127.0.0.1:9223",
            "--page-kind", "inventory",
            "--rounds", "3",
        ])
        self.assertEqual(stability.command, "stability-test")
        self.assertEqual(stability.page_kind, "inventory")
        self.assertEqual(stability.rounds, 3)

    def test_comparison_snapshot_uses_page_hotel_row_and_display_precision(self):
        projections = {
            "operating_advice": [{"good": [], "bad": [{"safe": True}]}],
            "operating_market_overview": [{"quantity": 8, "rankOfQuantity": 2, "competitorNumber": 10}],
            "operating_scores": [{"serviceScore": 4.8, "ctripRatingall": 4.7}],
            "operating_flow": [{
                "rows": _responses()["/datacenter/api/inland/marketanalysis/flowanalysis/queryFlowTransforNewV1"],
                "derived_ratios": [],
            }],
            "pyramid_7d": [
                {"variant": "summary", "records": [{"roas": 3}], "totalRecords": 1},
                {"variant": "daily", "records": [{"roas": 3}], "totalRecords": 1},
            ],
            "violation_list": [{"totalRecords": 0, "recordsEmpty": True}],
        }
        snapshot = build_silent_comparison_snapshot(
            projections,
            hotel={"hotel_id": "H-1", "hotel_name": "测试酒店"},
            collected_at="2026-08-29T00:00:00+00:00",
        )
        operating = snapshot["operating_report"]
        self.assertEqual(operating["operating_reminder"], "经营提醒1项，需点开查看")
        self.assertEqual(operating["departed_room_nights"], 8)
        self.assertEqual(operating["room_night_rank"], "2 / 10")
        self.assertEqual(operating["hotel_exposure_conversion"], 0.1)
        self.assertEqual(operating["comp_order_conversion"], 0.15)
        self.assertEqual(snapshot["pyramid"]["roas_7d"], 3.0)
        self.assertEqual(snapshot["violation"]["status"], "无违约")
        self.assertEqual(snapshot["collector"]["failed_modules"], [])

    async def test_d_requires_manual_confirmation_and_page_gate_rejects_target_modules(self):
        with self.assertRaisesRegex(ValueError, "manual refresh"):
            await run_target_replay(
                cdp_url="http://127.0.0.1:9223",
                test_id="D",
                api_map_path="ctrip_api_map.json",
                capture_root="artifacts/target-discovery",
                output_path="artifacts/replay-target-endpoints/rejected.json",
            )
        self.assertTrue(_page_kind_ok("B", "https://ebooking.ctrip.com/home/mainland"))
        self.assertTrue(_page_kind_ok("C", "https://ebooking.ctrip.com/ebkorder/order-list"))
        self.assertTrue(_page_kind_ok("D", "https://ebooking.ctrip.com/ebkorder/order-list"))
        self.assertTrue(_page_kind_ok("C", "https://ebooking.ctrip.com/roomstatus/calendar", "inventory"))
        self.assertTrue(_page_kind_ok("C", "https://ebooking.ctrip.com/roomrate/price", "price"))
        self.assertTrue(_page_kind_ok("C", "https://ebooking.ctrip.com/ebkovsroom/inventory/calendar", "price"))
        self.assertFalse(_page_kind_ok("C", "https://ebooking.ctrip.com/ebkovsroom/inventory/calendar/other", "price"))
        self.assertFalse(_page_kind_ok("C", "https://ebooking.ctrip.com/ebkorder/order-list", "price"))
        self.assertFalse(_page_kind_ok("B", "https://ebooking.ctrip.com/toolcenter/cpc/report"))
        self.assertFalse(_page_kind_ok("C", "https://ebooking.ctrip.com/datacenter/inland/businessreport"))

    async def test_home_replay_keeps_page_focus_and_target_unchanged(self):
        page = FakePage()
        context = types.SimpleNamespace(pages=[page])
        browser = types.SimpleNamespace(contexts=[context], close_count=0)

        class Chromium:
            async def connect_over_cdp(self, cdp_url):
                return browser

        class Driver:
            def __init__(self):
                self.chromium = Chromium()
                self.stop_count = 0

            async def stop(self):
                self.stop_count += 1

        driver = Driver()

        class Starter:
            async def start(self):
                return driver

        package = types.ModuleType("playwright")
        package.__path__ = []
        async_api = types.ModuleType("playwright.async_api")
        async_api.async_playwright = lambda: Starter()
        package.async_api = async_api

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captures = root / "capture" / "captures.sanitized.jsonl"
            captures.parent.mkdir(parents=True)
            captures.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in _capture_records()) + "\n",
                encoding="utf-8",
            )
            output = root / "test_b.json"
            snapshots = []
            with patch.dict(sys.modules, {"playwright": package, "playwright.async_api": async_api}):
                report = await run_target_replay(
                    cdp_url="http://127.0.0.1:9223",
                    test_id="B",
                    api_map_path="ctrip_api_map.json",
                    capture_root=root,
                    output_path=output,
                    confirm_capture_set_current_hotel=True,
                    _comparison_snapshot_sink=snapshots,
                )

            self.assertEqual(report["result"], "PASS")
            self.assertEqual(report["endpoint_count"], 6)
            self.assertTrue(report["page_url_unchanged"])
            self.assertTrue(report["focus_state_unchanged"])
            self.assertFalse(report["new_target_opened"])
            self.assertTrue(report["capture_set_bound_to_current_hotel"])
            self.assertIsNone(report["write_side_effect_observed"])
            self.assertEqual(report["server_side_mutation_check"], "NOT_MEASURED")
            self.assertEqual(page.forbidden_calls, [])
            self.assertEqual(driver.stop_count, 1)
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0]["operating_report"]["departed_room_nights"], 8)
            self.assertFalse(has_unredacted_sensitive_material(output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
