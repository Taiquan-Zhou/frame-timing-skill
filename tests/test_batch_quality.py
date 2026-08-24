import csv
import json
from pathlib import Path

import pytest

from frame_timing_agent.batch_quality import BAD_QUALITY_REVIEW_RATIO, evaluate_quality


def write_metrics(tmp_path: Path, *, total: int, bad: int) -> Path:
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    with (analysis_dir / "frame_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source_index",
                "output_index",
                "timestamp_sec",
                "sharpness",
                "brightness",
                "contrast",
                "motion_score",
                "similarity_score",
                "bad_quality_candidate",
            ],
        )
        writer.writeheader()
        for index in range(total):
            writer.writerow(
                {
                    "source_index": index,
                    "output_index": index,
                    "timestamp_sec": index / 30,
                    "sharpness": 100,
                    "brightness": 120,
                    "contrast": 20,
                    "motion_score": 0.01,
                    "similarity_score": 0.99,
                    "bad_quality_candidate": "1" if index < bad else "0",
                }
            )
    (analysis_dir / "segments.json").write_text("[]", encoding="utf-8")
    return analysis_dir


def write_segments(analysis_dir: Path, segments: list[dict]) -> None:
    (analysis_dir / "segments.json").write_text(json.dumps(segments), encoding="utf-8")


def low_motion_segment(start: int, end: int, frame_count: int) -> dict:
    return {
        "segment_type": "low_motion_review",
        "start": start,
        "end": end,
        "frame_count": frame_count,
        "mean_motion": 0.001,
        "reason": "slow camera motion",
    }


def test_bad_quality_ratio_below_threshold_does_not_require_review(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=1000, bad=99)

    warnings = evaluate_quality(analysis_dir)

    assert warnings == ()


def test_bad_quality_ratio_at_threshold_requires_review(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=20, bad=2)

    warnings = evaluate_quality(analysis_dir)

    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.code == "quality.bad_candidate_ratio"
    assert warning.value == 0.10
    assert warning.threshold == BAD_QUALITY_REVIEW_RATIO
    assert warning.affected_count == 2
    assert warning.ranges == ()


@pytest.mark.parametrize("filename", ["frame_metrics.csv", "segments.json"])
def test_missing_quality_artifact_is_a_hard_error(tmp_path, filename):
    analysis_dir = write_metrics(tmp_path, total=10, bad=0)
    (analysis_dir / filename).unlink()

    with pytest.raises(ValueError, match="missing"):
        evaluate_quality(analysis_dir)


def test_empty_metrics_is_a_hard_error(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=0, bad=0)

    with pytest.raises(ValueError, match="no frame rows"):
        evaluate_quality(analysis_dir)


@pytest.mark.parametrize("value", ["", "true", "2", "-1"])
def test_invalid_bad_quality_value_is_a_hard_error(tmp_path, value):
    analysis_dir = write_metrics(tmp_path, total=1, bad=0)
    metrics_path = analysis_dir / "frame_metrics.csv"
    rows = list(csv.reader(metrics_path.read_text(encoding="utf-8").splitlines()))
    rows[1][-1] = value
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)

    with pytest.raises(ValueError, match="bad_quality_candidate"):
        evaluate_quality(analysis_dir)


def test_duplicate_metrics_header_is_a_hard_error(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=1, bad=1)
    metrics_path = analysis_dir / "frame_metrics.csv"
    rows = list(csv.reader(metrics_path.read_text(encoding="utf-8").splitlines()))
    rows[0].append("bad_quality_candidate")
    rows[1].append("0")
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)

    with pytest.raises(ValueError, match="columns"):
        evaluate_quality(analysis_dir)


@pytest.mark.parametrize("source_indices", [[0, -1], [0, "bad"]])
def test_invalid_source_index_is_a_hard_error(tmp_path, source_indices):
    analysis_dir = write_metrics(tmp_path, total=2, bad=0)
    metrics_path = analysis_dir / "frame_metrics.csv"
    rows = list(csv.reader(metrics_path.read_text(encoding="utf-8").splitlines()))
    rows[1][0], rows[2][0] = map(str, source_indices)
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)

    with pytest.raises(ValueError, match="source_index"):
        evaluate_quality(analysis_dir)


def test_duplicate_source_index_instances_are_allowed(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=2, bad=1)
    metrics_path = analysis_dir / "frame_metrics.csv"
    rows = list(csv.reader(metrics_path.read_text(encoding="utf-8").splitlines()))
    rows[1][0] = rows[2][0] = "7"
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)

    warnings = evaluate_quality(analysis_dir)

    assert warnings[0].code == "quality.bad_candidate_ratio"
    assert warnings[0].value == 0.5


@pytest.mark.parametrize("output_indices", [[0, 0], [0, -1], [0, "bad"]])
def test_invalid_or_duplicate_output_index_is_a_hard_error(tmp_path, output_indices):
    analysis_dir = write_metrics(tmp_path, total=2, bad=0)
    metrics_path = analysis_dir / "frame_metrics.csv"
    rows = list(csv.reader(metrics_path.read_text(encoding="utf-8").splitlines()))
    rows[1][1], rows[2][1] = map(str, output_indices)
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)

    with pytest.raises(ValueError, match="output_index"):
        evaluate_quality(analysis_dir)


@pytest.mark.parametrize("width_delta", [-1, 1])
def test_metrics_row_width_must_match_header(tmp_path, width_delta):
    analysis_dir = write_metrics(tmp_path, total=1, bad=0)
    metrics_path = analysis_dir / "frame_metrics.csv"
    rows = list(csv.reader(metrics_path.read_text(encoding="utf-8").splitlines()))
    if width_delta < 0:
        rows[1].pop(3)
    else:
        rows[1].append("EXTRA")
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)

    with pytest.raises(ValueError, match="column count"):
        evaluate_quality(analysis_dir)


def test_no_low_motion_segment_produces_no_segment_warning(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=10, bad=0)
    write_segments(
        analysis_dir,
        [
            {
                "segment_type": "static",
                "start": 0,
                "end": 9,
                "frame_count": 10,
                "mean_motion": 0.0,
                "reason": "static",
            }
        ],
    )

    assert evaluate_quality(analysis_dir) == ()


def test_incomplete_non_review_segment_is_a_hard_error(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=10, bad=0)
    write_segments(analysis_dir, [{"segment_type": "static"}])

    with pytest.raises(ValueError, match="invalid segment"):
        evaluate_quality(analysis_dir)


def test_segment_endpoint_outside_metrics_is_a_hard_error(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=10, bad=0)
    write_segments(analysis_dir, [low_motion_segment(3, 12, 5)])

    with pytest.raises(ValueError, match="outside frame metrics"):
        evaluate_quality(analysis_dir)


def test_segment_frame_count_cannot_exceed_analyzed_frames(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=10, bad=0)
    write_segments(analysis_dir, [low_motion_segment(3, 7, 11)])

    with pytest.raises(ValueError, match="frame_count"):
        evaluate_quality(analysis_dir)


def test_overlapping_low_motion_segments_are_a_hard_error(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=10, bad=0)
    write_segments(
        analysis_dir,
        [
            low_motion_segment(0, 6, 5),
            low_motion_segment(5, 9, 5),
        ],
    )

    with pytest.raises(ValueError, match="overlap"):
        evaluate_quality(analysis_dir)


def test_total_low_motion_affected_count_cannot_exceed_analyzed_frames(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=10, bad=0)
    write_segments(
        analysis_dir,
        [
            low_motion_segment(0, 4, 6),
            low_motion_segment(5, 9, 6),
        ],
    )

    with pytest.raises(ValueError, match="affected count"):
        evaluate_quality(analysis_dir)


def test_one_low_motion_segment_reports_its_source_range(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=20, bad=0)
    write_segments(analysis_dir, [low_motion_segment(3, 7, 5)])

    warnings = evaluate_quality(analysis_dir)

    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.code == "quality.low_motion_review"
    assert warning.value == 1
    assert warning.threshold is None
    assert warning.affected_count == 5
    assert warning.ranges == ((3, 7),)


def test_multiple_low_motion_segments_are_combined_in_one_warning(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=30, bad=0)
    write_segments(
        analysis_dir,
        [
            low_motion_segment(2, 4, 3),
            low_motion_segment(20, 25, 6),
        ],
    )

    warnings = evaluate_quality(analysis_dir)

    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.value == 2
    assert warning.affected_count == 9
    assert warning.ranges == ((2, 4), (20, 25))


def test_both_rules_return_stable_order(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=10, bad=1)
    write_segments(analysis_dir, [low_motion_segment(5, 7, 3)])

    warnings = evaluate_quality(analysis_dir)

    assert [warning.code for warning in warnings] == [
        "quality.bad_candidate_ratio",
        "quality.low_motion_review",
    ]
