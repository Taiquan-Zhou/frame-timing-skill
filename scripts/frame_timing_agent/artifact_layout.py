from __future__ import annotations

from pathlib import Path

ANALYSIS_ARTIFACT = "analysis.json"
STRATEGY_ARTIFACT = "strategy.json"
VALIDATION_ARTIFACT = "validation.json"
EXECUTION_ARTIFACT = "execution.json"
HEALTH_ARTIFACT = "health.json"
REPORT_ARTIFACT = "report.md"
HUMAN_REVIEW_ARTIFACT = "human_review.md"
OUTPUT_DIRECTORY = "output_frames"


def validate_artifact_root(
    artifact_root: Path | str,
    frame_dir: Path | str | None = None,
) -> Path:
    artifact_root = Path(artifact_root)
    resolved_root = artifact_root.resolve()
    if "output" not in {part.lower() for part in resolved_root.parts}:
        raise ValueError("artifact root must be inside an output directory")
    if frame_dir is not None:
        resolved_frames = Path(frame_dir).resolve()
        if (
            resolved_root == resolved_frames
            or resolved_root.is_relative_to(resolved_frames)
            or resolved_frames.is_relative_to(resolved_root)
        ):
            raise ValueError("artifact root must not overlap the input frame directory")
    return artifact_root
