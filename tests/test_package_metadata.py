import tomllib
import unittest
from pathlib import Path


PRIVATE_REFERENCE_PATTERNS = (
    "D:\\A all code",
    "C:\\Users\\zztq",
    "lingbot-map",
    "video_preprocess",
)


class PackageMetadataTest(unittest.TestCase):
    def test_pyproject_declares_build_backend_and_config_package_data(self):
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(data["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertTrue(any(requirement.startswith("setuptools") for requirement in data["build-system"]["requires"]))
        self.assertEqual(data["project"]["readme"], "README.md")
        self.assertEqual(data["project"]["license"], "MIT")
        self.assertEqual(data["project"]["license-files"], ["LICENSE"])
        package_data = data["tool"]["setuptools"]["package-data"]
        self.assertIn("config/*.json", package_data["frame_timing_agent"])

    def test_repository_declares_public_release_metadata(self):
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(data["project"]["version"], "0.4.0")
        self.assertTrue(Path("LICENSE").is_file())
        self.assertTrue(Path("CHANGELOG.md").is_file())
        self.assertTrue(Path("SECURITY.md").is_file())
        self.assertNotIn("License :: OSI Approved :: MIT License", data["project"]["classifiers"])
        self.assertIn("agent-skill", data["project"]["keywords"])
        self.assertEqual(
            data["project"]["urls"]["Source"],
            "https://github.com/Taiquan-Zhou/frame-timing-skill",
        )

    def test_release_metadata_is_finalized_for_v040(self):
        changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertTrue(data["tool"]["mypy"]["strict"])
        self.assertIn("## 0.3.0 - 2026-07-09", changelog)
        self.assertNotIn("## 0.3.0 - Unreleased", changelog)
        self.assertIn("Agent-safe v3", changelog)
        self.assertIn("frame-timing-tool", changelog)
        self.assertIn("coverage_first", changelog)
        self.assertIn("statistical accuracy claims", changelog)
        self.assertIn("not pixel stabilization", changelog)
        self.assertIn("## 0.4.0 - 2026-07-30", changelog)
        self.assertIn("Windows desktop UI", changelog)

    def test_runtime_dependencies_are_bounded_for_reproducible_installs(self):
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn("numpy>=1.26,<2.0", data["project"]["dependencies"])
        self.assertIn("opencv-python>=4.8,<4.13", data["project"]["dependencies"])

    def test_console_scripts_expose_stable_agent_entrypoints(self):
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(data["project"]["scripts"]["frame-timing"], "frame_timing_agent.simple_cli:main")
        self.assertEqual(data["project"]["scripts"]["frame-timing-batch"], "frame_timing_agent.batch_timing_agent:main")
        self.assertEqual(
            data["project"]["scripts"]["frame-timing-health"], "frame_timing_agent.batch_artifact_health:main"
        )
        self.assertEqual(data["project"]["scripts"]["frame-timing-demo"], "frame_timing_agent.demo_frames:main")
        self.assertEqual(data["project"]["scripts"]["frame-timing-tool"], "frame_timing_agent.tool_cli:main")
        self.assertEqual(data["project"]["scripts"]["frame-timing-benchmark"], "frame_timing_agent.benchmark_cli:main")
        self.assertEqual(data["project"]["scripts"]["frame-timing-ui"], "frame_timing_agent.ui.app:main")
        self.assertIn("PySide6-Essentials>=6.6,<6.9", data["project"]["optional-dependencies"]["ui"])

    def test_agent_safe_batch_commands_reuse_the_existing_tool_entrypoint(self):
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        scripts = data["project"]["scripts"]

        self.assertEqual(
            [name for name, target in scripts.items() if target == "frame_timing_agent.tool_cli:main"],
            ["frame-timing-tool"],
        )
        self.assertNotIn("frame-timing-tool-batch", scripts)

    def test_console_entrypoints_use_python310_runtime_typing(self):
        for path in [
            Path("scripts") / "frame_timing_agent" / "tool_cli.py",
            Path("scripts") / "frame_timing_agent" / "benchmark_cli.py",
        ]:
            with self.subTest(path=str(path)):
                self.assertNotIn("from typing import Never", path.read_text(encoding="utf-8"))

    def test_user_facing_docs_do_not_reference_private_project_paths(self):
        doc_paths = [
            Path("README.md"),
            Path("README.en.md"),
            Path("README.zh-CN.md"),
            Path("SKILL.md"),
            Path("references") / "usage.md",
            Path("references") / "artifact_contract.md",
            Path("references") / "agent-integration.md",
            Path("references") / "migration-v2-to-v3.md",
            Path("agents") / "openai.yaml",
        ]

        for doc_path in doc_paths:
            content = doc_path.read_text(encoding="utf-8")
            for pattern in PRIVATE_REFERENCE_PATTERNS:
                with self.subTest(path=str(doc_path), pattern=pattern):
                    self.assertNotIn(pattern, content)

    def test_docs_explain_skill_install_cli_modes_and_host_project_smoke(self):
        skill = Path("SKILL.md").read_text(encoding="utf-8")
        usage = (Path("references") / "usage.md").read_text(encoding="utf-8")
        artifact_contract = (Path("references") / "artifact_contract.md").read_text(encoding="utf-8")
        readme = Path("README.md").read_text(encoding="utf-8")
        readme_en = Path("README.en.md").read_text(encoding="utf-8")
        readme_zh_compat = Path("README.zh-CN.md").read_text(encoding="utf-8")

        self.assertIn("<path-to-frame-timing-skill>", skill)
        self.assertIn("/path/to/frame-timing-skill", skill)
        self.assertIn("[English](README.en.md)", readme)
        self.assertIn("[中文](README.md)", readme_en)
        self.assertIn("[README.md](README.md)", readme_zh_compat)
        self.assertIn("## 普通用户", readme)
        self.assertIn("## AI Agent 和开发者", readme)
        self.assertIn("## For Users", readme_en)
        self.assertIn("## For Agents And Developers", readme_en)
        self.assertIn("### Agent-safe v3 JSON CLI", readme)
        self.assertIn("### Agent-safe v3 JSON CLI", readme_en)
        self.assertIn("Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill", readme)
        self.assertIn("Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill", readme_en)
        self.assertIn("assets/frame-timing-ui.png", readme)
        self.assertIn("assets/frame-timing-ui.png", readme_en)
        self.assertIn("assets/frame-timing-batch-ui.png", readme)
        self.assertIn("FrameTimingSkill-Windows-x64.zip", readme)
        self.assertIn("FrameTimingSkill-Windows-x64.zip", readme_en)
        self.assertTrue((Path("assets") / "frame-timing-ui.png").is_file())
        self.assertTrue((Path("assets") / "frame-timing-batch-ui.png").is_file())
        for content in [readme, readme_en, skill, usage, artifact_contract]:
            self.assertIn("coverage_first", content)
            self.assertIn("balanced", content)
            self.assertIn("jitter_reduction", content)
            self.assertIn("frame-timing-tool", content)
            self.assertIn("coverage-static-thinning-v1", content)
        for content in [skill, usage, artifact_contract]:
            self.assertIn("reconstruction_balanced", content)
            self.assertIn("select_sources", content)
        self.assertIn("frame-timing path/to/clean_frames", readme)
        self.assertIn("frame-timing path/to/clean_frames", readme_en)
        self.assertIn("output/frame_timing_run", readme)
        self.assertIn("output/frame_timing_run", readme_en)
        self.assertIn("git+https://github.com/Taiquan-Zhou/frame-timing-skill.git", readme)
        self.assertIn("git+https://github.com/Taiquan-Zhou/frame-timing-skill.git", readme_en)
        self.assertIn("https://github.com/Taiquan-Zhou/frame-timing-skill", readme)
        self.assertNotIn("--artifact-root output/benchmark_sample", readme)
        self.assertNotIn("--artifact-root output/benchmark_sample", readme_en)
        self.assertIn("frame-timing-tool analyze", skill)
        self.assertIn("Agent-safe v3 Workflow", skill)
        self.assertIn("legacy v2", usage)
        self.assertNotIn("### Python API", readme)
        self.assertNotIn("### Python API", readme_en)
        self.assertNotIn("reconstruction_balanced", readme)
        self.assertNotIn("reconstruction_balanced", readme_en)
        self.assertNotIn("select_sources", readme)
        self.assertNotIn("select_sources", readme_en)

        self.assertNotIn("<your-agent-skills-dir>/frame-timing-skill", readme)
        self.assertNotIn("repo: Taiquan-Zhou/frame-timing-skill", readme)
        self.assertNotIn("## AI Coding Tool Use", readme)
        self.assertNotIn("Codex", readme)
        self.assertNotIn("for development", readme)
        self.assertNotIn("## Smoke Test", readme)
        self.assertNotIn("From a local checkout", readme)
        self.assertNotIn("For development", readme)
        self.assertNotIn("Release Checklist", readme)
        self.assertNotIn("Release Artifact Scope", readme)
        self.assertNotIn("Repository Status", readme)
        self.assertNotIn("Codex", readme_en)
        self.assertNotIn("## Smoke Test", readme_en)
        self.assertNotIn("From a local checkout", readme_en)
        self.assertNotIn("For development", readme_en)

    def test_ci_workflow_runs_package_and_skill_verification(self):
        workflow = Path(".github") / "workflows" / "ci.yml"
        content = workflow.read_text(encoding="utf-8")

        self.assertIn("ubuntu-latest", content)
        self.assertIn("windows-latest", content)
        self.assertIn('"3.10"', content)
        self.assertIn('"3.12"', content)
        self.assertIn('python -m pip install ".[dev,ui]"', content)
        self.assertIn("QT_QPA_PLATFORM: offscreen", content)
        self.assertIn("python -m ruff check scripts tests", content)
        self.assertIn("python -m ruff format --check scripts tests", content)
        self.assertIn("python -m mypy scripts/frame_timing_agent", content)
        self.assertIn(
            "python -m pytest --cov=frame_timing_agent --cov-report=term-missing --cov-fail-under=90",
            content,
        )
        self.assertIn("python -m compileall -q scripts examples tests", content)
        self.assertIn("python -m build", content)
        self.assertIn("python -m twine check dist/*", content)
        self.assertIn("non-release files", content)
        self.assertIn('"/output/"', content)
        self.assertNotIn(".codex", content)
        self.assertIn("shell: bash", content)
        self.assertIn('python -m pip install "$source"', content)
        self.assertIn("frame-timing output/demo_frames/sample", content)
        self.assertIn("frame-timing-demo", content)
        self.assertIn("frame-timing-health", content)
        self.assertIn("frame-timing-tool capabilities", content)
        self.assertIn("frame-timing-benchmark --help", content)
        self.assertIn("frame-timing-ui --smoke-test", content)

    def test_security_workflow_and_dependabot_cover_release_dependencies(self):
        security = (Path(".github") / "workflows" / "security.yml").read_text(encoding="utf-8")
        dependabot = (Path(".github") / "dependabot.yml").read_text(encoding="utf-8")

        self.assertIn('python -m pip install ".[dev]"', security)
        self.assertIn("python -m pip_audit", security)
        self.assertIn("schedule:", security)
        self.assertIn('package-ecosystem: "pip"', dependabot)
        self.assertIn('package-ecosystem: "github-actions"', dependabot)

    def test_source_distribution_manifest_excludes_development_artifacts(self):
        content = Path("MANIFEST.in").read_text(encoding="utf-8")

        self.assertIn("prune tests", content)
        self.assertIn("prune docs", content)
        self.assertIn("prune .github", content)
        self.assertIn("prune examples", content)
        self.assertIn("prune output", content)
        self.assertIn("prune scripts/*.egg-info", content)
        self.assertIn("include LICENSE", content)
        self.assertIn("include README.en.md", content)
        self.assertIn("include README.zh-CN.md", content)
        self.assertIn("include CHANGELOG.md", content)
        self.assertIn("include SECURITY.md", content)
        self.assertIn("exclude AGENTS.md", content)
        self.assertIn("recursive-include scripts/frame_timing_agent", content)


if __name__ == "__main__":
    unittest.main()
