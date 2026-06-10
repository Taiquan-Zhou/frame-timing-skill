from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np


DEFAULT_OUTPUT_DIR = Path("output") / "demo_frames" / "sample"


def generate_demo_frames(output_dir: Path | str = DEFAULT_OUTPUT_DIR, count: int = 72) -> list[Path]:
    if count <= 0:
        raise ValueError(f"count must be positive: {count}")

    output_dir = Path(output_dir)
    _validate_output_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_previous_demo_frames(output_dir)

    paths: list[Path] = []
    for index in range(count):
        image = _build_demo_image(index=index, count=count)
        path = output_dir / f"frame_{index:06d}_src_{index:06d}.jpg"
        ok = cv2.imwrite(str(path), image)
        if not ok:
            raise OSError(f"failed to write demo frame: {path}")
        paths.append(path)
    return paths


def _build_demo_image(index: int, count: int) -> np.ndarray:
    height, width = 72, 96
    image = np.full((height, width, 3), 72, dtype=np.uint8)

    first_static_end = max(1, count // 3)
    fast_motion_end = max(first_static_end + 1, (count * 2) // 3)

    if index < first_static_end:
        image[:, :] = (72, 84, 96)
        _draw_reference_grid(image, offset=0)
    elif index < fast_motion_end:
        phase_index = index - first_static_end
        phase_count = max(1, fast_motion_end - first_static_end)
        x = int((width - 18) * phase_index / phase_count)
        y = int((height - 18) * ((phase_index * 3) % phase_count) / phase_count)
        image[:, :] = (42, 52, 62)
        cv2.rectangle(image, (x, y), (x + 18, y + 18), (40, 190, 230), thickness=-1)
        cv2.line(image, (0, height - 1 - y), (width - 1, y), (230, 170, 60), thickness=2)
    else:
        image[:, :] = (88, 76, 64)
        _draw_reference_grid(image, offset=4)
        cv2.circle(image, (width // 2, height // 2), 13, (135, 190, 90), thickness=-1)

    cv2.putText(
        image,
        f"{index:03d}",
        (6, height - 8),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        (240, 240, 240),
        1,
        cv2.LINE_AA,
    )
    return image


def _draw_reference_grid(image: np.ndarray, offset: int) -> None:
    height, width = image.shape[:2]
    for x in range(offset, width, 16):
        cv2.line(image, (x, 0), (x, height - 1), (104, 116, 128), thickness=1)
    for y in range(offset, height, 16):
        cv2.line(image, (0, y), (width - 1, y), (104, 116, 128), thickness=1)


def _validate_output_path(path: Path) -> None:
    if "output" not in {part.lower() for part in path.parts}:
        raise ValueError(f"output_dir must be inside an output directory: {path}")


def _clear_previous_demo_frames(output_dir: Path) -> None:
    for path in output_dir.glob("frame_*_src_*.jpg"):
        if path.is_file():
            path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate deterministic demo frames for frame timing tests.")
    parser.add_argument("--output_dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--count", type=int, default=72)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = generate_demo_frames(output_dir=args.output_dir, count=args.count)
    print(f"Generated {len(paths)} demo frames: {Path(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
