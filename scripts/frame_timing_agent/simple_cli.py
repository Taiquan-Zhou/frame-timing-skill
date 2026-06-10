from __future__ import annotations

from pathlib import Path
from typing import Sequence
import argparse
import sys


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frame_timing_agent.batch_timing_agent import BatchTimingItem, run_batch_timing_agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run frame timing on one already-clean extracted frame directory.")
    parser.add_argument("frames", type=Path, help="Directory containing already-clean extracted image frames.")
    parser.add_argument("--artifact_root", type=Path, default=Path("output") / "frame_timing_run")
    parser.add_argument("--name", default=None, help="Artifact item name. Defaults to the frame directory name.")
    parser.add_argument("--limit_first_n", type=int, default=300)
    parser.add_argument("--mode", default="aggressive_motion")
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--override_config", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    item_name = args.name or args.frames.name or args.frames.resolve().name
    result = run_batch_timing_agent(
        [BatchTimingItem(name=item_name, frames=args.frames)],
        artifact_root=args.artifact_root,
        limit_first_n=args.limit_first_n,
        mode=args.mode,
        write=True,
        fps=args.fps,
        override_config_path=args.override_config,
    )

    item = result.items[0]
    prefix = "OK" if item.status == "ok" else "FAIL"
    print(f"{prefix}: {item.name} analyzed={item.analyzed_count} estimated_output={item.estimated_output_count}")
    if item.output_dir is not None:
        print(f"Output frames: {item.output_dir}")
    print(f"Review dashboard: {result.review_dashboard_path}")
    print(f"Health report: {result.artifact_root / 'analysis' / 'maintenance_report.md'}")
    if item.error:
        print(f"Error: {item.error}")
    return 1 if result.failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
