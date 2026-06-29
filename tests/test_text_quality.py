from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOJIBAKE_MARKERS = ("\ufffd", "锛歚", "闃舵", "绛栫暐", "璇ュ")


def _published_text_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "SKILL.md"]
    files.extend(sorted((ROOT / "references").glob("*.md")))
    files.extend(sorted((ROOT / "scripts" / "frame_timing_agent").glob("*.py")))
    return files


def test_published_text_is_valid_utf8_without_known_mojibake() -> None:
    for path in _published_text_files():
        text = path.read_text(encoding="utf-8")
        markers = [marker for marker in MOJIBAKE_MARKERS if marker in text]
        assert not markers, f"{path.relative_to(ROOT)} contains mojibake markers: {markers}"


def test_usage_reference_names_real_batch_result_fields() -> None:
    usage = (ROOT / "references" / "usage.md").read_text(encoding="utf-8")

    assert "result.batch_report" not in usage
    assert "result.summary_json_path" in usage
    assert "result.summary_csv_path" in usage
    assert "result.review_dashboard_path" in usage
    assert "result.items" in usage
