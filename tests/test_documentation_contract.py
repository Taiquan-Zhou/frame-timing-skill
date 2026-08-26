from __future__ import annotations

import tomllib
from pathlib import Path

import cv2

from frame_timing_agent import __all__ as public_exports
from frame_timing_agent.artifact_layout import (
    ANALYSIS_ARTIFACT,
    EXECUTION_ARTIFACT,
    HEALTH_ARTIFACT,
    HUMAN_REVIEW_ARTIFACT,
    OUTPUT_DIRECTORY,
    REPORT_ARTIFACT,
    STRATEGY_ARTIFACT,
    VALIDATION_ARTIFACT,
)
from frame_timing_agent.contracts import POLICY_REVISION, SCHEMA_VERSION, PolicyName
from frame_timing_agent.tool_cli import _parser

ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = (
    ROOT / "README.md",
    ROOT / "README.zh-CN.md",
    ROOT / "SKILL.md",
    ROOT / "references" / "usage.md",
    ROOT / "references" / "artifact_contract.md",
    ROOT / "references" / "agent-integration.md",
    ROOT / "references" / "migration-v2-to-v3.md",
)
UI_SCREENSHOTS = (
    ROOT / "assets" / "frame-timing-ui.png",
    ROOT / "assets" / "frame-timing-batch-ui.png",
)
WORKFLOW_ANIMATION = ROOT / "assets" / "frame-timing-workflow.gif"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _all_docs() -> str:
    return "\n".join(_read(path) for path in DOC_PATHS)


def _project_scripts() -> dict[str, str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return dict(pyproject["project"]["scripts"])


def test_documentation_files_exist_and_track_agent_safe_contract() -> None:
    for path in DOC_PATHS:
        assert path.is_file(), f"missing documentation file: {path.relative_to(ROOT)}"

    docs = _all_docs()
    for policy in PolicyName:
        assert policy.value in docs
    for artifact_name in (
        ANALYSIS_ARTIFACT,
        STRATEGY_ARTIFACT,
        VALIDATION_ARTIFACT,
        EXECUTION_ARTIFACT,
        HEALTH_ARTIFACT,
        REPORT_ARTIFACT,
        HUMAN_REVIEW_ARTIFACT,
        OUTPUT_DIRECTORY,
    ):
        assert artifact_name in docs
    assert f"schema_version {SCHEMA_VERSION}" in docs or f"schema_version` {SCHEMA_VERSION}" in docs
    assert POLICY_REVISION in docs


def test_documented_commands_match_registered_entrypoints_and_help() -> None:
    docs = _all_docs()
    scripts = _project_scripts()
    help_text = _parser().format_help()

    for command in ("frame-timing", "frame-timing-tool", "frame-timing-benchmark"):
        assert command in scripts
        assert command in docs
    for subcommand in ("capabilities", "analyze", "plan", "validate", "apply", "verify"):
        assert subcommand in help_text
        assert f" {subcommand}" in docs or f"`{subcommand}`" in docs


def test_agent_integration_docs_name_public_api_from_package_root() -> None:
    integration = _read(ROOT / "references" / "agent-integration.md")

    for export in (
        "analyze_frames",
        "plan_strategy",
        "validate_strategy",
        "apply_validated_strategy",
        "verify_output",
        "StrategyRequest",
        "PolicyName",
    ):
        assert export in public_exports
        assert export in integration


def test_migration_docs_keep_v2_and_v3_boundaries_explicit() -> None:
    migration = _read(ROOT / "references" / "migration-v2-to-v3.md")

    assert "legacy v2" in migration
    assert "Agent-safe v3" in migration
    assert "override" in migration
    assert "does not automatically route" in migration
    assert "does not generate duplicate frames" in migration


def test_readme_and_skill_document_recoverable_offline_batch_contract() -> None:
    readme = _read(ROOT / "README.md")
    readme_en = _read(ROOT / "README.en.md")
    skill = _read(ROOT / "SKILL.md")

    for phrase in (
        "CPU-only",
        "单目录工作台",
        "离线批次",
        "frame-timing-ui",
        "frame-timing-tool batch create",
        "frame-timing-tool batch run",
        "frame-timing-tool batch status",
        "frame-timing-tool batch approve",
        "frame-timing-tool batch export",
        "--retry-item",
        "review_required",
        "显式",
    ):
        assert phrase in readme

    for phrase in (
        "CPU-only",
        "frame-timing-ui",
        "frame-timing-tool batch create",
        "frame-timing-tool batch run",
        "frame-timing-tool batch status",
        "frame-timing-tool batch approve",
        "frame-timing-tool batch export",
        "--retry-item",
        "review_required",
        "explicit",
    ):
        assert phrase in readme_en

    for phrase in (
        "CPU-only",
        "frame-timing-tool batch create",
        "frame-timing-tool batch run",
        "frame-timing-tool batch status",
        "frame-timing-tool batch approve",
        "frame-timing-tool batch export",
        "--retry-item",
        "review_required",
        "must not auto-resume",
        "must not auto-approve",
        "must not auto-retry",
        "must not auto-export",
        "frame-timing-batch",
        "compatibility-only",
        "must not be used for the recoverable production workflow",
    ):
        assert phrase in skill


def test_real_ui_screenshots_are_documented() -> None:
    readme = _read(ROOT / "README.md")
    readme_en = _read(ROOT / "README.en.md")

    assert "assets/frame-timing-batch-ui.png" in readme
    assert "assets/frame-timing-batch-ui.png" in readme_en
    for screenshot_path in UI_SCREENSHOTS:
        relative_path = screenshot_path.relative_to(ROOT).as_posix()
        assert f'src="{relative_path}"' in readme
        assert f'src="{relative_path}"' in readme_en
        assert screenshot_path.is_file()
        screenshot = cv2.imread(str(screenshot_path))
        assert screenshot is not None
        height, width = screenshot.shape[:2]
        assert width >= 1200
        assert height >= 800


def test_readme_workflow_animation_is_documented_and_lightweight() -> None:
    readme = _read(ROOT / "README.md")
    readme_en = _read(ROOT / "README.en.md")
    relative_path = WORKFLOW_ANIMATION.relative_to(ROOT).as_posix()

    assert f'src="{relative_path}"' in readme
    assert f'src="{relative_path}"' in readme_en
    data = WORKFLOW_ANIMATION.read_bytes()
    assert data.startswith((b"GIF87a", b"GIF89a"))
    assert data.count(b"\x21\xf9\x04") > 1
    assert WORKFLOW_ANIMATION.stat().st_size < 8 * 1024 * 1024

    width = int.from_bytes(data[6:8], byteorder="little")
    height = int.from_bytes(data[8:10], byteorder="little")
    assert width >= 1000
    assert height <= 420


def test_readme_uses_a_centered_hero_before_the_workflow_animation() -> None:
    readme = _read(ROOT / "README.md")
    readme_en = _read(ROOT / "README.en.md")

    for content in (readme, readme_en):
        hero_start = content.index('<div align="center">')
        animation = content.index('src="assets/frame-timing-workflow.gif"')
        assert hero_start < animation
        assert "# Frame Timing Skill" in content[hero_start:animation]
        assert "Agent-ready video-to-reconstruction pipeline" in content[hero_start:animation]

    readme_hero = readme[
        readme.index('<div align="center">') : readme.index(
            'src="assets/frame-timing-workflow.gif"'
        )
    ]
    assert "让 3D 重建从更好的帧开始" in readme_hero


def test_readme_hero_exposes_product_actions_and_proof_points() -> None:
    readme = _read(ROOT / "README.md")
    readme_en = _read(ROOT / "README.en.md")

    assert "[Windows 下载]" in readme
    assert "[快速开始](#快速开始)" in readme
    assert "[Agent Skill](SKILL.md)" in readme
    assert "## 一套核心，两种工作方式" in readme
    assert "### 选择工作流" not in readme

    assert "[Download for Windows]" in readme_en
    assert "[Quick Start](#quick-start)" in readme_en
    assert "[Agent Skill](SKILL.md)" in readme_en
    assert "## One Core, Two Workspaces" in readme_en
    assert "### Choose a Workflow" not in readme_en

    for content in (readme, readme_en):
        for proof_point in ("Local-first", "CPU-ready", "Recoverable", "Auditable"):
            assert proof_point in content


def test_readme_positions_desktop_as_the_human_workspace_for_one_agent_ready_engine() -> None:
    readme = _read(ROOT / "README.md")
    readme_en = _read(ROOT / "README.en.md")

    assert "Agent 可调用" in readme
    assert "本地人工工作台" in readme
    assert "同一套核心" in readme
    assert "assets/frame-timing-workflow.gif" in readme
    assert "agent-ready" in readme_en
    assert "human-in-the-loop workspace" in readme_en
    assert "same deterministic core" in readme_en
    assert "assets/frame-timing-workflow.gif" in readme_en
    assert "autonomous agent" not in readme.lower()
    assert "autonomous agent" not in readme_en.lower()


def test_release_docs_explain_workflow_choice_batch_lifecycle_and_artifacts() -> None:
    readme = _read(ROOT / "README.md")
    readme_en = _read(ROOT / "README.en.md")
    skill = _read(ROOT / "SKILL.md")

    for content in (readme, readme_en):
        assert "frame-timing-tool batch" in content
        assert "batch_state.json" in content
        assert "output_frames/" in content

    assert "一套核心，两种工作方式" in readme
    assert "assets/frame-timing-ui.png" in readme
    assert "assets/frame-timing-batch-ui.png" in readme
    assert "One Core, Two Workspaces" in readme_en
    assert "assets/frame-timing-ui.png" in readme_en
    assert "assets/frame-timing-batch-ui.png" in readme_en
    assert "Batch Artifact Contract" in skill
    assert "batch_summary.json" in skill
    assert "review_dashboard.md" in skill
    assert "frame-timing-batch" in skill
    assert "compatibility-only" in skill
