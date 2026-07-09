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

        self.assertEqual(data["project"]["version"], "0.3.0")
        self.assertTrue(Path("LICENSE").is_file())
        self.assertTrue(Path("CHANGELOG.md").is_file())
        self.assertTrue(Path("SECURITY.md").is_file())
        self.assertNotIn("License :: OSI Approved :: MIT License", data["project"]["classifiers"])
        self.assertIn("agent-skill", data["project"]["keywords"])
        self.assertEqual(
            data["project"]["urls"]["Source"],
            "https://github.com/Taiquan-Zhou/frame-timing-skill",
        )

    def test_release_metadata_is_finalized_for_v030(self):
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

    def test_user_facing_docs_do_not_reference_private_project_paths(self):
        doc_paths = [
            Path("README.md"),
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
        readme_zh = Path("README.zh-CN.md").read_text(encoding="utf-8")

        self.assertIn("<path-to-frame-timing-skill>", skill)
        self.assertIn("/path/to/frame-timing-skill", skill)
        self.assertIn("[中文](README.zh-CN.md)", readme)
        self.assertIn("[English](README.md)", readme_zh)
        self.assertIn("## For Users", readme)
        self.assertIn("## For Agents And Developers", readme)
        self.assertIn("### Agent-safe v3 JSON CLI", readme)
        self.assertIn("### Python API", readme)
        self.assertIn("Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill", readme)
        self.assertIn("## 普通用户", readme_zh)
        self.assertIn("## Agent 和开发者", readme_zh)
        self.assertIn("### Agent-safe v3 JSON CLI", readme_zh)
        for content in [readme, readme_zh, skill, usage, artifact_contract]:
            self.assertIn("coverage_first", content)
            self.assertIn("balanced", content)
            self.assertIn("jitter_reduction", content)
            self.assertIn("frame-timing-tool", content)
            self.assertIn("coverage-static-thinning-v1", content)
            self.assertIn("reconstruction_balanced", content)
            self.assertIn("select_sources", content)
        self.assertIn("frame-timing path/to/clean_frames", readme)
        self.assertIn("frame-timing path/to/clean_frames", readme_zh)
        self.assertIn("output/frame_timing_run", readme)
        self.assertIn("output/frame_timing_run", readme_zh)
        self.assertIn("git+https://github.com/Taiquan-Zhou/frame-timing-skill.git", readme)
        self.assertIn("https://github.com/Taiquan-Zhou/frame-timing-skill", readme)
        self.assertIn("frame-timing-tool analyze", skill)
        self.assertIn("Agent-safe v3 Workflow", skill)
        self.assertIn("legacy v2", usage)

        self.assertNotIn("<your-agent-skills-dir>/frame-timing-skill", readme)
        self.assertNotIn("repo: Taiquan-Zhou/frame-timing-skill", readme)
        self.assertNotIn("## AI Coding Tool Use", readme)
        self.assertNotIn("Codex", readme)
        self.assertNotIn("for development", readme)
        self.assertNotIn("## Smoke Test", readme)
        self.assertNotIn("## 功能测试", readme_zh)
        self.assertNotIn("From a local checkout", readme)
        self.assertNotIn("For development", readme)
        self.assertNotIn("从本地 checkout 安装", readme_zh)
        self.assertNotIn("开发安装", readme_zh)
        self.assertNotIn("Release Checklist", readme)
        self.assertNotIn("Release Artifact Scope", readme)
        self.assertNotIn("Repository Status", readme)

    def test_ci_workflow_runs_package_and_skill_verification(self):
        workflow = Path(".github") / "workflows" / "ci.yml"
        content = workflow.read_text(encoding="utf-8")

        self.assertIn("ubuntu-latest", content)
        self.assertIn("windows-latest", content)
        self.assertIn('"3.10"', content)
        self.assertIn('"3.12"', content)
        self.assertIn('python -m pip install ".[dev]"', content)
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
        self.assertIn("include README.zh-CN.md", content)
        self.assertIn("include CHANGELOG.md", content)
        self.assertIn("include SECURITY.md", content)
        self.assertIn("exclude AGENTS.md", content)
        self.assertIn("recursive-include scripts/frame_timing_agent", content)


if __name__ == "__main__":
    unittest.main()
