from __future__ import annotations

import tomllib
from pathlib import Path

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
