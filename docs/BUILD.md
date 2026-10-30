# Building and testing

## Inputs

`sources.lock.json` is the only source lock. 
It pins every native source archive by HTTPS URL and SHA-256. 
It also pins the consumer revision:

```text
racket-audio b1c6e3976a5ca63c5fa41564eea31c80d110d06e
```

That revision loads libao, libmpg123, libFLAC, libogg, libopus, libopusfile, libopusenc, 
libsoxr, TagLib and its C API, zlib, and FFmpeg `libavutil`, `libavcodec`, `libavformat`, and `libswresample`. 

Its Windows FFmpeg literals are `avutil-60.dll`, `avcodec-62.dll`, `avformat-62.dll`, and `swresample-6.dll`. 
FFmpeg 8.0.1 produces exactly those ABI majors.

TagLib 2.1 also needs `utf8cpp`. 
It is a source-only header dependency and is locked in the same file. 
No source is fetched by CMake FetchContent, a submodule update, or another package manager.

`fetch` downloads sources into `.work/downloads`, verifies every SHA-256 on both download and cache hit, and safely extracts them. 

`build` will fail if a verified input is absent; it does not download inputs itself.


## Prerequisites

Use a normal platform toolchain. Do not install audio libraries with Homebrew, apt, pacman, vcpkg, or another package manager.

- macOS: Xcode command-line tools, CMake, Ninja, GNU make, pkg-config, Autotools, libtool, patch, Python 3.9+.
- Linux: GCC or Clang, CMake, Ninja, GNU make, pkg-config, Autotools, libtool, `patchelf`, patch, Python 3.9+. 
  ALSA headers are an allowed OS dependency for the optional libao ALSA plugin.
- Windows: MSYS2 UCRT64, GCC, CMake, Ninja, GNU make, pkgconf, Autotools, libtool, patch, NASM, Python 3.9+. 
  The workflow installs build tools only; it does not install MSYS2 audio packages.

Build tools can be supplied by an official platform SDK or a verified tool download. 
The build sets a private pkg-config directory, include path, library path, and CMake ignore prefix so it cannot discover `/opt/homebrew` audio libraries.

## Commands

```sh
python3 scripts/audio_build.py validate
python3 scripts/audio_build.py fetch
python3 scripts/audio_build.py build   --target aarch64-macosx --clean
python3 scripts/audio_build.py stage   --target aarch64-macosx
python3 scripts/audio_build.py audit   --target aarch64-macosx
python3 scripts/audio_build.py test    --target aarch64-macosx
python3 scripts/audio_build.py package --target aarch64-macosx --version 1.0.0
```

`stage` creates a runtime layout with `lib/`, `lib/ao/plugins-4/`, license texts, a build provenance record, and a machine-readable file manifest. 
Unix archives preserve symlinks and modes. Windows uses zip and includes the MinGW runtime DLLs needed by the built C and C++ libraries, with their license texts.

`test` first copies the staged runtime to another directory. It never adds the original build prefix to a loader path. 
It then checks library loading, required symbols, the FFmpeg ABI, and libao init/shutdown using only the extracted runtime. 
The audit checks filenames, architecture, and loader metadata. On Unix it also checks RPATH or install names and forbidden build or Homebrew paths.

The GitHub workflow runs the same `all` command for every target. 
It uploads the archive, runtime manifest, provenance, FFmpeg configuration, and audit report. 
A device-independent libao file/null test is suitable for CI. 
Real audible playback needs a local device test and is not claimed by CI.


## Library configuration

FFmpeg is built as shared libraries with programs, network, device capture, filters, and uncontrolled host detection disabled. 
The enabled local protocols are `file`, `pipe`, and `data`; 
the enabled demuxers and audio decoders are listed in `scripts/audio_build.py`. 
The final `ffbuild/config.mak` is saved as a report.

Every macOS library uses `MACOSX_DEPLOYMENT_TARGET=11.0`. 
Staging changes install names to loader-relative names, removes build-prefix references, and ad-hoc signs changed Mach-O files. 
No certificate is needed. 

Linux libraries receive an `$ORIGIN` RPATH and are checked for their dynamic dependency information. 
glibc and libstdc++ are not bundled.

libao needs more than `libao` itself. Its optional drivers live in `lib/ao/plugins-4`. 
`patches/libao-relocatable-plugin-path.patch` derives that directory from the loaded library on macOS. 
It also has a restricted fallback for Racket's flattened `copy-foreign-libs` installation. 
The patch is recorded, hashed, dry-run tested, and included in provenance.

## Updating a dependency

1. Read the upstream change notes and the consumer FFI module that uses the library.
2. Download the exact release archive from the upstream project.
3. Calculate its SHA-256 locally and update one entry in `sources.lock.json`.
4. Update the matching recipe only when build options must change.
5. Add or update a checked-in patch when needed. Do not use an unrecorded local edit.
6. Run `validate`, the orchestration tests, and all target CI jobs.
7. Review runtime manifests, symbol reports, ABI names, licenses, and the handoff template.

The normal build never updates the lock file or follows a moving branch, tag, or `latest` URL.
