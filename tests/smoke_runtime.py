#!/usr/bin/env python3
"""Tests the extracted runtime without referring to its build prefix."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path


REQUIRED_SYMBOLS = {
    "avutil": "avutil_version",
    "avcodec": "avcodec_version",
    "avformat": "avformat_version",
    "swresample": "swresample_version",
    "FLAC": "FLAC__stream_encoder_new",
    "mpg123": "mpg123_init",
    "soxr": "soxr_create",
    "tag_c": "taglib_file_new",
    "opus": "opus_get_version_string",
    "opusfile": "op_open_file",
    "opusenc": "ope_encoder_create_file",
    "ogg": "ogg_sync_init",
    "ao": "ao_initialize",
}


def library_for(directory: Path, stem: str) -> Path:
    candidates = sorted(directory.glob(f"*{stem}*.dylib"))
    candidates += sorted(directory.glob(f"*{stem}*.so*"))
    candidates += sorted(directory.glob(f"*{stem}*.dll"))
    if not candidates:
        raise RuntimeError(f"missing library containing {stem}")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    library = args.runtime / "lib"
    if not library.is_dir():
        raise RuntimeError(f"not a runtime layout: {args.runtime}")
    if ".work/prefix" in str(args.runtime):
        raise RuntimeError("test was pointed at a build prefix")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(library))
    loaded = {}
    loaded_paths = {}
    # Load dependency leaves before users so Windows and macOS cannot hide a
    # missing direct dependency through an unrelated host installation.
    order = ["ogg", "opus", "FLAC", "mpg123", "soxr", "tag_c", "opusfile", "opusenc", "ao", "avutil", "swresample", "avcodec", "avformat"]
    for stem in order:
        path = library_for(library, stem)
        handle = ctypes.CDLL(str(path))
        symbol = REQUIRED_SYMBOLS[stem]
        getattr(handle, symbol)
        loaded[stem] = handle
        loaded_paths[stem] = path
    # The libao static file and null drivers must work without hardware.
    ao = loaded["ao"]
    ao.ao_initialize()
    ao.ao_shutdown()
    ffmpeg_major = loaded["avcodec"].avcodec_version() >> 16
    if ffmpeg_major != 62:
        raise RuntimeError(f"wrong libavcodec ABI major: {ffmpeg_major}, expected 62")
    print("loaded runtime libraries:")
    for stem, path in loaded_paths.items():
        print(f"  {stem}: {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, AttributeError) as exc:
        print(f"smoke test failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
