import tomllib
import unittest
from pathlib import Path


class PackageMetadataTest(unittest.TestCase):
    def test_pyproject_declares_build_backend_and_config_package_data(self):
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(data["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertIn("setuptools", data["build-system"]["requires"])
        package_data = data["tool"]["setuptools"]["package-data"]
        self.assertIn("config/*.json", package_data["frame_timing_agent"])

    def test_runtime_dependencies_are_bounded_for_reproducible_installs(self):
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn("numpy>=1.26,<2.0", data["project"]["dependencies"])
        self.assertIn("opencv-python>=4.8,<4.13", data["project"]["dependencies"])


if __name__ == "__main__":
    unittest.main()
