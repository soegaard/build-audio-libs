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
SMOKE_SPEC = importlib.util.spec_from_file_location("smoke_runtime", ROOT / "tests" / "smoke_runtime.py")
assert SMOKE_SPEC and SMOKE_SPEC.loader
SMOKE = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(SMOKE)


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
        self.assertTrue(BUILD.library_matches_stem(Path("libsoxr.dll"), "libsoxr", "windows"))
        self.assertFalse(BUILD.library_matches_stem(Path("libsoxr-lsr.dll"), "libsoxr", "windows"))

    def test_architecture_matching_accepts_file_tool_names(self) -> None:
        self.assertTrue(BUILD.architecture_matches("ELF 64-bit x86-64", "x86_64"))
        self.assertTrue(BUILD.architecture_matches("ELF 64-bit aarch64", "aarch64"))
        self.assertFalse(BUILD.architecture_matches("ELF 64-bit aarch64", "x86_64"))

    def test_windows_racket_uses_installer_location_when_not_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            racket = Path(directory) / "Racket" / "racket.exe"
            racket.parent.mkdir()
            racket.touch()
            with mock.patch.object(BUILD.shutil, "which", return_value=None):
                with mock.patch.dict(BUILD.os.environ, {"ProgramFiles": directory}, clear=False):
                    self.assertEqual(BUILD.racket_executable("x86_64-win32"), str(racket))

    def test_windows_racket_addon_uses_isolated_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "isolated-runtime"
            library = stage / "lib"
            library.mkdir(parents=True)
            (library / "libopus.dll").write_bytes(b"native library")
            addon = BUILD.prepare_racket_sound_lib_addon(stage, "x86_64-win32")
            self.assertEqual(addon, stage.parent / "racket-addon")
            assert addon is not None
            install = addon / "racket-sound-lib"
            self.assertEqual((install / "version.txt").read_text(encoding="utf-8"), "(1 1 2)\n")
            self.assertEqual((install / "windows-x86_64" / "libopus.dll").read_bytes(), b"native library")

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
        windows_source = Path("D:/build/source")
        self.assertEqual(
            BUILD.configure_command(windows_source, "x86_64-win32", ["--prefix=out"]),
            ["sh", "/d/build/source/configure", "--prefix=out"],
        )

    def test_windows_aliases_cover_upstream_import_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            library = stage / "lib"
            library.mkdir()
            for name in ("libao-4.dll", "libzlib1.dll", "libogg.dll"):
                (library / name).write_bytes(name.encode())
            BUILD.windows_aliases(stage)
            self.assertEqual((library / "libao-1.2.2.dll").read_bytes(), b"libao-4.dll")
            self.assertEqual((library / "zlib1.dll").read_bytes(), b"libzlib1.dll")
            self.assertEqual((library / "ogg.dll").read_bytes(), b"libogg.dll")

    def test_windows_runtime_bundle_includes_dlls_and_licenses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ucrt64"
            compiler = root / "bin" / "gcc"
            compiler.parent.mkdir(parents=True)
            compiler.touch()
            for name in ("libgcc_s_seh-1.dll", "libstdc++-6.dll", "libwinpthread-1.dll"):
                (root / "bin" / name).write_bytes(name.encode())
            for package in ("gcc-libs", "winpthreads"):
                license_file = root / "share" / "licenses" / package / "COPYING"
                license_file.parent.mkdir(parents=True)
                license_file.write_text(package, encoding="utf-8")
            stage = Path(directory) / "stage"
            (stage / "lib").mkdir(parents=True)
            with mock.patch.object(BUILD.shutil, "which", return_value=str(compiler)):
                BUILD.bundle_windows_toolchain_runtime(stage)
            self.assertTrue((stage / "lib" / "libgcc_s_seh-1.dll").is_file())
            self.assertTrue((stage / "licenses" / "mingw-ucrt64" / "gcc-libs" / "COPYING").is_file())
            self.assertTrue((stage / "licenses" / "mingw-ucrt64" / "winpthreads" / "COPYING").is_file())

    def test_windows_runtime_bundle_accepts_equivalent_license_directory_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ucrt64"
            compiler = root / "bin" / "gcc"
            compiler.parent.mkdir(parents=True)
            compiler.touch()
            for name in ("libgcc_s_seh-1.dll", "libstdc++-6.dll", "libwinpthread-1.dll"):
                (root / "bin" / name).write_bytes(name.encode())
            for package in ("gcc-16", "winpthreads-git"):
                license_file = root / "share" / "licenses" / package / "COPYING"
                license_file.parent.mkdir(parents=True)
                license_file.write_text(package, encoding="utf-8")
            stage = Path(directory) / "stage"
            (stage / "lib").mkdir(parents=True)
            with mock.patch.object(BUILD.shutil, "which", return_value=str(compiler)):
                BUILD.bundle_windows_toolchain_runtime(stage)
            self.assertTrue((stage / "licenses" / "mingw-ucrt64" / "gcc-libs" / "COPYING").is_file())
            self.assertTrue((stage / "licenses" / "mingw-ucrt64" / "winpthreads" / "COPYING").is_file())

    def test_runtime_smoke_chooses_exact_library_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory)
            for name in ("libFLAC++.so", "libFLAC.so", "libsoxr-lsr.so", "libsoxr.so", "libsoxr-lsr.dll", "libsoxr.dll"):
                (library / name).touch()
            self.assertEqual(SMOKE.library_for(library, "FLAC").name, "libFLAC.so")
            self.assertEqual(SMOKE.library_for(library, "soxr").name, "libsoxr.so")
            (library / "libsoxr.so").unlink()
            (library / "libsoxr-lsr.so").unlink()
            self.assertEqual(SMOKE.library_for(library, "soxr").name, "libsoxr.dll")

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
