#!/usr/bin/env python3
"""Exercise safe API paths in the staged native audio libraries.

This test needs no audio device and does not use any build-prefix libraries.
It can run all checks, or one named check with ``--only``.
"""

from __future__ import annotations

import argparse
import ctypes
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path

from smoke_runtime import library_for


class ExerciseError(RuntimeError):
    """A native library did not complete its small API exercise."""


def function(handle: ctypes.CDLL, name: str, restype: object, *argtypes: object) -> Callable:
    result = getattr(handle, name)
    result.restype = restype
    result.argtypes = list(argtypes)
    return result


def require(condition: object, message: str) -> None:
    if not condition:
        raise ExerciseError(message)


def exercise_zlib(handles: dict[str, ctypes.CDLL], _: Path) -> None:
    zlib = handles["zlib"]
    compress = function(
        zlib, "compress", ctypes.c_int,
        ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_ulong,
    )
    uncompress = function(
        zlib, "uncompress", ctypes.c_int,
        ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_ulong,
    )
    compress_bound = function(zlib, "compressBound", ctypes.c_ulong, ctypes.c_ulong)
    source = b"racket-audio native library smoke test\n" * 8
    input_buffer = (ctypes.c_ubyte * len(source)).from_buffer_copy(source)
    compressed = (ctypes.c_ubyte * compress_bound(len(source)))()
    compressed_size = ctypes.c_ulong(len(compressed))
    require(compress(compressed, ctypes.byref(compressed_size), input_buffer, len(source)) == 0,
            "zlib could not compress test data")
    restored = (ctypes.c_ubyte * len(source))()
    restored_size = ctypes.c_ulong(len(restored))
    require(uncompress(restored, ctypes.byref(restored_size), compressed, compressed_size.value) == 0,
            "zlib could not decompress test data")
    require(bytes(restored[:restored_size.value]) == source, "zlib round trip changed the data")


def exercise_ogg(handles: dict[str, ctypes.CDLL], _: Path) -> None:
    ogg = handles["ogg"]
    sync_init = function(ogg, "ogg_sync_init", ctypes.c_int, ctypes.c_void_p)
    sync_clear = function(ogg, "ogg_sync_clear", ctypes.c_int, ctypes.c_void_p)
    # ogg_sync_state is an opaque C struct. This storage is larger and more
    # aligned than the struct on all supported targets.
    state = (ctypes.c_uint64 * 128)()
    require(sync_init(ctypes.byref(state)) == 0, "libogg could not initialize sync state")
    require(sync_clear(ctypes.byref(state)) == 0, "libogg could not clear sync state")


def exercise_opus(handles: dict[str, ctypes.CDLL], _: Path) -> None:
    opus = handles["opus"]
    version = function(opus, "opus_get_version_string", ctypes.c_char_p)
    create = function(
        opus, "opus_encoder_create", ctypes.c_void_p,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
    )
    encode = function(
        opus, "opus_encode", ctypes.c_int,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_short), ctypes.c_int,
        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int,
    )
    destroy = function(opus, "opus_encoder_destroy", None, ctypes.c_void_p)
    require(version(), "libopus did not report a version")
    error = ctypes.c_int()
    encoder = create(48000, 1, 2049, ctypes.byref(error))  # OPUS_APPLICATION_AUDIO
    require(encoder and error.value == 0, f"libopus could not create an encoder: {error.value}")
    try:
        pcm = (ctypes.c_short * 960)()
        packet = (ctypes.c_ubyte * 4000)()
        require(encode(encoder, pcm, 960, packet, len(packet)) > 0,
                "libopus could not encode a silent frame")
    finally:
        destroy(encoder)


def exercise_opusenc_and_opusfile(handles: dict[str, ctypes.CDLL], temporary: Path) -> None:
    opusenc = handles["opusenc"]
    opusfile = handles["opusfile"]
    comments_create = function(opusenc, "ope_comments_create", ctypes.c_void_p)
    comments_destroy = function(opusenc, "ope_comments_destroy", None, ctypes.c_void_p)
    create_file = function(
        opusenc, "ope_encoder_create_file", ctypes.c_void_p,
        ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int32, ctypes.c_int,
        ctypes.c_int, ctypes.POINTER(ctypes.c_int),
    )
    write_float = function(
        opusenc, "ope_encoder_write_float", ctypes.c_int,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int,
    )
    drain = function(opusenc, "ope_encoder_drain", ctypes.c_int, ctypes.c_void_p)
    destroy = function(opusenc, "ope_encoder_destroy", None, ctypes.c_void_p)
    open_file = function(opusfile, "op_open_file", ctypes.c_void_p,
                         ctypes.c_char_p, ctypes.POINTER(ctypes.c_int))
    channel_count = function(opusfile, "op_channel_count", ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
    read = function(opusfile, "op_read", ctypes.c_int,
                    ctypes.c_void_p, ctypes.POINTER(ctypes.c_short), ctypes.c_int,
                    ctypes.POINTER(ctypes.c_int))
    free = function(opusfile, "op_free", None, ctypes.c_void_p)

    output = temporary / "silence.opus"
    comments = comments_create()
    require(comments, "libopusenc could not create comments")
    error = ctypes.c_int()
    encoder = create_file(str(output).encode(), comments, 48000, 1, 0, ctypes.byref(error))
    comments_destroy(comments)
    require(encoder and error.value == 0,
            f"libopusenc could not create an Opus file: {error.value}")
    try:
        pcm = (ctypes.c_float * 960)()
        require(write_float(encoder, pcm, 960) == 0, "libopusenc could not write PCM")
        require(drain(encoder) == 0, "libopusenc could not finish the Opus file")
    finally:
        destroy(encoder)
    require(output.stat().st_size > 0, "libopusenc created an empty Opus file")

    error = ctypes.c_int()
    decoder = open_file(str(output).encode(), ctypes.byref(error))
    require(decoder and error.value == 0,
            f"libopusfile could not open the generated Opus file: {error.value}")
    try:
        require(channel_count(decoder, -1) == 1, "libopusfile reported the wrong channel count")
        pcm = (ctypes.c_short * 960)()
        link = ctypes.c_int()
        require(read(decoder, pcm, len(pcm), ctypes.byref(link)) > 0,
                "libopusfile could not decode a frame")
    finally:
        free(decoder)


def exercise_flac(handles: dict[str, ctypes.CDLL], _: Path) -> None:
    flac = handles["FLAC"]
    create = function(flac, "FLAC__stream_encoder_new", ctypes.c_void_p)
    destroy = function(flac, "FLAC__stream_encoder_delete", None, ctypes.c_void_p)
    encoder = create()
    require(encoder, "libFLAC could not allocate a stream encoder")
    destroy(encoder)


def exercise_mpg123(handles: dict[str, ctypes.CDLL], _: Path) -> None:
    mpg123 = handles["mpg123"]
    initialize = function(mpg123, "mpg123_init", ctypes.c_int)
    create = function(mpg123, "mpg123_new", ctypes.c_void_p,
                      ctypes.c_char_p, ctypes.POINTER(ctypes.c_int))
    destroy = function(mpg123, "mpg123_delete", None, ctypes.c_void_p)
    shutdown = function(mpg123, "mpg123_exit", None)
    require(initialize() == 0, "mpg123 could not initialize")
    try:
        error = ctypes.c_int()
        decoder = create(None, ctypes.byref(error))
        require(decoder and error.value == 0,
                f"mpg123 could not create a decoder: {error.value}")
        destroy(decoder)
    finally:
        shutdown()


def exercise_soxr(handles: dict[str, ctypes.CDLL], _: Path) -> None:
    soxr = handles["soxr"]
    create = function(
        soxr, "soxr_create", ctypes.c_void_p,
        ctypes.c_double, ctypes.c_double, ctypes.c_uint, ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    )
    process = function(
        soxr, "soxr_process", ctypes.c_char_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    )
    destroy = function(soxr, "soxr_delete", None, ctypes.c_void_p)
    error = ctypes.c_char_p()
    resampler = create(44100.0, 48000.0, 1, ctypes.byref(error), None, None, None)
    require(resampler and not error.value, f"libsoxr could not create a resampler: {error.value!r}")
    try:
        source = (ctypes.c_float * 1024)()
        destination = (ctypes.c_float * 1200)()
        consumed = ctypes.c_size_t()
        produced = ctypes.c_size_t()
        result = process(resampler, source, len(source), ctypes.byref(consumed),
                         destination, len(destination), ctypes.byref(produced))
        require(not result, f"libsoxr could not resample PCM: {result!r}")
        require(consumed.value == len(source), "libsoxr did not consume the input")
        require(produced.value > 0, "libsoxr did not produce output")
    finally:
        destroy(resampler)


def make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x00" * 80)


def exercise_taglib(handles: dict[str, ctypes.CDLL], temporary: Path) -> None:
    taglib = handles["tag_c"]
    open_file = function(taglib, "taglib_file_new", ctypes.c_void_p, ctypes.c_char_p)
    valid = function(taglib, "taglib_file_is_valid", ctypes.c_bool, ctypes.c_void_p)
    properties = function(taglib, "taglib_file_audioproperties", ctypes.c_void_p, ctypes.c_void_p)
    sample_rate = function(taglib, "taglib_audioproperties_samplerate", ctypes.c_int, ctypes.c_void_p)
    channels = function(taglib, "taglib_audioproperties_channels", ctypes.c_int, ctypes.c_void_p)
    close_file = function(taglib, "taglib_file_free", None, ctypes.c_void_p)
    wav_file = temporary / "silence.wav"
    make_wav(wav_file)
    audio_file = open_file(str(wav_file).encode())
    require(audio_file, "TagLib could not open the generated WAV file")
    try:
        require(valid(audio_file), "TagLib marked the generated WAV file invalid")
        info = properties(audio_file)
        require(info, "TagLib did not return WAV audio properties")
        require(sample_rate(info) == 8000, "TagLib reported the wrong WAV sample rate")
        require(channels(info) == 1, "TagLib reported the wrong WAV channel count")
    finally:
        close_file(audio_file)


def exercise_ffmpeg(handles: dict[str, ctypes.CDLL], _: Path) -> None:
    expected_majors = {"avutil": 60, "avcodec": 62, "avformat": 62, "swresample": 6}
    for name, expected in expected_majors.items():
        version = function(handles[name], f"{name}_version", ctypes.c_uint)
        actual = version() >> 16
        require(actual == expected,
                f"{name} ABI major is {actual}, expected {expected}")
    allocate = function(handles["swresample"], "swr_alloc", ctypes.c_void_p)
    free = function(handles["swresample"], "swr_free", None, ctypes.POINTER(ctypes.c_void_p))
    context = ctypes.c_void_p(allocate())
    require(context.value, "libswresample could not allocate a context")
    free(ctypes.byref(context))
    require(not context.value, "libswresample did not release its context")


EXERCISES: dict[str, Callable[[dict[str, ctypes.CDLL], Path], None]] = {
    "zlib": exercise_zlib,
    "ogg": exercise_ogg,
    "opus": exercise_opus,
    "opusenc-and-opusfile": exercise_opusenc_and_opusfile,
    "flac": exercise_flac,
    "mpg123": exercise_mpg123,
    "soxr": exercise_soxr,
    "taglib": exercise_taglib,
    "ffmpeg": exercise_ffmpeg,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--only", choices=EXERCISES)
    args = parser.parse_args()
    library = args.runtime / "lib"
    if not library.is_dir():
        raise ExerciseError(f"not a runtime layout: {args.runtime}")
    if ".work/prefix" in str(args.runtime):
        raise ExerciseError("test was pointed at a build prefix")

    handles = {
        stem: ctypes.CDLL(str(library_for(library, stem)))
        for stem in ("zlib", "ogg", "opus", "opusenc", "opusfile", "FLAC", "mpg123", "soxr",
                     "tag_c", "avutil", "swresample", "avcodec", "avformat")
    }
    selected = [args.only] if args.only else list(EXERCISES)
    with tempfile.TemporaryDirectory(prefix="racket-audio-native-smoke-") as directory:
        temporary = Path(directory)
        for name in selected:
            EXERCISES[name](handles, temporary)
            print(f"{name}: passed")
    print(f"native API exercise test passed for {args.target}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AttributeError, OSError, RuntimeError, ExerciseError) as exc:
        print(f"native API exercise test failed: {exc}")
        raise SystemExit(2)
