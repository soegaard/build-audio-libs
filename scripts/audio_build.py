#!/usr/bin/env python3
"""Build, audit, test, and package the native libraries for racket-audio.

This program deliberately has no Python package dependencies. It is the one
entry point used by developers and the GitHub Actions workflows.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import platform as host_platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".work"
DIST = ROOT / "dist"
REPORTS = ROOT / "reports"
LOCK_PATH = ROOT / "sources.lock.json"
PLATFORMS_PATH = ROOT / "platforms.json"
BUILD_ORDER = [
    "zlib", "libogg", "opus", "flac", "mpg123", "soxr", "utfcpp",
    "taglib", "opusfile", "opusenc", "libao", "ffmpeg",
]
REQUIRED_SOURCE_NAMES = set(BUILD_ORDER)
REQUIRED_EXPORTS = {
    "libavutil": "avutil_version", "libavcodec": "avcodec_version",
    "libavformat": "avformat_version", "libswresample": "swresample_version",
    "libFLAC": "FLAC__stream_encoder_new", "libmpg123": "mpg123_init",
    "libsoxr": "soxr_create", "libtag_c": "taglib_file_new",
    "libopus": "opus_get_version_string", "libopusfile": "op_open_file",
    "libopusenc": "ope_encoder_create_file", "libogg": "ogg_sync_init",
    "libao": "ao_initialize",
}


class BuildError(RuntimeError):
    """An actionable build error."""


def fail(message: str) -> None:
    raise BuildError(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path}: {exc}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    printable = " ".join(command)
    print(f"+ {printable}", flush=True)
    try:
        subprocess.run(command, cwd=cwd, env=env, check=True)
    except FileNotFoundError:
        fail(f"required tool is not installed: {command[0]}")
    except subprocess.CalledProcessError as exc:
        fail(f"command failed with exit code {exc.returncode}: {printable}")


def capture(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, env=env, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        return f"unavailable: {exc}"
    return result.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_lock() -> dict[str, Any]:
    lock = read_json(LOCK_PATH)
    if lock.get("schema") != 1 or not isinstance(lock.get("sources"), list):
        fail("sources.lock.json has an unsupported schema")
    return lock


def load_platforms() -> dict[str, Any]:
    platforms = read_json(PLATFORMS_PATH)
    if platforms.get("schema") != 1 or not isinstance(platforms.get("targets"), dict):
        fail("platforms.json has an unsupported schema")
    return platforms


def source_map(lock: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for record in lock["sources"]:
        name = record.get("name")
        if not isinstance(name, str) or name in records:
            fail("every source must have a unique string name")
        records[name] = record
    return records


def validate_lock() -> None:
    lock = load_lock()
    records = source_map(lock)
    missing = REQUIRED_SOURCE_NAMES - set(records)
    extra = set(records) - REQUIRED_SOURCE_NAMES
    if missing or extra:
        fail(f"lock source names differ from build order; missing={sorted(missing)}, extra={sorted(extra)}")
    for name, record in records.items():
        required = ("version", "archive", "filename", "sha256", "license", "dependencies", "recipe", "patches")
        absent = [key for key in required if key not in record]
        if absent:
            fail(f"{name}: missing fields: {', '.join(absent)}")
        if not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
            fail(f"{name}: sha256 must be a lowercase SHA-256 digest")
        if not str(record["archive"]).startswith("https://"):
            fail(f"{name}: archive URL must use HTTPS")
        for dependency in record["dependencies"]:
            if dependency not in records:
                fail(f"{name}: unknown dependency {dependency}")
        for patch_name in record["patches"]:
            patch = ROOT / patch_name
            if not patch.is_file():
                fail(f"{name}: missing patch {patch_name}")
    consumer = lock.get("consumer", {})
    if not re.fullmatch(r"[0-9a-f]{40}", str(consumer.get("commit", ""))):
        fail("consumer.commit must be a full immutable Git SHA")
    if not str(consumer.get("repository", "")).startswith("https://"):
        fail("consumer.repository must use HTTPS")
    for target, config in load_platforms()["targets"].items():
        for key in ("os", "arch", "runner", "build_host", "tested_on", "system_library_allowlist"):
            if key not in config:
                fail(f"{target}: missing platform field {key}")
        if not isinstance(config["system_library_allowlist"], list):
            fail(f"{target}: system_library_allowlist must be a list")


def target_config(target: str) -> dict[str, Any]:
    targets = load_platforms()["targets"]
    if target not in targets:
        fail(f"unknown target {target!r}; choose one of: {', '.join(sorted(targets))}")
    return targets[target]


def cache_path(record: dict[str, Any]) -> Path:
    return WORK / "downloads" / record["filename"]


def verify_archive(record: dict[str, Any]) -> Path:
    archive = cache_path(record)
    if not archive.is_file():
        fail(f"missing verified source {archive.name}; run: python3 scripts/audio_build.py fetch")
    actual = sha256(archive)
    if actual != record["sha256"]:
        fail(f"checksum mismatch for cached {archive.name}: expected {record['sha256']}, got {actual}")
    return archive


def fetch_record(record: dict[str, Any]) -> None:
    destination = cache_path(record)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        verify_archive(record)
        print(f"verified cached {destination.name}")
        return
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with contextlib.suppress(FileNotFoundError):
        temporary.unlink()
    print(f"fetching {record['name']} {record['version']}")
    request = urllib.request.Request(record["archive"], headers={"User-Agent": "build-audio-libs/1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
    except OSError as exc:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        fail(f"download failed for {record['name']}: {exc}")
    temporary.replace(destination)
    verify_archive(record)
    print(f"verified {destination.name}")


def safe_members(archive: tarfile.TarFile) -> Iterable[tarfile.TarInfo]:
    for member in archive.getmembers():
        name = PurePosixPath(member.name)
        if name.is_absolute() or ".." in name.parts:
            fail(f"unsafe archive member: {member.name}")
        if member.issym() or member.islnk():
            link = PurePosixPath(member.linkname)
            if link.is_absolute() or ".." in link.parts:
                fail(f"unsafe archive link: {member.name} -> {member.linkname}")
        yield member


def source_directory(record: dict[str, Any]) -> Path:
    archive = verify_archive(record)  # Hash-check cache hits before every extraction.
    destination = WORK / "sources" / record["name"]
    marker = destination / ".source-sha256"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == record["sha256"]:
        return destination
    if destination.exists():
        shutil.rmtree(destination)
    temporary = destination.parent / f".{record['name']}.extracting"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:*") as bundle:
            # Members were validated above. Do not use tarfile's newer filter
            # argument so Python 3.9+ remains a supported bootstrap runtime.
            bundle.extractall(temporary, members=safe_members(bundle))
    except (tarfile.TarError, OSError) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        fail(f"cannot extract {archive.name}: {exc}")
    roots = [path for path in temporary.iterdir() if path.is_dir()]
    files = [path for path in temporary.iterdir() if path.is_file()]
    if len(roots) != 1 or files:
        shutil.rmtree(temporary, ignore_errors=True)
        fail(f"unexpected archive layout in {archive.name}")
    roots[0].replace(destination)
    shutil.rmtree(temporary, ignore_errors=True)
    marker.write_text(record["sha256"] + "\n", encoding="utf-8")
    return destination


def fetch_consumer(lock: dict[str, Any]) -> Path:
    consumer = lock["consumer"]
    destination = WORK / "consumer" / "racket-audio"
    marker = destination / ".pinned-commit"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == consumer["commit"]:
        return destination
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "--quiet", str(destination)])
    run(["git", "-C", str(destination), "remote", "add", "origin", consumer["repository"]])
    # Fetching the object by full SHA prevents a branch or tag from moving underneath us.
    run(["git", "-C", str(destination), "fetch", "--depth=1", "origin", consumer["commit"]])
    run(["git", "-C", str(destination), "checkout", "--detach", "--quiet", "FETCH_HEAD"])
    actual = capture(["git", "-C", str(destination), "rev-parse", "HEAD"])
    if actual != consumer["commit"]:
        fail(f"consumer commit mismatch: expected {consumer['commit']}, got {actual}")
    marker.write_text(actual + "\n", encoding="utf-8")
    return destination


def clean_environment(target: str, prefix: Path) -> dict[str, str]:
    config = target_config(target)
    env = os.environ.copy()
    # Do not let pkg-config or CMake discover Homebrew audio libraries.
    for key in ("CPATH", "LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "PKG_CONFIG_PATH"):
        env.pop(key, None)
    env["PKG_CONFIG_LIBDIR"] = str(prefix / "lib" / "pkgconfig")
    env["PKG_CONFIG_PATH"] = str(prefix / "lib" / "pkgconfig")
    include = prefix / "include"
    library = prefix / ("bin" if config["os"] == "windows" else "lib")
    env["CPPFLAGS"] = f"-I{include}"
    env["LDFLAGS"] = f"-L{library}"
    env["CMAKE_IGNORE_PREFIX_PATH"] = "/opt/homebrew;/usr/local/homebrew"
    env["CMAKE_FIND_USE_PACKAGE_ROOT_PATH"] = "FALSE"
    if config["os"] == "macosx":
        deployment = config["deployment_target"]
        env["MACOSX_DEPLOYMENT_TARGET"] = deployment
        env["CFLAGS"] = f"-mmacosx-version-min={deployment}"
        env["CXXFLAGS"] = f"-mmacosx-version-min={deployment}"
    return env


def cmake_options(target: str, prefix: Path) -> list[str]:
    config = target_config(target)
    generator = "Ninja"
    try:
        ninja_ok = subprocess.run(["ninja", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except FileNotFoundError:
        ninja_ok = False
    # Local macOS installations sometimes expose a stale Python wrapper named
    # ninja. CMake's Unix Makefiles generator is a safe toolchain fallback.
    if not ninja_ok:
        generator = "Unix Makefiles"
    options = [
        "-G", generator,
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=ON",
        "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
        "-DCMAKE_FIND_USE_PACKAGE_ROOT_PATH=FALSE",
        "-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local/homebrew",
    ]
    if config["os"] == "macosx":
        options += [
            "-DCMAKE_OSX_ARCHITECTURES=arm64",
            f"-DCMAKE_OSX_DEPLOYMENT_TARGET={config['deployment_target']}",
        ]
    return options


def jobs() -> str:
    return str(max(1, os.cpu_count() or 1))


def apply_patches(record: dict[str, Any], source: Path) -> None:
    for patch_name in record["patches"]:
        run(["patch", "-p1", "--forward", "--input", str(ROOT / patch_name)], cwd=source)


def copy_source_for_build(record: dict[str, Any], target: str) -> Path:
    source = source_directory(record)
    destination = WORK / "build" / target / "sources" / record["name"]
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True)
    apply_patches(record, destination)
    return destination


def build_cmake(name: str, source: Path, target: str, prefix: Path, env: dict[str, str]) -> None:
    build = WORK / "build" / target / name
    if build.exists():
        shutil.rmtree(build)
    extra: list[str] = []
    if name == "zlib":
        extra = ["-DZLIB_BUILD_EXAMPLES=OFF"]
    elif name == "libogg":
        # libogg 1.3.5 declares CMake 2.x compatibility. CMake 4 requires
        # this explicit lower policy bound for that supported old release.
        extra = ["-DCMAKE_POLICY_VERSION_MINIMUM=3.5", "-DINSTALL_DOCS=OFF"]
    elif name == "opus":
        extra = ["-DOPUS_BUILD_PROGRAMS=OFF", "-DOPUS_BUILD_TESTING=OFF"]
    elif name == "flac":
        extra = ["-DBUILD_PROGRAMS=OFF", "-DBUILD_EXAMPLES=OFF", "-DBUILD_TESTING=OFF", "-DBUILD_DOCS=OFF", "-DWITH_OGG=OFF"]
    elif name == "soxr":
        extra = ["-DBUILD_TESTS=OFF", "-DBUILD_EXAMPLES=OFF", "-DBUILD_LSR_TESTS=OFF", "-DWITH_OPENMP=OFF"]
    elif name == "taglib":
        extra = [
            "-DBUILD_TESTING=OFF", "-DBUILD_EXAMPLES=OFF", "-DBUILD_BINDINGS=ON",
            "-DWITH_ZLIB=ON", f"-Dutf8cpp_INCLUDE_DIR={prefix / 'include' / 'utf8cpp'}",
        ]
    else:
        fail(f"no CMake recipe for {name}")
    run(["cmake", "-S", str(source), "-B", str(build), *cmake_options(target, prefix), *extra], env=env)
    run(["cmake", "--build", str(build), "--parallel", jobs()], env=env)
    run(["cmake", "--install", str(build)], env=env)


def build_autotools(name: str, source: Path, target: str, prefix: Path, env: dict[str, str]) -> None:
    build = WORK / "build" / target / name
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    if name == "libao":
        # The pinned upstream tag is a source checkout without generated
        # configure files. Bootstrap only from its checked-in Autotools input.
        run(["autoreconf", "--install", "--force"], cwd=source, env=env)
    options = [f"--prefix={prefix}", "--enable-shared", "--disable-static"]
    if name == "mpg123":
        options += ["--with-default-audio=dummy", "--disable-components", "--enable-libmpg123"]
    elif name == "opusfile":
        options += ["--disable-http", "--disable-doc"]
    elif name == "opusenc":
        options += ["--disable-doc"]
    elif name == "libao":
        options += ["--enable-plugins", "--disable-esd", "--disable-pulse", "--disable-arts", "--disable-nas", "--disable-roar", "--disable-oss"]
        system = target_config(target)["os"]
        if system == "macosx":
            options += ["--enable-macosx"]
        elif system == "linux":
            options += ["--enable-alsa"]
    else:
        fail(f"no Autotools recipe for {name}")
    run([str(source / "configure"), *options], cwd=build, env=env)
    run(["make", f"-j{jobs()}"], cwd=build, env=env)
    run(["make", "install"], cwd=build, env=env)


def build_headers(source: Path, prefix: Path) -> None:
    headers = source / "source"
    if not (headers / "utf8.h").is_file():
        fail("utfcpp source archive does not contain source/utf8.h")
    destination = prefix / "include" / "utf8cpp"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(headers, destination)


def build_ffmpeg(source: Path, target: str, prefix: Path, env: dict[str, str]) -> None:
    build = WORK / "build" / target / "ffmpeg"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    configuration = [
        str(source / "configure"), f"--prefix={prefix}",
        "--enable-shared", "--disable-static", "--disable-programs", "--disable-doc",
        "--disable-debug", "--disable-autodetect", "--disable-network", "--disable-avdevice",
        "--disable-avfilter", "--enable-small", "--enable-zlib",
        "--enable-protocol=file,pipe,data",
        "--enable-demuxer=aac,ac3,asf,flac,matroska,mov,mp3,ogg,wav",
        "--enable-decoder=aac,ac3,alac,flac,mp3*,opus,pcm_*,vorbis,wma*",
        f"--extra-cflags=-I{prefix / 'include'}",
        f"--extra-ldflags=-L{prefix / 'lib'}",
    ]
    config = target_config(target)
    if config["os"] == "macosx":
        configuration += ["--arch=arm64", "--cc=clang", "--install-name-dir=@loader_path"]
    elif config["os"] == "windows":
        configuration += ["--target-os=mingw32", "--arch=x86_64"]
    run(configuration, cwd=build, env=env)
    run(["make", f"-j{jobs()}"], cwd=build, env=env)
    run(["make", "install"], cwd=build, env=env)
    config_mak = build / "ffbuild" / "config.mak"
    if config_mak.is_file():
        report = REPORTS / target / "ffmpeg-config.mak"
        report.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_mak, report)


def build_target(target: str, *, clean: bool = False) -> None:
    validate_lock()
    target_config(target)
    lock = load_lock()
    records = source_map(lock)
    prefix = WORK / "prefix" / target
    if clean and prefix.exists():
        shutil.rmtree(prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    env = clean_environment(target, prefix)
    for name in BUILD_ORDER:
        record = records[name]
        source = copy_source_for_build(record, target)
        print(f"building {name} for {target}")
        if name == "utfcpp":
            build_headers(source, prefix)
        elif name == "ffmpeg":
            build_ffmpeg(source, target, prefix, env)
        elif record["recipe"].startswith("cmake:"):
            build_cmake(name, source, target, prefix, env)
        elif record["recipe"].startswith("autotools:"):
            build_autotools(name, source, target, prefix, env)
        else:
            fail(f"unknown recipe {record['recipe']}")
    write_provenance(target, prefix)


def shared_files(path: Path, system: str) -> list[Path]:
    if not path.is_dir():
        return []
    if system == "macosx":
        return [item for item in path.rglob("*.dylib") if item.is_file() or item.is_symlink()]
    if system == "windows":
        return [item for item in path.rglob("*.dll") if item.is_file()]
    return [item for item in path.rglob("*.so*") if item.is_file() or item.is_symlink()]


def copy_file_or_link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    else:
        shutil.copy2(source, destination)


def copy_licenses(records: dict[str, dict[str, Any]], stage: Path) -> None:
    licenses = stage / "licenses"
    for name, record in records.items():
        source = source_directory(record)
        candidates = []
        for pattern in ("COPYING*", "LICENSE*", "COPYRIGHT*"):
            candidates.extend(candidate for candidate in source.glob(pattern) if candidate.is_file())
        if not candidates:
            fail(f"{name}: cannot find a license text in its source archive")
        for actual in sorted(set(candidates)):
            destination = licenses / f"{name}-{actual.name}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(actual, destination)


def macos_relocate(stage: Path) -> None:
    library = stage / "lib"
    dylibs = shared_files(library, "macosx")
    names = {item.name for item in dylibs}
    for dylib in dylibs:
        relative_parent = dylib.parent.relative_to(library)
        run(["install_name_tool", "-id", f"@loader_path/{dylib.name}", str(dylib)])
        dependencies = capture(["otool", "-L", str(dylib)]).splitlines()[1:]
        for line in dependencies:
            old = line.strip().split(" ", 1)[0]
            base = Path(old).name
            if base not in names or old.startswith("/usr/lib/") or old.startswith("/System/Library/"):
                continue
            if relative_parent == Path("."):
                new = f"@loader_path/{base}"
            else:
                up = "/".join(".." for _ in relative_parent.parts)
                new = f"@loader_path/{up}/{base}"
            if old != new:
                run(["install_name_tool", "-change", old, new, str(dylib)])
        run(["codesign", "--force", "--sign", "-", str(dylib)])


def linux_relocate(stage: Path) -> None:
    library = stage / "lib"
    for shared in shared_files(library, "linux"):
        parent = shared.parent.relative_to(library)
        rpath = "$ORIGIN" if parent == Path(".") else "$ORIGIN/" + "/".join(".." for _ in parent.parts)
        run(["patchelf", "--set-rpath", rpath, str(shared)])


def windows_aliases(stage: Path) -> None:
    library = stage / "lib"
    candidates = sorted(library.glob("libao*.dll"))
    if not candidates:
        fail("libao did not install a Windows DLL")
    # racket-audio pins this literal historical name. This is an alias to the
    # same DLL, not a substitute for the ABI-sensitive FFmpeg names.
    shutil.copy2(candidates[0], library / "libao-1.2.2.dll")


def stage_target(target: str) -> Path:
    config = target_config(target)
    prefix = WORK / "prefix" / target
    if not prefix.is_dir():
        fail(f"missing build prefix for {target}; run build first")
    stage = WORK / "stage" / target
    if stage.exists():
        shutil.rmtree(stage)
    library = stage / "lib"
    system = config["os"]
    source_dirs = [prefix / "lib"]
    if system == "windows":
        source_dirs.insert(0, prefix / "bin")
    copied: set[str] = set()
    for source_dir in source_dirs:
        for shared in shared_files(source_dir, system):
            if "ao/plugins" in shared.as_posix():
                continue
            if system != "windows" and not any(shared.name.startswith(stem) for stem in runtime_stems()):
                continue
            if shared.name in copied:
                continue
            copy_file_or_link(shared, library / shared.name)
            copied.add(shared.name)
    plugin_root = prefix / "lib" / "ao"
    if plugin_root.is_dir():
        shutil.copytree(plugin_root, library / "ao", symlinks=True)
    if system == "macosx":
        macos_relocate(stage)
    elif system == "linux":
        linux_relocate(stage)
    else:
        windows_aliases(stage)
    records = source_map(load_lock())
    copy_licenses(records, stage)
    provenance = read_json(WORK / "provenance" / target / "build.json")
    write_json(stage / "provenance.json", provenance)
    write_manifest(target, stage)
    return stage


def git_commit() -> str:
    return capture(["git", "rev-parse", "HEAD"], cwd=ROOT)


def write_provenance(target: str, prefix: Path) -> None:
    lock = load_lock()
    records = source_map(lock)
    compiler = capture([os.environ.get("CC", "cc"), "--version"]).splitlines()
    tools = {
        "python": sys.version,
        "compiler": compiler[0] if compiler else "unavailable",
        "cmake": capture(["cmake", "--version"]).splitlines()[0],
        "make": capture(["make", "--version"]).splitlines()[0],
        "host": host_platform.platform(),
        "github_runner_image": os.environ.get("ImageOS", "local"),
        "github_runner_version": os.environ.get("ImageVersion", "local"),
    }
    provenance = {
        "target": target,
        "build_repository_commit": git_commit(),
        "consumer": lock["consumer"],
        "platform": target_config(target),
        "prefix": str(prefix),
        "tools": tools,
        "sources": [
            {
                "name": record["name"], "version": record["version"],
                "archive": record["archive"], "sha256": record["sha256"],
                "recipe": record["recipe"],
                "patches": [
                    {"path": patch, "sha256": sha256(ROOT / patch)} for patch in record["patches"]
                ],
            }
            for record in records.values()
        ],
        "ffmpeg_configuration_report": str(REPORTS / target / "ffmpeg-config.mak"),
        "reproducibility": "Pinned inputs and recorded toolchain settings provide repeatable source selection. They do not claim bit-for-bit identical output across compiler or runner-image changes.",
    }
    write_json(WORK / "provenance" / target / "build.json", provenance)


def expected_names(target: str) -> list[str]:
    system = target_config(target)["os"]
    if system == "windows":
        return ["avutil-60.dll", "avcodec-62.dll", "avformat-62.dll", "swresample-6.dll", "libao-1.2.2.dll"]
    return runtime_stems()


def runtime_stems() -> list[str]:
    return [
        "libavutil", "libavcodec", "libavformat", "libswresample", "libFLAC",
        "libmpg123", "libsoxr", "libtag", "libtag_c", "libopus", "libopusfile",
        "libopusenc", "libogg", "libao", "libz",
    ]


def find_library(library: Path, expected: str) -> Path | None:
    base = expected[:-4] if expected.endswith(".dll") else expected
    for path in library.iterdir():
        if path.name == expected or path.name.startswith(base + ".") or path.name.startswith(base + "-"):
            return path
    return None


def write_manifest(target: str, stage: Path) -> None:
    files = []
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name != "runtime-manifest.json":
            files.append({"path": path.relative_to(stage).as_posix(), "sha256": sha256(path), "size": path.stat().st_size})
    write_json(stage / "runtime-manifest.json", {"target": target, "files": files, "expected_libraries": expected_names(target)})


def allowed_dependency(name: str, allowlist: Iterable[str]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, Path(value).name.lower()) for value in allowlist)


def library_matches_stem(path: Path, stem: str, system: str) -> bool:
    """Return whether a library file is the runtime library for ``stem``."""
    name = path.name.lower()
    normalized_stem = stem.lower()
    if system == "windows":
        stems = [normalized_stem]
        if normalized_stem.startswith("libav"):
            stems.append(normalized_stem[3:])
        return any(name == f"{value}.dll" or name.startswith(f"{value}-") for value in stems)
    return name.startswith(f"{normalized_stem}.")


def audit_target(target: str, stage: Path | None = None) -> Path:
    config = target_config(target)
    stage = stage or (WORK / "stage" / target)
    library = stage / "lib"
    if not library.is_dir():
        fail(f"missing staged runtime for {target}; run stage first")
    report: dict[str, Any] = {"target": target, "files": {}, "errors": []}
    for expected in expected_names(target):
        if find_library(library, expected) is None:
            report["errors"].append(f"missing expected library {expected}")
    system = config["os"]
    staged_names = {path.name.lower() for path in shared_files(library, system)}
    allowed_names = config["system_library_allowlist"]
    for shared in shared_files(library, system):
        # Link names are needed at runtime, but auditing them repeats the
        # binary check with `file` reporting only the link destination.
        if shared.is_symlink():
            continue
        key = shared.relative_to(stage).as_posix()
        if system == "macosx":
            description = capture(["file", "-b", str(shared)])
            details = capture(["otool", "-L", str(shared)])
            load_commands = capture(["otool", "-l", str(shared)])
            if "arm64" not in description:
                report["errors"].append(f"{key}: not arm64 ({description})")
            if "minos 11.0" not in load_commands:
                report["errors"].append(f"{key}: does not declare macOS 11.0 minimum")
            dependencies = [line.strip().split(" ", 1)[0] for line in details.splitlines()[1:]]
            for dependency in dependencies:
                base = Path(dependency).name.lower()
                if dependency.startswith("/usr/lib/") or dependency.startswith("/System/Library/"):
                    continue
                if base not in staged_names:
                    report["errors"].append(f"{key}: unbundled runtime dependency {dependency}")
            symbols = capture(["nm", "-gU", str(shared)])
        elif system == "linux":
            description = capture(["file", "-b", str(shared)])
            details = capture(["readelf", "-d", str(shared)])
            versions = capture(["readelf", "--version-info", str(shared)])
            if config["arch"] not in description:
                report["errors"].append(f"{key}: wrong architecture ({description})")
            if "$ORIGIN" not in details:
                report["errors"].append(f"{key}: missing $ORIGIN RPATH")
            needed = re.findall(r"Shared library: \[(.+?)\]", details)
            for dependency in needed:
                if dependency.lower() not in staged_names and not allowed_dependency(dependency, allowed_names):
                    report["errors"].append(f"{key}: unbundled runtime dependency {dependency}")
            for kind, baseline_name in (("GLIBC", "glibc_baseline"), ("GLIBCXX", "glibcxx_baseline")):
                baseline = tuple(int(part) for part in config[baseline_name].split("."))
                for value in re.findall(rf"{kind}_(\d+(?:\.\d+)*)", versions):
                    found = tuple(int(part) for part in value.split("."))
                    if found > baseline:
                        report["errors"].append(f"{key}: requires {kind}_{value}, above baseline {kind}_{config[baseline_name]}")
            symbols = capture(["nm", "-D", "--defined-only", str(shared)])
        else:
            description = capture(["file", "-b", str(shared)])
            details = capture(["objdump", "-p", str(shared)])
            if "x86-64" not in description and "x86_64" not in description:
                report["errors"].append(f"{key}: wrong architecture ({description})")
            imports = re.findall(r"DLL Name: (.+)", details)
            for dependency in imports:
                if dependency.lower() not in staged_names and not allowed_dependency(dependency, allowed_names):
                    report["errors"].append(f"{key}: unbundled runtime dependency {dependency}")
            symbols = capture(["nm", "-g", str(shared)])
        forbidden = [str(WORK), "/opt/homebrew", "/usr/local/homebrew"]
        if any(value in details for value in forbidden):
            report["errors"].append(f"{key}: contains a build or Homebrew path")
        for stem, symbol in REQUIRED_EXPORTS.items():
            if library_matches_stem(shared, stem, system) and symbol not in symbols:
                report["errors"].append(f"{key}: missing required export {symbol}")
        report["files"][key] = {"file": description, "dependencies": details, "exports": symbols}
    report_path = REPORTS / target / "audit.json"
    write_json(report_path, report)
    if report["errors"]:
        fail("runtime audit failed:\n" + "\n".join(report["errors"]))
    return report_path


def isolated_stage(target: str) -> Path:
    source = WORK / "stage" / target
    if not source.is_dir():
        fail(f"missing staged runtime for {target}; run stage first")
    destination = WORK / "test" / target / "isolated-runtime"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)
    return destination


def test_target(target: str) -> None:
    stage = isolated_stage(target)
    audit_target(target, stage)
    config = target_config(target)
    system = config["os"]
    env = os.environ.copy()
    # The test process sees only the extracted runtime. The original prefix is
    # never put in a loader search path.
    if system == "macosx":
        env["DYLD_LIBRARY_PATH"] = str(stage / "lib")
        env["DYLD_PRINT_LIBRARIES"] = "1"
    elif system == "linux":
        env["LD_LIBRARY_PATH"] = str(stage / "lib")
        env["LD_DEBUG"] = "libs"
    else:
        env["PATH"] = str(stage / "lib") + os.pathsep + env.get("PATH", "")
    smoke = ROOT / "tests" / "smoke_runtime.py"
    run([sys.executable, str(smoke), "--target", target, "--runtime", str(stage)], env=env)
    consumer = WORK / "consumer" / "racket-audio"
    if not consumer.is_dir():
        fail("missing pinned racket-audio source; run fetch before test")
    racket_collections = capture([
        "racket", "-e", "(require setup/dirs racket/path) (display (path->string (find-collects-dir)))",
    ])
    if racket_collections.startswith("unavailable:"):
        fail("Racket is required for the pinned consumer binding test")
    stubs = ROOT / "tests" / "racket-stubs"
    binding_env = env.copy()
    binding_env["PLTCOLLECTS"] = os.pathsep.join([str(stubs), racket_collections])
    run(["racket", str(ROOT / "tests" / "racket_binding_smoke.rkt"), str(consumer)], env=binding_env)
    print(f"package test completed from isolated directory: {stage}")


def validate_release_version(version: str) -> None:
    if not re.fullmatch(r"[0-9]+(?:[.][0-9A-Za-z-]+){1,3}", version):
        fail("release version must look like 1.2.3 or 1.2.3-rc1")


def package_target(target: str, version: str) -> Path:
    stage = WORK / "stage" / target
    if not stage.is_dir():
        fail(f"missing staged runtime for {target}; run stage first")
    validate_release_version(version)
    DIST.mkdir(exist_ok=True)
    if target_config(target)["os"] == "windows":
        output = DIST / f"racket-audio-native-libs-{target}-{version}.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())
    else:
        output = DIST / f"racket-audio-native-libs-{target}-{version}.tar.gz"
        with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(stage.rglob("*")):
                archive.add(path, arcname=path.relative_to(stage).as_posix(), recursive=False)
    print(output)
    return output


def complete(target: str, version: str, clean: bool) -> Path:
    build_target(target, clean=clean)
    stage_target(target)
    audit_target(target)
    test_target(target)
    return package_target(target, version)


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    sub = argument_parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate lock files, platforms, and patch references")
    sub.add_parser("fetch", help="download and verify every pinned source and consumer commit")
    for name in ("build", "stage", "audit", "test"):
        command = sub.add_parser(name)
        command.add_argument("--target", required=True)
        if name == "build":
            command.add_argument("--clean", action="store_true")
    package = sub.add_parser("package")
    package.add_argument("--target", required=True)
    package.add_argument("--version", required=True)
    all_command = sub.add_parser("all", help="fetch, build, stage, audit, test, and package")
    all_command.add_argument("--target", required=True)
    all_command.add_argument("--version", required=True)
    all_command.add_argument("--clean", action="store_true")
    return argument_parser


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.command == "validate":
            validate_lock()
        elif arguments.command == "fetch":
            validate_lock()
            lock = load_lock()
            for record in source_map(lock).values():
                fetch_record(record)
                source_directory(record)
            fetch_consumer(lock)
        elif arguments.command == "build":
            build_target(arguments.target, clean=arguments.clean)
        elif arguments.command == "stage":
            stage_target(arguments.target)
        elif arguments.command == "audit":
            audit_target(arguments.target)
        elif arguments.command == "test":
            test_target(arguments.target)
        elif arguments.command == "package":
            package_target(arguments.target, arguments.version)
        elif arguments.command == "all":
            validate_lock()
            lock = load_lock()
            for record in source_map(lock).values():
                fetch_record(record)
                source_directory(record)
            fetch_consumer(lock)
            complete(arguments.target, arguments.version, arguments.clean)
        return 0
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
