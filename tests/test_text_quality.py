from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
KNOWN_MOJIBAKE_SAMPLES = (
    "闃舵",
    "鏂囨。",
    "绛栫暐",
    "璇ュ",
    "鐢ㄦ埛",
    "鐩綍",
    "鎴愬姛",
    "澶辫触",
    "瀹℃煡",
)
MOJIBAKE_MARKERS = ("\ufffd", *KNOWN_MOJIBAKE_SAMPLES)


def _published_text_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "SKILL.md"]
    files.extend(sorted((ROOT / "references").glob("*.md")))
    files.extend(sorted((ROOT / "scripts" / "frame_timing_agent").glob("*.py")))
    return files


def _find_mojibake_markers(text: str) -> list[str]:
    return [marker for marker in MOJIBAKE_MARKERS if marker in text]


def test_published_text_is_valid_utf8_without_known_mojibake() -> None:
    for path in _published_text_files():
        text = path.read_text(encoding="utf-8")
        markers = _find_mojibake_markers(text)
        assert not markers, f"{path.relative_to(ROOT)} contains mojibake markers: {markers}"


@pytest.mark.parametrize(
    "mojibake",
    KNOWN_MOJIBAKE_SAMPLES,
)
def test_known_mojibake_samples_are_detected(mojibake: str) -> None:
    assert _find_mojibake_markers(mojibake)


@pytest.mark.parametrize("text", ["Frame timing strategy", "帧处理策略", "用户目录与审查报告"])
def test_normal_english_and_chinese_are_not_flagged(text: str) -> None:
    assert not _find_mojibake_markers(text)


def test_usage_reference_names_real_batch_result_fields() -> None:
    usage = (ROOT / "references" / "usage.md").read_text(encoding="utf-8")

    assert "result.batch_report" not in usage
    assert "result.summary_json_path" in usage
    assert "result.summary_csv_path" in usage
    assert "result.review_dashboard_path" in usage
    assert "result.items" in usage
