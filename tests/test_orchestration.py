"""Fast checks for failure handling in the local orchestration layer."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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

    def test_required_export_matching_ignores_related_library_names(self) -> None:
        self.assertTrue(BUILD.library_matches_stem(Path("libFLAC.so.12"), "libFLAC", "linux"))
        self.assertFalse(BUILD.library_matches_stem(Path("libFLAC++.so.11"), "libFLAC", "linux"))
        self.assertTrue(BUILD.library_matches_stem(Path("libsoxr.so.0"), "libsoxr", "linux"))
        self.assertFalse(BUILD.library_matches_stem(Path("libsoxr-lsr.so.0"), "libsoxr", "linux"))
        self.assertTrue(BUILD.library_matches_stem(Path("avcodec-62.dll"), "libavcodec", "windows"))

    def test_architecture_matching_accepts_file_tool_names(self) -> None:
        self.assertTrue(BUILD.architecture_matches("ELF 64-bit x86-64", "x86_64"))
        self.assertTrue(BUILD.architecture_matches("ELF 64-bit aarch64", "aarch64"))
        self.assertFalse(BUILD.architecture_matches("ELF 64-bit aarch64", "x86_64"))

    def test_windows_configure_scripts_run_through_sh(self) -> None:
        source = Path("source")
        self.assertEqual(
            BUILD.configure_command(source, "x86_64-win32", ["--prefix=out"]),
            ["sh", "source/configure", "--prefix=out"],
        )
        self.assertEqual(
            BUILD.configure_command(source, "x86_64-linux", ["--prefix=out"]),
            ["source/configure", "--prefix=out"],
        )

    def test_download_retries_after_a_temporary_error(self) -> None:
        content = b"source archive"
        record = {
            "name": "example", "version": "1.0", "archive": "https://example.invalid/source.tar.gz",
            "filename": "source.tar.gz", "sha256": hashlib.sha256(content).hexdigest(),
        }
        original = BUILD.WORK
        with tempfile.TemporaryDirectory() as directory:
            BUILD.WORK = Path(directory)
            with mock.patch.object(BUILD.urllib.request, "urlopen", side_effect=[OSError("temporary"), io.BytesIO(content)]) as open_url:
                with mock.patch.object(BUILD.time, "sleep"):
                    BUILD.fetch_record(record)
            self.assertEqual(open_url.call_count, 2)
            self.assertEqual(BUILD.cache_path(record).read_bytes(), content)
        BUILD.WORK = original


if __name__ == "__main__":
    unittest.main()
