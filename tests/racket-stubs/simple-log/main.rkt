#lang racket/base

(require (for-syntax racket/base))

(provide sl-def-log)

;; The native smoke test imports FFI modules directly. This small test-only
;; stand-in keeps logging out of the test dependency graph.
(define-syntax (sl-def-log stx)
  (syntax-case stx ()
    [(_ _ name)
     (let ([suffix (symbol->string (syntax-e #'name))])
       (with-syntax ([dbg-name (datum->syntax #'name (string->symbol (string-append "dbg-" suffix)))]
                     [info-name (datum->syntax #'name (string->symbol (string-append "info-" suffix)))]
                     [err-name (datum->syntax #'name (string->symbol (string-append "err-" suffix)))]
                     [warn-name (datum->syntax #'name (string->symbol (string-append "warn-" suffix)))]
                     [fatal-name (datum->syntax #'name (string->symbol (string-append "fatal-" suffix)))]
                     [sync-name (datum->syntax #'name (string->symbol (string-append "sync-log-" suffix)))])
         #'(begin
             (define (dbg-name . _) (void))
             (define (info-name . _) (void))
             (define (err-name . _) (void))
             (define (warn-name . _) (void))
             (define (fatal-name . _) (void))
             (define (sync-name . _) (void)))))]))
