# Frame Timing Skill

[中文](README.md)

Frame Timing Skill prepares already-clean extracted image frames before reconstruction, NeRF, Gaussian Splatting, photogrammetry, or visual review. It analyzes frame motion and quality, plans a safe frame selection strategy, copies selected frames byte-for-byte, and writes local audit artifacts.

It does not extract video, edit pixels, stabilize images, deblur frames, upload data, or run reconstruction. Agent-safe v3 provides coverage protection for frame selection; it is not a 3D geometry, parallax, or camera-baseline coverage optimizer.

<p align="center">
  <img src="assets/frame-timing-workflow.png" alt="Frame Timing workflow: clean_frames -> analyze -> plan -> validate -> apply -> verify -> output_frames" width="100%">
</p>

## For Users

Ask your AI agent or AI coding tool to install this repository as a skill:

```text
Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill
```

Then ask it to process an already-clean frame directory. The recommended Agent workflow uses `frame-timing-tool`:

```text
Use frame-timing-skill on path/to/clean_frames.
Analyze first, compare candidates if needed, validate before apply, and verify before using output_frames downstream.
```

If you only need the compatibility one-command flow, use:

```bash
frame-timing path/to/clean_frames
```

## For Agents And Developers

Install from the repository:

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-timing-skill.git
```

### Agent-safe v3 JSON CLI

Use `frame-timing-tool` when an Agent needs explicit, auditable stages. The lifecycle uses `schema_version 3` and policy revision `coverage-static-thinning-v1`.

```bash
frame-timing-tool capabilities
frame-timing-tool analyze --frames path/to/clean_frames --artifact-root output/frame_timing_run
frame-timing-tool plan --analysis output/frame_timing_run/analysis.json --policy coverage_first
frame-timing-tool validate --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json
frame-timing-tool apply --frames path/to/clean_frames --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json --validation output/frame_timing_run/validation.json --output-dir output/frame_timing_run/output_frames
frame-timing-tool verify --frames path/to/clean_frames --artifact-root output/frame_timing_run
```

Available v3 policies:

- `coverage_first`: default for reconstruction-oriented Agent use; protects non-static coverage and thins only confirmed static ranges conservatively.
- `balanced`: middle-risk comparison candidate.
- `jitter_reduction`: aggressive comparison candidate; useful for visual review, but high-risk for reconstruction coverage.

Medium-risk and high-risk candidates will be shown to the user before applying. Failed validation must not be bypassed by editing JSON; apply revalidates the candidate digest and strategy identity.

## License

MIT. See [LICENSE](LICENSE).
