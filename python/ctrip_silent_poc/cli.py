"""Operator-driven CLI for discovery, Test B-D, and field comparison.

The CLI never navigates, reloads, focuses, clicks, or types in a browser. The
operator changes pages manually while the listener remains attached.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .api_map import write_api_map
from .comparator import compare_results, compute_category
from .inspector import NetworkInspector, extract_hotel_ids, hotel_fingerprint
from .legacy_control import adapt_legacy_batch_result, legacy_control_ready, run_legacy_control_from_authorized_page
from .models import CaptureRecord, CollectorResult, Module
from .redaction import safe_error, sanitize_url
from .replay import (
    InMemoryRequestVault,
    SilentCollector,
    find_first,
    inspect_current_page,
    is_ebooking_url,
    normalize_operating,
    normalize_violation,
)


MODULE_INSTRUCTIONS = (
    ("operating_report", "经营报告"),
    ("pyramid", "金字塔数据报告；若近7天为0/暂无，请按旧流程人工查看过去30天"),
    ("violation", "商家违规/违约看板"),
)

SILENT_INSTRUCTIONS = (
    ("B", "请人工停留在 eBooking 首页，确认当前页面稳定后回到终端。"),
    ("C", "请人工停留在订单、房态、价格或其他普通 eBooking 页面，确认稳定后回到终端。"),
    ("D", "请人工刷新当前普通 eBooking 页面，确认登录态和页面稳定后回到终端。"),
)


def _load_json(path: str | None) -> Mapping[str, Any] | None:
    if not path:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        return float(match.group(0)) if match else None
    return None


def _explicit_empty_roas(value: Any) -> bool:
    number = _number(value)
    if number == 0:
        return True
    return isinstance(value, str) and bool(re.search(r"暂无|无数据|未投流|no\s*data|no\s*investment", value, re.I))


def _build_test_a_from_captures(
    records: list[Any],
    hotel: Mapping[str, Any],
    *,
    batch_id: str | None = None,
) -> CollectorResult:
    """Build Test A only from one identified, same-hotel capture batch.

    Any missing/mismatched provenance is fail-closed.  In particular, a
    capture from another batch or hotel is never silently filtered into a
    seemingly complete result.
    """

    result = CollectorResult()
    result.collector["mode"] = "natural"
    result.hotel.update({key: str(hotel.get(key) or "")[:200] for key in ("hotel_id", "hotel_name")})
    expected_batch = str(batch_id or "").strip()
    expected_fingerprint = hotel_fingerprint(hotel)
    expected_hotel_id = str(hotel.get("hotel_id") or "").strip().lower()
    provenance_errors: list[str] = []
    if not expected_batch:
        provenance_errors.append("Test A batch id is missing.")
    if not expected_fingerprint:
        provenance_errors.append("Test A hotel identity is missing.")
    usable = []
    for record in records:
        if not isinstance(record, CaptureRecord):
            provenance_errors.append("Test A capture record is invalid.")
            continue
        if getattr(record, "test_a_batch_id", None) != expected_batch:
            provenance_errors.append("Test A captures contain another or missing batch provenance.")
            continue
        if getattr(record, "hotel_fingerprint", None) != expected_fingerprint:
            provenance_errors.append("Test A captures contain another or missing hotel provenance.")
            continue
        record_ids: set[str] = set()
        for value in (
            getattr(record, "request_url", None),
            getattr(record, "response_url", None),
            getattr(record, "payload", None),
            getattr(record, "response", None),
        ):
            record_ids.update(extract_hotel_ids(value))
        # If the response/request exposes an explicit id, it must agree with
        # the page identity.  A name-only page cannot safely vouch for it.
        if record_ids and (not expected_hotel_id or record_ids != {expected_hotel_id}):
            provenance_errors.append("Test A request/response hotel identity conflicts with the page.")
            continue
        status = getattr(record, "status", None)
        if status is None or 200 <= status < 300:
            usable.append(record)

    if provenance_errors:
        result.collector["failed_modules"] = [module.value for module in (Module.OPERATING_REPORT, Module.PYRAMID, Module.VIOLATION)]
        result.collector["warnings"] = list(dict.fromkeys(provenance_errors))
        return result

    for record in usable:
        if record.module == Module.OPERATING_REPORT.value:
            for key, value in normalize_operating(record.response).items():
                if value is not None:
                    result.operating_report[key] = value

    result.operating_report["category"] = compute_category(
        result.operating_report.get("hotel_list_exposure"),
        result.operating_report.get("comp_list_exposure"),
        result.operating_report.get("hotel_order_conversion"),
        result.operating_report.get("comp_order_conversion"),
    )
    missing_operating = [
        key for key, value in result.operating_report.items()
        if key != "category" and value is None
    ]
    if missing_operating:
        result.collector["failed_modules"].append(Module.OPERATING_REPORT.value)
        result.collector["warnings"].append("Test A operating fields missing: " + ", ".join(missing_operating))

    pyramid_records = [record for record in usable if record.module == Module.PYRAMID.value]
    raw_7d = next((value for record in pyramid_records if record.variant != "30d" for value in [find_first(record.response, ("roas_7d", "roas7d", "近7天ROAS", "7dRoas", "roas"))] if value is not None), None)
    raw_30d = next((value for record in pyramid_records if record.variant == "30d" for value in [find_first(record.response, ("roas_30d", "roas30d", "近30天ROAS", "30dRoas", "roas"))] if value is not None), None)
    result.pyramid["roas_7d"] = _number(raw_7d)
    if _explicit_empty_roas(raw_7d):
        result.pyramid["roas_30d"] = _number(raw_30d)
        result.pyramid["no_investment"] = _explicit_empty_roas(raw_30d)
    if raw_7d is None or (_explicit_empty_roas(raw_7d) and raw_30d is None):
        result.collector["failed_modules"].append(Module.PYRAMID.value)
        result.collector["warnings"].append("Test A Pyramid period evidence is incomplete.")

    statuses = []
    for record in usable:
        if record.module == Module.VIOLATION.value:
            status = normalize_violation(record.response)
            if status is not None:
                statuses.append(status)
    unique_statuses = list(dict.fromkeys(statuses))
    if len(unique_statuses) == 1:
        result.violation["status"] = unique_statuses[0]
    else:
        result.collector["failed_modules"].append(Module.VIOLATION.value)
        result.collector["warnings"].append("Test A violation evidence is missing or conflicting.")
    result.collected_at = max((record.request_time for record in records if record.request_time), default=None)
    result.collector["failed_modules"] = list(dict.fromkeys(result.collector["failed_modules"]))
    return result


def _hotel_identity_matches(old_result: Mapping[str, Any], page_hotel: Mapping[str, Any]) -> bool:
    old_hotel = old_result.get("hotel") if isinstance(old_result.get("hotel"), Mapping) else {}
    old_id, page_id = str(old_hotel.get("hotel_id") or "").strip(), str(page_hotel.get("hotel_id") or "").strip()
    if old_id and page_id:
        return old_id == page_id
    old_name = re.sub(r"\s+", "", str(old_hotel.get("hotel_name") or "")).lower()
    page_name = re.sub(r"\s+", "", str(page_hotel.get("hotel_name") or "")).lower()
    return bool(old_name and page_name and old_name == page_name)


async def _confirm(prompt: str) -> bool:
    answer = await asyncio.to_thread(input, prompt + " 输入 y 并回车继续，其他输入跳过：")
    return answer.strip().lower() in {"y", "yes", "是", "继续"}


async def _select_page(browser: Any, page_index: int) -> tuple[Any, Any]:
    candidates: list[tuple[Any, Any]] = []
    for context in browser.contexts:
        for page in context.pages:
            url = page.url
            if is_ebooking_url(url):
                candidates.append((context, page))
    if not candidates:
        raise RuntimeError("No existing ebooking.ctrip.com page is attached to this CDP browser.")
    if page_index < 0 or page_index >= len(candidates):
        safe_urls = [sanitize_url(page.url) for _, page in candidates]
        raise RuntimeError(f"page-index {page_index} is out of range; available sanitized pages: {safe_urls}")
    return candidates[page_index]


async def _run_observe(args: argparse.Namespace) -> int:
    """Passively observe one existing eBooking page for real API discovery.

    This command deliberately has no interactive page-control operations.  It
    does not navigate, reload, focus, click or type; an operator may continue
    using or manually refresh the selected page during the capture window.
    """

    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise RuntimeError("Playwright is required for the observe command. Install the optional playwright dependency.") from error

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    inspector: NetworkInspector | None = None
    try:
        browser = await playwright.chromium.connect_over_cdp(args.cdp_url)
        context, page = await _select_page(browser, args.page_index)
        state_before = await inspect_current_page(page)
        page_hotel = {"hotel_id": state_before.hotel_id, "hotel_name": state_before.hotel_name}
        if not state_before.is_ebooking:
            raise RuntimeError("The selected page is not the exact eBooking HTTPS origin.")
        if state_before.is_logged_in is not True:
            raise RuntimeError("The selected eBooking page does not have a confirmed logged-in state.")
        if not state_before.initialized:
            raise RuntimeError("The selected eBooking page has not completed initialization.")
        if not hotel_fingerprint(page_hotel):
            raise RuntimeError("The current hotel identity could not be confirmed from harmless page state.")

        before_url = sanitize_url(page.url)
        inspector = NetworkInspector(
            capture_enabled=True,
            hotel_fingerprint=hotel_fingerprint(page_hotel),
        ).attach(context, target_page=page)
        await page.wait_for_timeout(max(1, args.seconds) * 1000)
        await inspector.drain()

        after_url = sanitize_url(page.url)
        state_after = await inspect_current_page(page)
        after_hotel = {"hotel_id": state_after.hotel_id, "hotel_name": state_after.hotel_name}
        same_hotel = hotel_fingerprint(page_hotel) == hotel_fingerprint(after_hotel)
        observation_stable = bool(
            before_url == after_url
            and state_after.is_logged_in is True
            and state_after.initialized
            and same_hotel
        )
        target_modules = {
            Module.OPERATING_REPORT.value,
            Module.PYRAMID.value,
            Module.VIOLATION.value,
        }
        target_candidates = [record for record in inspector.records if record.module in target_modules]
        module_counts = Counter(record.module for record in inspector.records)

        inspector.write_jsonl(output_dir / "captures.sanitized.jsonl")
        write_api_map(output_dir / "ctrip_api_map.json", inspector.records)
        summary = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "seconds": max(1, args.seconds),
            "page_before": before_url,
            "page_after": after_url,
            "page_unchanged": before_url == after_url,
            "logged_in_before": state_before.is_logged_in,
            "logged_in_after": state_after.is_logged_in,
            "initialized_after": state_after.initialized,
            "hotel_identity_present": True,
            "hotel_identity_unchanged": same_hotel,
            "capture_count": len(inspector.records),
            "module_counts": dict(sorted(module_counts.items())),
            "target_candidate_count": len(target_candidates),
            "target_candidate_modules": sorted({record.module for record in target_candidates}),
            "result": "CANDIDATES_OBSERVED" if target_candidates and observation_stable else "NOT VERIFIED",
            "notes": [
                "Candidate classification is discovery evidence only; it does not prove endpoint semantics or replay safety.",
                "No navigation, reload, focus, click or keyboard operation was performed by this command.",
            ],
        }
        _write_json(output_dir / "passive_observation.json", summary)
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        if inspector is not None:
            inspector.detach()
        # Disconnect only the Playwright driver.  Never close the employee's
        # existing browser or clear its profile/session state.
        await playwright.stop()


async def _run_session(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise RuntimeError("Playwright is required for the session command. Install the optional playwright dependency.") from error

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    old_result = _load_json(args.old_result)
    if not old_result or not legacy_control_ready(old_result):
        raise RuntimeError("The supplied legacy control result lacks complete, explicit evidence.")
    playwright = await async_playwright().start()
    inspector: NetworkInspector | None = None
    try:
        browser = await playwright.chromium.connect_over_cdp(args.cdp_url)
        context, page = await _select_page(browser, args.page_index)
        initial_page_state = await inspect_current_page(page)
        initial_hotel = {"hotel_id": initial_page_state.hotel_id, "hotel_name": initial_page_state.hotel_name}
        if not hotel_fingerprint(initial_hotel):
            raise RuntimeError("Test A cannot start: current hotel identity is unavailable.")
        if not _hotel_identity_matches(old_result or {}, initial_hotel):
            raise RuntimeError("Test A cannot start: current hotel does not match the supplied old result.")
        test_a_batch_id = uuid.uuid4().hex
        vault = InMemoryRequestVault()
        inspector = NetworkInspector(
            request_vault=vault,
            test_a_batch_id=test_a_batch_id,
            hotel_fingerprint=hotel_fingerprint(initial_hotel),
            capture_enabled=False,
        ).attach(context, target_page=page)
        print("已附加被动监听。程序不会切页、刷新、抢焦点或操作鼠标键盘。")

        for module, label in MODULE_INSTRUCTIONS:
            confirmed = await _confirm(f"Test A / {label}：按 y 后请在 {args.capture_seconds} 秒内由人工正常进入/操作目标页面")
            if not confirmed:
                continue
            before_state = await inspect_current_page(page)
            before_hotel = {"hotel_id": before_state.hotel_id, "hotel_name": before_state.hotel_name}
            if hotel_fingerprint(before_hotel) != hotel_fingerprint(initial_hotel) or not _hotel_identity_matches(old_result or {}, before_hotel):
                raise RuntimeError(f"Test A {label} stage rejected: hotel identity changed or is unavailable.")
            before_count = len(inspector.records)
            inspector.set_module_hint(module)
            inspector.set_capture_enabled(True)
            try:
                if args.capture_seconds == 0:
                    await _confirm(
                        f"Test A / {label} 捕获窗口已开启；请人工刷新/操作并等待页面数据稳定，完成后"
                    )
                else:
                    await page.wait_for_timeout(max(1, args.capture_seconds) * 1000)
                await inspector.drain()
            finally:
                inspector.set_capture_enabled(False)
                inspector.set_module_hint(None)
            after_state = await inspect_current_page(page)
            after_hotel = {"hotel_id": after_state.hotel_id, "hotel_name": after_state.hotel_name}
            if hotel_fingerprint(after_hotel) != hotel_fingerprint(initial_hotel) or not _hotel_identity_matches(old_result or {}, after_hotel):
                raise RuntimeError(f"Test A {label} stage rejected: hotel identity changed or is unavailable after capture.")
            print(f"{label} 捕获完成：新增 {len(inspector.records) - before_count} 条脱敏 XHR/fetch 记录。")

        inspector.set_module_hint(None)
        inspector.write_jsonl(output_dir / "captures.sanitized.jsonl")
        api_map_path = output_dir / "ctrip_api_map.json"
        api_map = write_api_map(api_map_path, inspector.records)
        page_state = await inspect_current_page(page)
        page_hotel = {"hotel_id": page_state.hotel_id, "hotel_name": page_state.hotel_name}
        if hotel_fingerprint(page_hotel) != hotel_fingerprint(initial_hotel) or not _hotel_identity_matches(old_result or {}, page_hotel):
            raise RuntimeError("Test A final capture identity does not match the initial same-hotel session.")
        test_a_result = _build_test_a_from_captures(inspector.records, page_hotel, batch_id=test_a_batch_id)
        _write_json(output_dir / "test_a_natural_result.sanitized.json", test_a_result.to_dict())
        a_comparison = compare_results(old_result or {}, test_a_result.to_dict()).to_dict()
        captured_modules = {record.module for record in inspector.records}
        module_coverage = all(module.value in captured_modules for module in (Module.OPERATING_REPORT, Module.PYRAMID, Module.VIOLATION))
        identity_match = _hotel_identity_matches(old_result or {}, page_hotel)
        a_passed = module_coverage and identity_match and not test_a_result.collector["failed_modules"] and a_comparison["equal"]
        tests: dict[str, Any] = {
            "A": {
                "result": "success" if a_passed else "failed",
                "capture_count": len(inspector.records),
                "test_a_batch_id": test_a_batch_id,
                "hotel_fingerprint": hotel_fingerprint(page_hotel),
                "captured_modules": sorted(captured_modules),
                "module_coverage_complete": module_coverage,
                "hotel_identity_matches_old_result": identity_match,
                "capture_window": {
                    "first_request_time": min((record.request_time for record in inspector.records if record.request_time), default=None),
                    "last_request_time": max((record.request_time for record in inspector.records if record.request_time), default=None),
                },
                "collector_result": test_a_result.to_dict(),
                "comparison": a_comparison,
                "notes": ["Test A is built only from this session's natural response captures and must match the same-hotel old result field by field."],
            }
        }

        approved_count = 0
        reviewed = await _confirm(
            "发现地图已写入。请先人工审查每个精确端点；只将确定无副作用的端点设为 read_only:true，"
            "POST 还必须填写 read_only_justification。B-D 是受控试验，未知页面上下文的端点还必须显式设置 controlled_silent_test:true。完成保存后"
        )
        if reviewed:
            reviewed_map = _load_json(str(api_map_path)) or {}
            approved_count = inspector.approve_from_api_map(reviewed_map, controlled_test=True)
        print(f"本进程获准的精确只读请求模板：{approved_count} 个。未批准模板不会执行。")

        collector = SilentCollector(cooldown_seconds=max(0, args.cooldown_minutes) * 60)
        for test_id, instruction in SILENT_INSTRUCTIONS:
            if approved_count == 0:
                tests[test_id] = {"result": "blocked", "notes": ["No exact in-memory request template received read-only approval."]}
                continue
            confirmed = await _confirm(f"Test {test_id}：{instruction}")
            if not confirmed:
                tests[test_id] = {"result": "skipped"}
                continue
            before = sanitize_url(page.url)
            result = await collector.collect(page, vault, force=True)
            after = sanitize_url(page.url)
            safe_result = result.to_dict()
            comparison = compare_results(old_result or {}, safe_result).to_dict()
            page_unchanged = before == after and result.collector["current_page_unchanged"]
            passed = not result.collector["failed_modules"] and page_unchanged and comparison["equal"]
            tests[test_id] = {
                "result": "success" if passed else "failed",
                "current_page_url_before": before,
                "current_page_url_after": after,
                "current_page_unchanged": page_unchanged,
                "collector_result": safe_result,
                "comparison": comparison,
            }

        all_tests_passed = all(tests.get(test_id, {}).get("result") == "success" for test_id in ("A", "B", "C", "D"))
        report = {
            "status": "verified" if all_tests_passed else "unverified",
            "api_map": "ctrip_api_map.json",
            "api_map_kind": api_map.get("map_kind", "discovery"),
            "approved_read_only_templates": approved_count,
            "tests": tests,
            "notes": [
                "The generated file is a discovery map, not an executable extension map.",
                "A-D pass only when every required field equals the old collector and the current URL remains unchanged.",
                "No programmatic navigation, reload, focus, click, or keyboard action is used.",
            ],
        }
        _write_json(output_dir / "silent_test_report.json", report)
        print(f"本机脱敏产物已写入：{output_dir.resolve()}")
        return 0
    finally:
        if inspector is not None:
            inspector.detach()
        # Stop the Playwright driver connection only. Do not call browser.close(),
        # which could close the employee's existing Chrome.
        await playwright.stop()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctrip-silent-poc")
    subparsers = parser.add_subparsers(dest="command", required=True)

    skeleton = subparsers.add_parser("skeleton-map", help="write an empty, unverified API map")
    skeleton.add_argument("--output", default="ctrip_api_map.json")

    compare = subparsers.add_parser("compare", help="compare old and silent normalized JSON")
    compare.add_argument("--old", required=True)
    compare.add_argument("--silent", required=True)
    compare.add_argument("--output")

    adapt_legacy = subparsers.add_parser("adapt-legacy", help="create a credential-free normalized baseline from old worker JSON")
    adapt_legacy.add_argument("--input", required=True, help="local legacy BatchStoreResult JSON")
    adapt_legacy.add_argument("--output", required=True, help="safe normalized control JSON")
    adapt_legacy.add_argument(
        "--pyramid-observation",
        choices=("7d", "30d", "no-investment", "unknown"),
        default="unknown",
        help="what the legacy run visibly proved; unknown blocks Test A-D",
    )

    run_legacy = subparsers.add_parser("run-legacy-control", help="run the untouched old collector and emit only a safe normalized control")
    run_legacy.add_argument("--cdp-url", required=True, help="authorized local Silent-test Chrome CDP endpoint")
    run_legacy.add_argument("--runtime-dir", required=True, help="existing old collector Runtime directory")
    run_legacy.add_argument("--output", required=True, help="safe normalized control JSON")
    run_legacy.add_argument("--speed", choices=("fast", "stable"), default="stable")

    observe = subparsers.add_parser(
        "observe",
        help="passively observe an existing authorized eBooking page without navigation or focus",
    )
    observe.add_argument("--cdp-url", required=True, help="existing authorized local Chrome CDP endpoint")
    observe.add_argument("--page-index", type=int, default=0)
    observe.add_argument("--seconds", type=int, default=30, help="passive capture window in seconds")
    observe.add_argument("--output-dir", default="artifacts/passive-observation")

    session = subparsers.add_parser("session", help="attach to an existing authorized CDP browser without navigation")
    session.add_argument("--cdp-url", required=True, help="existing local CDP endpoint, for example http://127.0.0.1:PORT")
    session.add_argument("--page-index", type=int, default=0)
    session.add_argument("--capture-seconds", type=int, default=20, help="seconds per Test A module; 0 waits for explicit operator completion")
    session.add_argument("--cooldown-minutes", type=int, default=30)
    session.add_argument("--old-result", required=True, help="normalized old collector JSON for field comparison")
    session.add_argument("--output-dir", default="artifacts/local-session")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "skeleton-map":
        write_api_map(args.output, [])
        return 0
    if args.command == "compare":
        old_result = _load_json(args.old)
        silent_result = _load_json(args.silent)
        report = compare_results(old_result or {}, silent_result or {}).to_dict()
        if args.output:
            _write_json(Path(args.output), report)
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["equal"] else 1
    if args.command == "adapt-legacy":
        raw = _load_json(args.input)
        normalized = adapt_legacy_batch_result(raw or {}, pyramid_observation=args.pyramid_observation)
        _write_json(Path(args.output), normalized)
        print(f"脱敏旧采集器对照已写入：{Path(args.output).resolve()}")
        return 0 if legacy_control_ready(normalized) else 1
    if args.command == "run-legacy-control":
        normalized = run_legacy_control_from_authorized_page(
            cdp_url=args.cdp_url,
            runtime_dir=args.runtime_dir,
            output_path=args.output,
            speed=args.speed,
        )
        print(f"旧采集器脱敏对照完成：evidence_complete={str(legacy_control_ready(normalized)).lower()}")
        return 0 if legacy_control_ready(normalized) else 1
    if args.command == "observe":
        return asyncio.run(_run_observe(args))
    if args.command == "session":
        return asyncio.run(_run_session(args))
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        # Do not print exception repr or raw request data.
        print(f"POC stopped: {type(error).__name__}: {safe_error(error)}", file=sys.stderr)
        raise SystemExit(2)
