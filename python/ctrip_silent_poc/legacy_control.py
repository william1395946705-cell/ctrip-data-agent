"""Convert the existing collector's local worker JSON into a safe control file.

The legacy worker result may contain account/profile metadata.  This adapter
uses an explicit allow-list and never copies those fields.  Pyramid provenance
must be supplied from the legacy run's visible behavior; it is never guessed
from a missing or zero value.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import redirect_stdout
from io import StringIO
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

from .comparator import OPERATING_FIELDS, compute_category


LEGACY_OPERATING_FIELDS = {
    "经营提醒": "operating_reminder",
    "昨日离店间夜排名": "room_night_rank",
    "点评分": "review_score",
    "PSI分": "psi_score",
    "本店列表页曝光": "hotel_list_exposure",
    "竞争圈列表页曝光量": "comp_list_exposure",
    "本店曝光转化率": "hotel_exposure_conversion",
    "竞争圈曝光转化率": "comp_exposure_conversion",
    "本店下单转化率": "hotel_order_conversion",
    "竞争圈下单转化率": "comp_order_conversion",
    "分类": "category",
}

PYRAMID_OBSERVATIONS = frozenset({"7d", "30d", "no-investment", "unknown"})


def _legacy_violation(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"有", "有违约", "有违规", "yes", "true"}:
        return "有违约"
    if text in {"无", "无违约", "无违规", "no", "false"}:
        return "无违约"
    return None


def adapt_legacy_batch_result(raw: Mapping[str, Any], *, pyramid_observation: str = "unknown") -> dict[str, Any]:
    """Return an allow-listed normalized baseline for old-vs-silent checks.

    ``pyramid_observation`` is evidence from the legacy run itself:

    - ``7d``: legacy returned before switching to 30 days;
    - ``30d``: legacy visibly switched after a zero 7-day result;
    - ``no-investment``: legacy explicitly reported both windows uninvested;
    - ``unknown``: provenance is unavailable and the baseline is blocked.
    """

    if not isinstance(raw, Mapping):
        raise ValueError("Legacy worker JSON root must be an object.")
    if pyramid_observation not in PYRAMID_OBSERVATIONS:
        raise ValueError("Unsupported Pyramid observation.")
    fields = raw.get("fields")
    if not isinstance(fields, Mapping):
        raise ValueError("Legacy worker JSON is missing the fields object.")

    operating = {field: None for field in OPERATING_FIELDS}
    for legacy_name, normalized_name in LEGACY_OPERATING_FIELDS.items():
        if legacy_name in fields:
            operating[normalized_name] = fields.get(legacy_name)
    derived_category = compute_category(
        operating.get("hotel_list_exposure"),
        operating.get("comp_list_exposure"),
        operating.get("hotel_order_conversion"),
        operating.get("comp_order_conversion"),
    )
    if derived_category is not None:
        operating["category"] = derived_category

    warnings: list[str] = []
    failed_modules: list[str] = []
    missing_operating = [name for name, value in operating.items() if value is None]
    if missing_operating:
        failed_modules.append("operating_report")
        warnings.append("Legacy operating control is missing required fields: " + ", ".join(missing_operating))

    effective_roas = fields.get("金字塔") if "金字塔" in fields else None
    pyramid: dict[str, Any] = {
        "roas_7d": None,
        "roas_30d": None,
        "no_investment": False,
    }
    pyramid_evidence_complete = False
    if pyramid_observation == "7d" and effective_roas is not None:
        pyramid.update({"roas_7d": effective_roas, "roas_30d": None, "no_investment": False})
        pyramid_evidence_complete = True
    elif pyramid_observation == "30d" and effective_roas is not None:
        pyramid.update({"roas_7d": 0, "roas_30d": effective_roas, "no_investment": False})
        pyramid_evidence_complete = True
    elif pyramid_observation == "no-investment" and effective_roas is None:
        pyramid.update({"roas_7d": 0, "roas_30d": 0, "no_investment": True})
        pyramid_evidence_complete = True
    else:
        failed_modules.append("pyramid")
        warnings.append("Legacy Pyramid 7d/30d provenance is incomplete; do not claim field equality.")

    violation = _legacy_violation(fields.get("违约看板"))
    if violation is None:
        failed_modules.append("violation")
        warnings.append("Legacy violation control is missing an explicit 有/无 status.")

    legacy_missing = raw.get("missing_modules")
    if isinstance(legacy_missing, list) and legacy_missing:
        warnings.append("Legacy collector reported missing modules; the control is not complete.")
        for item in legacy_missing:
            text = str(item)
            if "经营" in text:
                failed_modules.append("operating_report")
            elif "金字塔" in text:
                failed_modules.append("pyramid")
            elif "违约" in text or "违规" in text:
                failed_modules.append("violation")

    failed_modules = list(dict.fromkeys(failed_modules))
    status = str(raw.get("status") or "")
    if status not in {"完整成功", "部分成功"}:
        warnings.append("Legacy collector did not report a successful run status.")
    evidence_complete = not failed_modules and pyramid_evidence_complete and status == "完整成功"
    return {
        "platform": "ctrip",
        "hotel": {
            "hotel_id": "",
            "hotel_name": str(raw.get("hotel_name") or "")[:200],
        },
        "collected_at": None,
        "operating_report": operating,
        "pyramid": pyramid,
        "violation": {"status": violation},
        "collector": {
            "mode": "legacy_control",
            "control_evidence_complete": evidence_complete,
            "pyramid_observation": pyramid_observation,
            "failed_modules": failed_modules,
            "warnings": warnings,
        },
    }


def legacy_control_ready(value: Mapping[str, Any]) -> bool:
    """Fail closed when an adapted legacy baseline lacks evidence."""

    collector = value.get("collector")
    if isinstance(collector, Mapping) and collector.get("mode") == "legacy_control":
        return collector.get("control_evidence_complete") is True and not collector.get("failed_modules")
    return True


def run_legacy_control_from_authorized_page(
    *,
    cdp_url: str,
    runtime_dir: str | Path,
    output_path: str | Path,
    speed: str = "stable",
) -> dict[str, Any]:
    """Run the untouched old collector and retain only an allow-listed control.

    The currently authorized Silent-test page supplies the hotel name in
    memory.  The old collector uses its own mapped test Profile and a temporary
    copy of the Excel template.  Its stdout and raw worker JSON may contain
    local account/profile metadata, so both remain process-local or under an
    auto-removed ``/private/tmp`` directory.
    """

    runtime = Path(runtime_dir).resolve()
    output = Path(output_path).resolve()
    if speed not in {"fast", "stable"}:
        raise ValueError("Legacy speed must be fast or stable.")
    if not (runtime / "ctrip_batch_profile_collector.py").is_file() or not (runtime / "config.py").is_file():
        raise ValueError("Legacy Runtime directory is invalid.")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("Playwright is required for the legacy control run.") from error

    hotel_name = ""
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        pages = [
            page
            for context in browser.contexts
            for page in context.pages
            if page.url.startswith("https://ebooking.ctrip.com/")
        ]
        if not pages:
            raise RuntimeError("Authorized eBooking page is unavailable.")
        hotel_name = pages[0].evaluate(
            """() => {
              const node = document.querySelector(
                '#he-micro-html-inline-hotel-name, .he-ctrip-hotel-title-link, .he-ctrip-hotel-title, '
                + '[data-hotel-name], [data-property-name], .hotel-name, .hotelName'
              );
              return node ? String(node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200) : '';
            }"""
        )
    finally:
        playwright.stop()
    if not hotel_name:
        raise RuntimeError("Authorized page hotel identity is unavailable.")

    # Import the existing runtime without copying or modifying its code.
    runtime_text = str(runtime)
    sys.path.insert(0, runtime_text)
    captured_log = StringIO()
    try:
        legacy_config = importlib.import_module("config")
        legacy_worker = importlib.import_module("ctrip_batch_profile_collector")
        source_excel = Path(legacy_config.DEFAULT_EXCEL_TEMPLATE)
        if not source_excel.is_file():
            raise RuntimeError("Legacy Excel template is unavailable.")
        with tempfile.TemporaryDirectory(prefix="ctrip-legacy-control-", dir="/private/tmp") as temp_dir:
            temp_root = Path(temp_dir)
            excel_copy = temp_root / "control.xlsx"
            raw_result_path = temp_root / "legacy-result.json"
            shutil.copy2(source_excel, excel_copy)
            with redirect_stdout(captured_log):
                exit_code = legacy_worker.collect_worker(
                    hotel_name,
                    excel_copy,
                    raw_result_path,
                    speed=speed,
                )
            raw = json.loads(raw_result_path.read_text(encoding="utf-8"))
            log_text = captured_log.getvalue()
            fields = raw.get("fields") if isinstance(raw.get("fields"), Mapping) else {}
            switched_30d = "切换到【过去30天】" in log_text or "切换到过去30天" in log_text
            explicit_no_investment = "近7天和过去30天均未投流" in log_text or "均无投放数据" in log_text
            if explicit_no_investment:
                observation = "no-investment"
            elif switched_30d and fields.get("金字塔") is not None:
                observation = "30d"
            elif not switched_30d and fields.get("金字塔") is not None:
                observation = "7d"
            else:
                observation = "unknown"
            normalized = adapt_legacy_batch_result(raw, pyramid_observation=observation)
            normalized["collector"]["legacy_exit_code"] = int(exit_code)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        # Never propagate an old-runtime message that may include an account or
        # Profile path into the POC terminal/report.
        raise RuntimeError("Legacy control run failed; inspect the old collector's local artifacts.") from None
    finally:
        captured_log.seek(0)
        captured_log.truncate(0)
        if sys.path and sys.path[0] == runtime_text:
            sys.path.pop(0)
    return normalized
