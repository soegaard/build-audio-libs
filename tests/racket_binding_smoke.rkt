#lang racket/base

(require racket/path)

(define arguments (current-command-line-arguments))
(unless (= (vector-length arguments) 1)
  (error 'racket-binding-smoke "expected the pinned racket-audio source path"))
(define consumer (string->path (vector-ref arguments 0)))

(define (binding module-name export-name)
  (dynamic-require (build-path consumer module-name) export-name))

;; These are the real FFI modules from the lock-file commit. The only test
;; adaptation is the small logging/finalizer dependency stubs in this folder.
(unless ((binding "opusfile-decoder.rkt" 'opusfile-available?))
  (error 'racket-binding-smoke "libopusfile was not available"))
(unless ((binding "opus-encoder.rkt" 'opus-encoder-available?))
  (error 'racket-binding-smoke "libopusenc was not available"))
(unless ((binding "soxr-ffi.rkt" 'soxr-available?))
  (error 'racket-binding-smoke "libsoxr was not available"))
(unless (string? ((binding "soxr-ffi.rkt" 'soxr-version)))
  (error 'racket-binding-smoke "libsoxr did not return a version"))
(unless (= 62 (car ((binding "ffmpeg-definitions.rkt" 'ffmpeg-version) 'avcodec)))
  (error 'racket-binding-smoke "wrong libavcodec ABI"))
((binding "libflac-ffi.rkt" 'flac-ffi-encoder-handler))
((binding "libmpg123-ffi.rkt" 'mpg123-ffi-decoder-handler))
(binding "taglib-ffi.rkt" 'taglib_file_new)
(binding "libao-async-ffi-racket.rkt" 'ao_create_async)
(displayln "pinned racket-audio FFI binding smoke test passed")
