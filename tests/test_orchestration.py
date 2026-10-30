"""Fast checks for failure handling in the local orchestration layer."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("audio_build", ROOT / "scripts" / "audio_build.py")
assert SPEC and SPEC.loader
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class OrchestrationTests(unittest.TestCase):
    def test_unknown_target_fails(self) -> None:
        with self.assertRaises(BUILD.BuildError):
            BUILD.target_config("not-a-target")

    def test_cache_checksum_mismatch_fails(self) -> None:
        record = {"filename": "broken.tar.gz", "sha256": "0" * 64}
        original = BUILD.WORK
        with tempfile.TemporaryDirectory() as directory:
            BUILD.WORK = Path(directory)
            path = BUILD.cache_path(record)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"not the expected archive")
            with self.assertRaises(BUILD.BuildError):
                BUILD.verify_archive(record)
        BUILD.WORK = original

    def test_missing_source_fails(self) -> None:
        record = {"filename": "missing.tar.gz", "sha256": "0" * 64}
        original = BUILD.WORK
        with tempfile.TemporaryDirectory() as directory:
            BUILD.WORK = Path(directory)
            with self.assertRaises(BUILD.BuildError):
                BUILD.verify_archive(record)
        BUILD.WORK = original

    def test_malformed_release_version_fails(self) -> None:
        with self.assertRaises(BUILD.BuildError):
            BUILD.validate_release_version("release/latest")

    def test_malformed_archive_path_fails(self) -> None:
        import tarfile

        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "bad.tar"
            with tarfile.open(archive_path, "w") as archive:
                item = tarfile.TarInfo("../escape")
                item.size = 0
                archive.addfile(item)
            with tarfile.open(archive_path) as archive:
                with self.assertRaises(BUILD.BuildError):
                    list(BUILD.safe_members(archive))

    def test_lock_is_valid_json(self) -> None:
        lock = json.loads((ROOT / "sources.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["schema"], 1)
        BUILD.validate_lock()


if __name__ == "__main__":
    unittest.main()
