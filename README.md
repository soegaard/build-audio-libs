# racket-audio native library builds

This repository builds native shared libraries for 
[racket-audio](https://git.dijkewijk.nl/hans/racket-audio). 

The idea is to use Github Actions to build binaries for all platforms (macOS, Linux and Windows).
The binaries are then distributed via Hans Dijkema's `racket-audio-native-libs` repository.


Each platform has its own build job on Github Actions.

| Target           | Package branch   | Build host                       | Compatibility baseline |
| ---              | ---              | ---                              | --- |
| `aarch64-macosx` | `aarch64-macosx` | GitHub `macos-26`, arm64         | macOS 11.0, arm64 |
| `x86_64-win32`   | `x86_64-win32`   | GitHub `windows-2022`, x64       | Windows 10 / Server 2016, x64 |
| `x86_64-linux`   | `x86_64-linux`   | GitHub `ubuntu-22.04`, x64       | glibc 2.35, x64 |
| `aarch64-linux`  | `aarch64-linux`  | GitHub `ubuntu-22.04-arm`, arm64 | glibc 2.35, arm64 |


The scripts save information on runner labels and architectures in `platforms.json`. 
Each build records the exact image, compiler, and tool versions in `provenance.json`.

## Quick start

On a target machine with a C/C++ compiler, CMake, Ninja, GNU make, pkg-config, patch, and Python 3.9+:

```sh
python3 scripts/audio_build.py validate
python3 scripts/audio_build.py fetch
python3 scripts/audio_build.py build   --target aarch64-macosx --clean
python3 scripts/audio_build.py stage   --target aarch64-macosx
python3 scripts/audio_build.py audit   --target aarch64-macosx
python3 scripts/audio_build.py test    --target aarch64-macosx
python3 scripts/audio_build.py package --target aarch64-macosx --version 1.0.0
```

Or run the complete pipeline:

```sh
python3 scripts/audio_build.py all --target aarch64-macosx --version 1.0.0 --clean
```

In order to avoid dependencies we are building the binaries from sources.
This is simpler in the long run than relying on e.g. Homebrew.


See [building](docs/BUILD.md), [releases](docs/RELEASE.md), and [the handoff](docs/HANDOFF.md).
