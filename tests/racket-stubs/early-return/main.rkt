#lang racket/base

(require (for-syntax racket/base))

(provide define/return early-return)

(define-syntax-rule (define/return (name argument ...) return body ...)
  (define (name argument ...)
    (let/ec return body ...)))

;; This test-only form implements the guard syntax used by the pinned source.
;; A guard returns its fallback value; other bindings run in order.
(define-syntax (early-return stx)
  (syntax-case stx ()
    [(_ (entry ...) body ...)
     (letrec ([expand
               (lambda (entries)
                 (if (null? entries)
                     #'(begin body ...)
                     (let ([entry (car entries)]
                           [rest (cdr entries)])
                       (syntax-case entry (? => do)
                         [(name value ? test => fallback ~ cleanup ...)
                          #`(let ([name value])
                              (if test
                                  (begin cleanup ... fallback)
                                  (begin0 #,(expand rest) cleanup ...)))]
                         [(name value ? test => fallback)
                          #`(let ([name value])
                              (if test fallback #,(expand rest)))]
                         [(? test => fallback)
                          #`(if test fallback #,(expand rest))]
                         [(do expression ...)
                          #`(begin expression ... #,(expand rest))]
                         [(name value)
                          #`(let ([name value]) #,(expand rest))]
                         [_ (raise-syntax-error 'early-return "unsupported binding" stx entry)]))))])
       (expand (syntax->list #'(entry ...))))]))
