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
        self.assertIn("setuptools", data["build-system"]["requires"])
        self.assertEqual(data["project"]["readme"], "README.md")
        package_data = data["tool"]["setuptools"]["package-data"]
        self.assertIn("config/*.json", package_data["frame_timing_agent"])

    def test_runtime_dependencies_are_bounded_for_reproducible_installs(self):
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn("numpy>=1.26,<2.0", data["project"]["dependencies"])
        self.assertIn("opencv-python>=4.8,<4.13", data["project"]["dependencies"])

    def test_console_scripts_expose_stable_agent_entrypoints(self):
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(
            data["project"]["scripts"]["frame-timing-batch"],
            "frame_timing_agent.batch_timing_agent:main",
        )
        self.assertEqual(
            data["project"]["scripts"]["frame-timing-health"],
            "frame_timing_agent.batch_artifact_health:main",
        )
        self.assertEqual(
            data["project"]["scripts"]["frame-timing-demo"],
            "frame_timing_agent.demo_frames:main",
        )

    def test_user_facing_docs_do_not_reference_private_project_paths(self):
        doc_paths = [
            Path("README.md"),
            Path("SKILL.md"),
            Path("references") / "usage.md",
            Path("references") / "artifact_contract.md",
            Path("agents") / "openai.yaml",
        ]

        for doc_path in doc_paths:
            content = doc_path.read_text(encoding="utf-8")
            for pattern in PRIVATE_REFERENCE_PATTERNS:
                with self.subTest(path=str(doc_path), pattern=pattern):
                    self.assertNotIn(pattern, content)


if __name__ == "__main__":
    unittest.main()
