# Handoff to racket-audio-native-libs

Hans's repository is the distribution repository. This repository only produces release-ready source-build archives. Do not commit, push, register, or publish anything in Hans's repository from this build repository.

| Build archive target | Hans branch | Racket package |
| --- | --- | --- |
| `aarch64-macosx` | `aarch64-macosx` | `racket-audio-aarch64-macosx` |
| `x86_64-win32` | `x86_64-win32` | `racket-audio-x86_64-win32` |
| `x86_64-linux` | `x86_64-linux` | `racket-audio-x86_64-linux` |
| `aarch64-linux` | `aarch64-linux` | `racket-audio-aarch64-linux` |

For each target, extract the release archive and copy the `lib/` file contents into the root of the matching branch. Preserve `ao/plugins-4/` in the source handoff directory. Racket's `copy-foreign-libs` installs files into its foreign-library directory by basename, so list every shared library and each libao driver plugin. The libao patch deliberately falls back to that flattened directory, while the archive keeps the normal plugin layout for audit and non-Racket users.

Generate `info.rkt` from the staged archive rather than maintaining a manual list. The following template has placeholders that must be replaced with the actual target, version, and files from `runtime-manifest.json`:

```racket
#lang info
(define collection 'multi)
(define version "BUNDLE-VERSION")
(define pkg-desc "native libraries for racket-audio on TARGET")
(define pkg-authors '(hans))
(define license '(GPL-2.0-or-later))
(define install-platform "TARGET")
(define copy-foreign-libs
  '("lib/libavutil.60.dylib"
    "lib/libavcodec.62.dylib"
    "lib/libavformat.62.dylib"
    "lib/libswresample.6.dylib"
    "lib/ao/plugins-4/libmacosx.dylib"))
(define deps '("base"))
```

For Windows, list every bundled DLL, including the deliberate `libao-1.2.2.dll` compatibility alias and all direct non-system dependencies. Do not rename FFmpeg DLLs: the FFmpeg 8 build already produces the ABI names used by the pinned consumer. For Linux, list the SONAME file plus any required symlink or dependency file shown by the runtime manifest.

Before publishing a branch, install it in an isolated Racket user depot, run `raco setup`, and require the pinned `racket-audio` revision. Check the Racket foreign-library directory and run the same decoder, encoder, resampler, metadata, and libao file/null tests used for the released archive. Do not let `racket-audio` download its older Windows bundle; the test must use the copied files.

The branch currently contains an empty Apple Silicon `info.rkt` template, so the first handoff must use the generated manifest as the authoritative file list.
