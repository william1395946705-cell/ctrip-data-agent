from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from ctrip_silent_poc.api_map import build_api_map, write_api_map
from ctrip_silent_poc.cli import _build_parser, _build_test_a_from_captures, _run_observe
from ctrip_silent_poc.comparator import compare_results, compute_category
from ctrip_silent_poc.inspector import NetworkInspector, hotel_fingerprint
from ctrip_silent_poc.legacy_bridge import attach_inspector
from ctrip_silent_poc.legacy_control import adapt_legacy_batch_result, legacy_control_ready
from ctrip_silent_poc.models import CaptureRecord, Module, ResultStatus
from ctrip_silent_poc.redaction import REDACTED, redact_value, safe_headers, sanitize_url
from ctrip_silent_poc.replay import InMemoryRequestVault, RequestTemplate, SilentCollector, classify_replay_response, normalize_violation, replay_request
from ctrip_silent_poc.testsuite import SilentTestRunner


class FakeRequest:
    resource_type = "xhr"
    url = "https://ebooking.ctrip.com/api/operating-report?token=SECRET&date=2026-08-28"
    method = "POST"
    post_data = json.dumps({"hotelId": "H-1", "csrfToken": "do-not-log", "date": "2026-08-28"})
    headers = {"accept": "application/json", "content-type": "application/json", "authorization": "Bearer TOP_SECRET", "cookie": "sid=SECRET", "x-csrf-token": "CSRF_SECRET", "x-requested-with": "XMLHttpRequest"}


class FakeResponse:
    request = FakeRequest()
    url = FakeRequest.url
    status = 200
    headers = {"content-type": "application/json"}
    frame = None

    async def json(self):
        return {"success": True, "data": {"hotel_list_exposure": 10, "comp_list_exposure": 5, "csrfToken": "VERY_SECRET"}}


class FakeContext:
    def __init__(self):
        self.callback = None

    def on(self, event, callback):
        assert event == "response"
        self.callback = callback

    def off(self, event, callback):
        if self.callback is callback:
            self.callback = None


class SyncResponse:
    request = FakeRequest()
    url = FakeRequest.url
    status = 200
    headers = {"content-type": "application/json"}
    frame = None

    def json(self):
        return {"经营提醒": "请关注", "昨日离店间夜竞争圈排名": 2}


class SyncContext(FakeContext):
    pass


class FakePage:
    def __init__(self, url="https://ebooking.ctrip.com/home"):
        self.url = url
        self.evaluate_calls = []

    async def evaluate(self, script, args=None):
        self.evaluate_calls.append((script, args))
        if args is None:
            return {"ready": True, "has_body": True, "has_app": True, "is_logged_in": True, "hotel_id": "H-1", "hotel_name": "测试酒店"}
        endpoint = args.get("url", "")
        if "pyramid" in endpoint:
            return {"status": 200, "url": endpoint, "contentType": "application/json", "data": {"roas_7d": 0}}
        if "violation" in endpoint:
            return {"status": 200, "url": endpoint, "contentType": "application/json", "data": {"status": "无违约"}}
        return {"status": 200, "url": endpoint, "contentType": "application/json", "data": {"hotel_list_exposure": 10, "comp_list_exposure": 5, "hotel_order_conversion": 0.2, "comp_order_conversion": 0.1}}


def expected_result():
    return {
        "operating_report": {"operating_reminder": "x", "room_night_rank": 1, "review_score": 4.8, "psi_score": 80, "hotel_list_exposure": 10, "comp_list_exposure": 5, "hotel_exposure_conversion": 0.1, "comp_exposure_conversion": 0.1, "hotel_order_conversion": 0.2, "comp_order_conversion": 0.1, "category": "高曝高转"},
        "pyramid": {"roas_7d": 0, "roas_30d": 0, "no_investment": True},
        "violation": {"status": "无违约"},
    }


def approved_get(module, url, *, variant=None):
    return RequestTemplate(module, url, variant=variant, read_only=True)


class SilentPocTests(unittest.IsolatedAsyncioTestCase):
    async def test_inspector_redacts_and_keeps_safe_schemas(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = InMemoryRequestVault()
            inspector = NetworkInspector(
                request_vault=vault,
                test_a_batch_id="batch-1",
                hotel_fingerprint=hotel_fingerprint({"hotel_id": "H-1", "hotel_name": "测试酒店"}),
            )
            record = await inspector.capture_response(FakeResponse(), module_hint="operating_report", trigger_page="https://ebooking.ctrip.com/home")
            self.assertEqual(record.module, Module.OPERATING_REPORT.value)
            self.assertEqual(record.required_page_context, "specific_module_page")
            self.assertEqual(record.test_a_batch_id, "batch-1")
            self.assertEqual(record.hotel_fingerprint, hotel_fingerprint({"hotel_id": "H-1", "hotel_name": "测试酒店"}))
            self.assertNotIn("SECRET", json.dumps(record.to_dict(), ensure_ascii=False))
            self.assertEqual(record.payload["csrfToken"], REDACTED)
            self.assertIn("cookie_header_observed", record.request_context_types)
            self.assertIn("authorization_header_observed", record.request_context_types)
            self.assertIn("csrf_header_observed", record.request_context_types)
            self.assertIn("csrf_request_field_observed", record.request_context_types)
            self.assertEqual(record.payload_schema["type"], "object")
            self.assertNotIn("csrfToken", json.dumps(record.payload_schema))
            self.assertIsNone(vault.get(Module.OPERATING_REPORT.value))
            output = Path(directory) / "captures.jsonl"
            inspector.write_jsonl(output)
            self.assertNotIn("TOP_SECRET", output.read_text())

    async def test_text_and_malformed_html_credentials_never_reach_jsonl(self):
        class TextResponse(FakeResponse):
            headers = {"content-type": "text/html"}

            async def json(self):
                raise ValueError("not json")

            async def text(self):
                return '<div data-token="TOP_SECRET" data-api-key="LEAK_API_KEY" data-access-key=LEAK_ACCESS>cookie: sid=LEAK</div>'

        with tempfile.TemporaryDirectory() as directory:
            inspector = NetworkInspector()
            await inspector.capture_response(TextResponse(), module_hint="operating_report")
            output = Path(directory) / "captures.jsonl"
            inspector.write_jsonl(output)
            saved = output.read_text(encoding="utf-8")
            self.assertNotIn("TOP_SECRET", saved)
            self.assertNotIn("sid=LEAK", saved)
            self.assertNotIn("LEAK_API_KEY", saved)
            self.assertNotIn("LEAK_ACCESS", saved)
        parsed = redact_value('{"csrfToken":"TOP_SECRET","cookie":"sid=LEAK"}')
        self.assertEqual(parsed["csrfToken"], REDACTED)
        self.assertEqual(parsed["cookie"], REDACTED)

    async def test_inspector_context_listener_is_passive(self):
        context = FakeContext()
        inspector = NetworkInspector().attach(context)
        self.assertIsNotNone(context.callback)
        context.callback(FakeResponse())
        await inspector.drain()
        self.assertEqual(len(inspector.records), 1)
        inspector.set_capture_enabled(False)
        context.callback(FakeResponse())
        await inspector.drain()
        self.assertEqual(len(inspector.records), 1)
        inspector.detach()
        self.assertIsNone(context.callback)

    async def test_inspector_records_only_inside_explicit_capture_window(self):
        context = FakeContext()
        inspector = NetworkInspector(capture_enabled=False).attach(context)
        context.callback(FakeResponse())
        await inspector.drain()
        self.assertEqual(inspector.records, [])
        inspector.set_capture_enabled(True)
        context.callback(FakeResponse())
        await inspector.drain()
        self.assertEqual(len(inspector.records), 1)

    async def test_inspector_listener_rejects_cross_origin_noise(self):
        context = FakeContext()
        inspector = NetworkInspector().attach(context)
        request = type("Request", (), {
            "resource_type": "xhr",
            "url": "https://m.ctrip.com/restapi/noise",
            "method": "POST",
            "post_data": "{}",
            "headers": {},
        })()
        response = type("Response", (), {
            "request": request,
            "url": request.url,
            "status": 200,
            "headers": {"content-type": "application/json"},
            "frame": None,
            "json": lambda self: {"status": "ok"},
        })()
        context.callback(response)
        await inspector.drain()
        self.assertEqual(inspector.records, [])

    async def test_response_body_keywords_do_not_classify_unrelated_order_api(self):
        request = type("Request", (), {
            "resource_type": "xhr",
            "url": "https://ebooking.ctrip.com/restapi/soa2/27204/getOrderDetail",
            "method": "POST",
            "post_data": "{}",
            "headers": {},
        })()
        response = type("Response", (), {
            "request": request,
            "url": request.url,
            "status": 200,
            "headers": {"content-type": "application/json"},
            "frame": None,
            "json": lambda self: {"promotion": "金字塔", "notice": "违约提醒"},
        })()
        record = await NetworkInspector().capture_response(
            response,
            trigger_page="https://ebooking.ctrip.com/ebkorderv3/domestic",
        )
        self.assertEqual(record.module, Module.UNKNOWN.value)

    async def test_target_page_route_does_not_classify_unrelated_request(self):
        request = type("Request", (), {
            "resource_type": "xhr",
            "url": "https://ebooking.ctrip.com/restapi/soa2/24278/getMultiNotifyMessage",
            "method": "POST",
            "post_data": "{}",
            "headers": {},
        })()
        response = type("Response", (), {
            "request": request,
            "url": request.url,
            "status": 200,
            "headers": {"content-type": "application/json"},
            "frame": None,
            "json": lambda self: {"success": True},
        })()
        record = await NetworkInspector().capture_response(
            response,
            trigger_page="https://ebooking.ctrip.com/merchant/violation",
        )
        self.assertEqual(record.module, Module.UNKNOWN.value)
        self.assertEqual(record.required_page_context, "specific_module_page")

    async def test_generic_cpc_routes_are_not_roas_but_report_route_is(self):
        async def capture(path):
            request = type("Request", (), {
                "resource_type": "xhr",
                "url": "https://ebooking.ctrip.com" + path,
                "method": "POST",
                "post_data": "{}",
                "headers": {},
            })()
            response = type("Response", (), {
                "request": request,
                "url": request.url,
                "status": 200,
                "headers": {"content-type": "application/json"},
                "frame": None,
                "json": lambda self: {"code": 0, "data": {}},
            })()
            return await NetworkInspector().capture_response(response)

        unrelated = await capture("/toolcenter/api/cpc/getAdMangerQrCode")
        report = await capture("/toolcenter/api/cpc/queryCampaignReportList")
        self.assertEqual(unrelated.module, Module.UNKNOWN.value)
        self.assertEqual(report.module, Module.PYRAMID.value)

    async def test_generic_datacenter_route_is_not_operating_report(self):
        async def capture(path):
            request = type("Request", (), {
                "resource_type": "xhr",
                "url": "https://ebooking.ctrip.com" + path,
                "method": "POST",
                "post_data": "{}",
                "headers": {},
            })()
            response = type("Response", (), {
                "request": request,
                "url": request.url,
                "status": 200,
                "headers": {"content-type": "application/json"},
                "frame": None,
                "json": lambda self: {"code": 0, "data": {}},
            })()
            return await NetworkInspector().capture_response(response)

        unrelated = await capture("/datacenter/api/dataCenter/report/getVisitorTitle")
        report = await capture("/datacenter/api/dataCenter/report/getHotelAdvice")
        self.assertEqual(unrelated.module, Module.UNKNOWN.value)
        self.assertEqual(report.module, Module.OPERATING_REPORT.value)

    async def test_observe_never_controls_page_or_closes_browser(self):
        class ObservePage:
            url = "https://ebooking.ctrip.com/home/mainland"

            def __init__(self):
                self.evaluate_count = 0
                self.wait_count = 0
                self.forbidden_calls = []

            async def evaluate(self, script):
                self.evaluate_count += 1
                return {
                    "ready": True,
                    "has_body": True,
                    "has_app": True,
                    "is_logged_in": True,
                    "hotel_id": "H-1",
                    "hotel_name": "测试酒店",
                }

            async def wait_for_timeout(self, milliseconds):
                self.wait_count += 1

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

        class ObserveContext(FakeContext):
            def __init__(self, page):
                super().__init__()
                self.pages = [page]

        class ObserveBrowser:
            def __init__(self, context):
                self.contexts = [context]
                self.close_count = 0

            async def close(self):
                self.close_count += 1

        page = ObservePage()
        context = ObserveContext(page)
        browser = ObserveBrowser(context)

        class Chromium:
            async def connect_over_cdp(self, cdp_url):
                return browser

        class PlaywrightDriver:
            def __init__(self):
                self.chromium = Chromium()
                self.stop_count = 0

            async def stop(self):
                self.stop_count += 1

        driver = PlaywrightDriver()

        class Starter:
            async def start(self):
                return driver

        package = types.ModuleType("playwright")
        package.__path__ = []
        async_api = types.ModuleType("playwright.async_api")
        async_api.async_playwright = lambda: Starter()
        package.async_api = async_api
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules,
            {"playwright": package, "playwright.async_api": async_api},
        ), patch("builtins.print"), patch("builtins.input", return_value=""):
            result = await _run_observe(argparse.Namespace(
                cdp_url="http://127.0.0.1:9223",
                page_index=0,
                seconds=1,
                until_enter=True,
                output_dir=directory,
            ))
            summary = json.loads((Path(directory) / "passive_observation.json").read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(summary["result"], "NOT VERIFIED")
        self.assertEqual(page.evaluate_count, 2)
        self.assertEqual(page.wait_count, 0)
        self.assertEqual(page.forbidden_calls, [])
        self.assertEqual(browser.close_count, 0)
        self.assertEqual(driver.stop_count, 1)

    async def test_discovery_candidate_requires_exact_human_read_only_approval(self):
        vault = InMemoryRequestVault()
        inspector = NetworkInspector(request_vault=vault)
        record = await inspector.capture_response(FakeResponse(), module_hint="operating_report")
        discovery = build_api_map([record])
        endpoint = discovery["modules"]["operating_report"]["endpoints"][0]
        self.assertEqual(inspector.approve_from_api_map(discovery), 0)
        endpoint["read_only"] = True
        endpoint["read_only_justification"] = "Reviewed reporting query with no state mutation"
        self.assertEqual(inspector.approve_from_api_map(discovery), 0)
        endpoint["controlled_silent_test"] = True
        self.assertEqual(inspector.approve_from_api_map(discovery, controlled_test=True), 1)
        approved = vault.get("operating_report")
        self.assertIsNotNone(approved)
        self.assertTrue(approved.read_only)

    async def test_replay_statuses_and_silent_collector(self):
        self.assertEqual(classify_replay_response("pyramid", {"status": 200, "data": {"message": "暂无数据"}}).status, ResultStatus.SUCCESS)
        self.assertEqual(classify_replay_response("pyramid", {"status": 202, "data": {}}).status, ResultStatus.LOADING)
        self.assertEqual(classify_replay_response("pyramid", {"status": 401, "data": {}}).status, ResultStatus.LOGIN_EXPIRED)
        self.assertEqual(classify_replay_response("pyramid", {"status": 429, "data": {}}).status, ResultStatus.BLOCKED)
        self.assertEqual(classify_replay_response("pyramid", {"status": 500, "data": {}}).status, ResultStatus.REQUEST_FAILED)
        self.assertEqual(classify_replay_response("pyramid", {"status": 200, "url": "https://ebooking.ctrip.com/login", "redirected": True, "data": ""}).status, ResultStatus.LOGIN_EXPIRED)
        self.assertEqual(classify_replay_response("pyramid", {"status": 200, "data": {}}).status, ResultStatus.SUCCESS)
        page = FakePage()
        templates = {"operating_report": approved_get("operating_report", "https://ebooking.ctrip.com/api/operating-report"), "pyramid": approved_get("pyramid", "https://ebooking.ctrip.com/api/pyramid", variant="7d"), "pyramid_30d": approved_get("pyramid", "https://ebooking.ctrip.com/api/pyramid?range=30d", variant="30d"), "violation": approved_get("violation", "https://ebooking.ctrip.com/api/violation")}
        result = await SilentCollector().collect(page, templates, hotel={"hotel_id": "H-1", "hotel_name": "测试酒店"}, force=True)
        self.assertTrue(result.collector["current_page_unchanged"])
        self.assertEqual(result.pyramid["roas_7d"], 0)
        self.assertEqual(result.pyramid["roas_30d"], 0)
        self.assertTrue(result.pyramid["no_investment"])
        self.assertEqual(result.operating_report["category"], "高曝高转")
        self.assertGreaterEqual(len(page.evaluate_calls), 5)

    async def test_failed_roas_does_not_become_no_investment(self):
        class FailedPage(FakePage):
            async def evaluate(self, script, args=None):
                if args is not None and "pyramid" in args.get("url", ""):
                    return {"status": 503, "url": args["url"], "data": {"message": "loading"}}
                return await super().evaluate(script, args)

        result = await SilentCollector().collect(FailedPage(), {"pyramid": approved_get("pyramid", "https://ebooking.ctrip.com/api/pyramid")}, hotel={"hotel_id": "H-1"}, force=True)
        self.assertFalse(result.pyramid["no_investment"])
        self.assertTrue(any("not complete" in warning for warning in result.collector["warnings"]))

    async def test_replay_refuses_unapproved_writes_and_cross_origin(self):
        page = FakePage()
        for template in (
            RequestTemplate("operating_report", "https://ebooking.ctrip.com/api/x", method="PUT"),
            RequestTemplate("operating_report", "https://example.com/api/x", read_only=True),
            RequestTemplate("operating_report", "https://ebooking.ctrip.com/api/x"),
        ):
            result = await replay_request(page, template)
            self.assertEqual(result.status, ResultStatus.BLOCKED)
        self.assertEqual(page.evaluate_calls, [])

    async def test_unrelated_no_data_does_not_mean_no_investment(self):
        class UnrelatedPage(FakePage):
            async def evaluate(self, script, args=None):
                if args is not None and "pyramid" in args.get("url", ""):
                    return {"status": 200, "url": args["url"], "contentType": "application/json", "data": {"message": "其他模块暂无数据"}}
                return await super().evaluate(script, args)

        result = await SilentCollector().collect(
            UnrelatedPage(),
            {"pyramid": approved_get("pyramid", "https://ebooking.ctrip.com/api/pyramid")},
            hotel={"hotel_id": "H-1"},
            force=True,
        )
        self.assertFalse(result.pyramid["no_investment"])
        self.assertIn("pyramid", result.collector["failed_modules"])

    async def test_test_runner_requires_confirmation(self):
        page = FakePage()
        runner = SilentTestRunner()
        executed = []

        async def execute(case):
            executed.append(case.test_id)
            return expected_result()

        skipped = await runner.run_case(runner.cases[0], page, execute)
        self.assertTrue(skipped.skipped)
        self.assertFalse(executed)
        result = await runner.run_case(runner.cases[0], page, execute, confirmed=True, old_result=expected_result())
        self.assertTrue(result.current_page_unchanged)
        self.assertTrue(result.comparison and result.comparison.equal)
        self.assertEqual(executed, ["A"])


class PureSilentPocTests(unittest.TestCase):
    def test_legacy_adapter_allowlists_fields_and_never_guesses_pyramid(self):
        raw = {
            "hotel_name": "测试酒店",
            "account": "DO_NOT_COPY",
            "profile_path": "/DO/NOT/COPY",
            "status": "完整成功",
            "fields": {
                "经营提醒": "x", "昨日离店间夜排名": "1", "点评分": 4.8, "PSI分": 80,
                "本店列表页曝光": 10, "竞争圈列表页曝光量": 5,
                "本店曝光转化率": 0.1, "竞争圈曝光转化率": 0.1,
                "本店下单转化率": 0.2, "竞争圈下单转化率": 0.1,
                "分类": "高曝高转", "金字塔": 2.5, "违约看板": "无",
            },
            "missing_modules": [],
        }
        unknown = adapt_legacy_batch_result(raw, pyramid_observation="unknown")
        self.assertFalse(legacy_control_ready(unknown))
        self.assertNotIn("DO_NOT_COPY", json.dumps(unknown, ensure_ascii=False))
        self.assertNotIn("/DO/NOT/COPY", json.dumps(unknown, ensure_ascii=False))
        proven = adapt_legacy_batch_result(raw, pyramid_observation="7d")
        self.assertTrue(legacy_control_ready(proven))
        self.assertEqual(proven["pyramid"], {"roas_7d": 2.5, "roas_30d": None, "no_investment": False})
        self.assertEqual(proven["violation"]["status"], "无违约")

    def test_test_a_result_is_built_from_current_capture_records(self):
        hotel = {"hotel_id": "H-1", "hotel_name": "测试酒店"}
        base = {
            "request_url": "https://ebooking.ctrip.com/api/report",
            "method": "GET",
            "payload_schema": {"type": "null"},
            "response_schema": {"type": "object"},
            "required_page_context": "specific_module_page",
            "can_call_from_any_ebooking_page": None,
            "result": "unverified",
            "status": 200,
            "request_time": "2026-08-28T08:00:00+00:00",
            "test_a_batch_id": "batch-1",
            "hotel_fingerprint": hotel_fingerprint(hotel),
        }
        records = [
            CaptureRecord(module="operating_report", response=expected_result()["operating_report"], **base),
            CaptureRecord(module="pyramid", variant="7d", response={"roas_7d": 0}, **base),
            CaptureRecord(module="pyramid", variant="30d", response={"roas_30d": 0}, **base),
            CaptureRecord(module="violation", response={"status": "无违约"}, **base),
        ]
        natural = _build_test_a_from_captures(records, hotel, batch_id="batch-1")
        self.assertEqual(natural.collector["mode"], "natural")
        self.assertFalse(natural.collector["failed_modules"])
        self.assertTrue(natural.pyramid["no_investment"])
        self.assertTrue(compare_results(expected_result(), natural.to_dict()).equal)

    def test_test_a_rejects_cross_batch_cross_hotel_and_missing_identity(self):
        hotel = {"hotel_id": "H-1", "hotel_name": "测试酒店"}
        base = {
            "request_url": "https://ebooking.ctrip.com/api/report",
            "method": "GET",
            "payload_schema": {"type": "null"},
            "response_schema": {"type": "object"},
            "required_page_context": "specific_module_page",
            "can_call_from_any_ebooking_page": None,
            "result": "unverified",
            "status": 200,
            "request_time": "2026-08-28T08:00:00+00:00",
            "test_a_batch_id": "batch-1",
            "hotel_fingerprint": hotel_fingerprint(hotel),
        }
        valid = CaptureRecord(module="operating_report", response={"hotel_list_exposure": 1}, **base)
        other_batch = CaptureRecord(module="violation", response={"status": "无违约"}, **{**base, "test_a_batch_id": "batch-old"})
        rejected = _build_test_a_from_captures([valid, other_batch], hotel, batch_id="batch-1")
        self.assertEqual(set(rejected.collector["failed_modules"]), {"operating_report", "pyramid", "violation"})
        self.assertTrue(any("batch" in warning for warning in rejected.collector["warnings"]))

        other_hotel = {"hotel_id": "H-2", "hotel_name": "另一家酒店"}
        foreign_id = CaptureRecord(
            module="operating_report",
            response={"hotelId": "H-2", "hotel_list_exposure": 1},
            **{**base, "hotel_fingerprint": hotel_fingerprint(other_hotel)},
        )
        rejected_hotel = _build_test_a_from_captures([foreign_id], hotel, batch_id="batch-1")
        self.assertEqual(set(rejected_hotel.collector["failed_modules"]), {"operating_report", "pyramid", "violation"})
        self.assertTrue(any("hotel" in warning for warning in rejected_hotel.collector["warnings"]))

        missing_identity = CaptureRecord(
            module="operating_report", response={"hotel_list_exposure": 1}, **{**base, "hotel_fingerprint": None}
        )
        rejected_missing = _build_test_a_from_captures([missing_identity], hotel, batch_id="batch-1")
        self.assertEqual(set(rejected_missing.collector["failed_modules"]), {"operating_report", "pyramid", "violation"})

    def test_sync_playwright_callback_and_legacy_bridge(self):
        context = SyncContext()
        manager = type("Manager", (), {"_context": context})()
        inspector = attach_inspector(manager, module_hint="operating_report")
        context.callback(SyncResponse())
        self.assertEqual(len(inspector.records), 1)
        self.assertEqual(inspector.records[0].module, "operating_report")

    def test_pyramid_vault_keeps_7d_and_30d_templates_separate(self):
        vault = InMemoryRequestVault()
        first = FakeResponse()
        first.request = type("Request", (), {"url": "https://ebooking.ctrip.com/api/query", "method": "POST", "post_data": '{"range":"7d"}', "headers": {}})()
        second = FakeResponse()
        second.request = type("Request", (), {"url": "https://ebooking.ctrip.com/api/query", "method": "POST", "post_data": '{"range":"30d"}', "headers": {}})()
        first_candidate = vault.candidate_from_response("pyramid", first, variant="7d")
        second_candidate = vault.candidate_from_response("pyramid", second, variant="30d")
        self.assertIsNone(vault.get("pyramid_7d"))
        vault.approve_candidate(first_candidate, read_only_justification="Reviewed reporting query only")
        vault.approve_candidate(second_candidate, read_only_justification="Reviewed reporting query only")
        self.assertIsNotNone(vault.get("pyramid_7d"))
        self.assertIsNotNone(vault.get("pyramid_30d"))
        self.assertIs(vault.get("pyramid"), vault.get("pyramid_7d"))

    def test_url_headers_and_recursive_redaction(self):
        self.assertNotIn("SECRET", sanitize_url("https://ebooking.ctrip.com/a?token=SECRET&date=2026-08-28"))
        self.assertNotIn("LEAK_API", sanitize_url("https://ebooking.ctrip.com/a?api-key=LEAK_API"))
        self.assertNotIn("LEAK_PRIVATE", sanitize_url("https://ebooking.ctrip.com/a?private_key=LEAK_PRIVATE"))
        self.assertTrue(sanitize_url("https://ebooking.ctrip.com/a?date=2026-08-28").endswith("date=2026-08-28"))
        headers = safe_headers(FakeRequest.headers)
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)
        self.assertEqual(redact_value({"access_token": "secret", "hotel_id": "H-1"})["access_token"], REDACTED)
        self.assertEqual(redact_value({"ssoTicket": "secret"})["ssoTicket"], REDACTED)
        self.assertEqual(redact_value({"api-key": "LEAK_API"})["api-key"], REDACTED)
        self.assertEqual(redact_value({"private_key": "LEAK_PRIVATE"})["private_key"], REDACTED)
        self.assertEqual(redact_value({"auth-value": "LEAK_AUTH"})["auth-value"], REDACTED)

    def test_dynamic_session_identifiers_are_redacted_but_business_ids_remain(self):
        safe_url = sanitize_url(
            "https://ebooking.ctrip.com/a?_fxpcqlniredt=LEAK_TRACE&x-traceID=LEAK_TRACE_2&date=2026-08-29"
        )
        self.assertNotIn("LEAK_TRACE", safe_url)
        self.assertIn("date=2026-08-29", safe_url)
        safe_payload = redact_value({
            "reqHead": {
                "ubt": {"fp": "LEAK_FP", "oneId": "LEAK_ONEID", "pvid": 123, "sid": "LEAK_SID"},
                "client": {"clientId": "LEAK_CLIENT"},
            },
            "fingerPrintKeys": "LEAK_FINGERPRINT",
            "spiderkey": "LEAK_SPIDERKEY",
            "spiderVersion": "SAFE_VERSION",
            "orderId": "ORDER-1",
            "hotelId": "HOTEL-1",
        })
        self.assertNotIn("LEAK_", json.dumps(safe_payload))
        self.assertEqual(safe_payload["fingerPrintKeys"], REDACTED)
        self.assertEqual(safe_payload["spiderkey"], REDACTED)
        self.assertEqual(safe_payload["spiderVersion"], "SAFE_VERSION")
        self.assertEqual(safe_payload["orderId"], "ORDER-1")
        self.assertEqual(safe_payload["hotelId"], "HOTEL-1")

    def test_api_map_resanitizes_request_url(self):
        capture = {
            "module": "operating_report",
            "request_url": "https://ebooking.ctrip.com/api/operating-report?x-traceID=LEAK_TRACE&oneId=LEAK_ONEID&date=2026-08-29",
            "method": "GET",
        }
        serialized = json.dumps(build_api_map([capture]))
        self.assertNotIn("LEAK_TRACE", serialized)
        self.assertNotIn("LEAK_ONEID", serialized)
        self.assertIn("date=2026-08-29", serialized)

    def test_observe_cli_is_available_as_passive_discovery_command(self):
        args = _build_parser().parse_args([
            "observe",
            "--cdp-url", "http://127.0.0.1:9223",
            "--seconds", "45",
            "--until-enter",
            "--output-dir", "artifacts/test-observe",
        ])
        self.assertEqual(args.command, "observe")
        self.assertEqual(args.seconds, 45)
        self.assertTrue(args.until_enter)

    def test_api_map_starts_unverified_and_requires_measurement(self):
        capture = {"module": "operating_report", "request_url": "https://ebooking.ctrip.com/api/operating-report", "method": "POST", "payload_schema": {"type": "object"}, "response_schema": {"type": "object"}, "required_page_context": "specific_module_page"}
        initial = build_api_map([capture])
        self.assertEqual(initial["map_kind"], "discovery")
        initial_module = initial["modules"]["operating_report"]
        self.assertEqual(initial_module["result"], "unverified")
        self.assertIsNone(initial_module["can_call_from_any_ebooking_page"])
        self.assertEqual(len(initial_module["endpoints"]), 1)
        self.assertFalse(initial_module["endpoints"][0]["read_only"])
        self.assertEqual(initial_module["endpoints"][0]["request_context_types"], [])
        measured = build_api_map([capture], measured_results={capture["request_url"]: {"result": "success", "can_call_from_any_ebooking_page": True, "required_page_context": "any_ebooking_page"}})
        self.assertEqual(measured["modules"]["operating_report"]["result"], "success")
        self.assertTrue(measured["modules"]["operating_report"]["can_call_from_any_ebooking_page"])
        self.assertFalse(measured["modules"]["operating_report"]["enabled"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ctrip_api_map.json"
            write_api_map(output, [capture])
            self.assertEqual(json.loads(output.read_text())["modules"]["operating_report"]["result"], "unverified")

    def test_discovered_map_keeps_modules_disabled_and_copies_safe_evidence(self):
        capture = {
            "module": "operating_report",
            "request_url": "https://ebooking.ctrip.com/api/operating-report",
            "method": "POST",
            "payload_schema": {"type": "object"},
            "response_schema": {"type": "object"},
            "required_page_context": "specific_module_page",
        }
        evidence = {
            capture["request_url"]: {
                "result": "discovered",
                "required_page_context": "specific_module_page",
                "field_paths": {"psi_score": "data.serviceScore"},
                "date_parameters": {"startDate": {"type": "string"}},
                "pagination": {"kind": "none_observed"},
                "page_sample_checks": [{"field": "psi_score", "matched": True}],
                "read_only_observation": "query semantics; no mutation observed",
                "write_operation_observed": False,
            }
        }
        result = build_api_map([capture], measured_results=evidence, map_status="discovered")
        module = result["modules"]["operating_report"]
        self.assertEqual(result["map_status"], "discovered")
        self.assertEqual(module["result"], "discovered")
        self.assertFalse(module["enabled"])
        self.assertEqual(module["endpoints"][0]["field_paths"]["psi_score"], "data.serviceScore")

    def test_api_map_filters_context_types_and_resanitizes_schemas_and_notes(self):
        capture = {
            "module": "operating_report",
            "request_url": "https://ebooking.ctrip.com/api/operating-report",
            "method": "POST",
            "payload_schema": {"properties": {"csrfToken": {"type": "string"}, "date": {"type": "string"}}},
            "response_schema": {"note": "token=LEAK"},
            "request_context_types": ["same_origin_session", "LEAK_CONTEXT"],
            "notes": ["authorization=LEAK"],
        }
        endpoint = build_api_map([capture], measured_results={
            capture["request_url"]: {
                "result": "invented-status",
                "required_page_context": "authorization=LEAK_CONTEXT",
                "field_paths": {
                    "safe": "data.date",
                    "unsafe": "data.csrfToken",
                },
                "notes": ["cookie=LEAK_NOTE"],
            }
        })["modules"]["operating_report"]["endpoints"][0]
        serialized = json.dumps(endpoint)
        self.assertNotIn("LEAK", serialized)
        self.assertNotIn("csrfToken", serialized)
        self.assertEqual(endpoint["request_context_types"], ["same_origin_session"])
        self.assertEqual(endpoint["field_paths"], {"safe": "data.date"})
        self.assertEqual(endpoint["result"], "unverified")
        self.assertEqual(endpoint["required_page_context"], "unknown")

    def test_comparator_covers_required_fields_and_category(self):
        old, new = expected_result(), expected_result()
        self.assertTrue(compare_results(old, new).equal)
        self.assertEqual(compute_category(10, 5, 0.2, 0.1), "高曝高转")
        new["operating_report"]["psi_score"] = 79
        self.assertIn("operating_report.psi_score", compare_results(old, new).mismatches)


if __name__ == "__main__":
    unittest.main()
