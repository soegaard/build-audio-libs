#lang racket/base

(provide sl-def-log)

;; The native smoke test imports FFI modules directly. This small test-only
;; stand-in keeps logging out of the test dependency graph.
(define-syntax-rule (sl-def-log _ sound)
  (begin
    (define (dbg-sound . _) (void))
    (define (info-sound . _) (void))
    (define (err-sound . _) (void))
    (define (warn-sound . _) (void))
    (define (fatal-sound . _) (void))
    (define (sync-log-sound . _) (void))))
