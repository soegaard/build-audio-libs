#lang racket/base

(require (for-syntax racket/base))

(provide let-assert
         let/assert
         a-!nullptr?
         a-nullptr?
         a-false?
         a-true?
         a->=?
         a->?
         a-<=?
         a-!eq?)

;; These predicates cover the assertions used by the pinned FFI modules.
(define (a-nullptr? value) (not value))
(define (a-!nullptr? value) (not (a-nullptr? value)))
(define (a-false? value) (eq? value #f))
(define (a-true? value) (not (a-false? value)))
(define (a->=? limit) (lambda (value) (and (real? value) (>= value limit))))
(define (a->? limit) (lambda (value) (and (real? value) (> value limit))))
(define (a-<=? limit) (lambda (value) (and (real? value) (<= value limit))))
(define (a-!eq? other) (lambda (value) (not (equal? value other))))

;; A failed assertion evaluates its fallback. Bindings are evaluated in order.
(define-syntax (let/assert stx)
  (syntax-case stx ()
    [(_ (entry ...) body ...)
     (letrec ([expand
               (lambda (entries)
                 (if (null? entries)
                     #'(begin body ...)
                     (let ([entry (car entries)]
                           [rest (cdr entries)])
                       (syntax-case entry ()
                         [(name value predicate fallback)
                          #`(let ([name value])
                              (if (predicate name) #,(expand rest) fallback))]
                         [(name value)
                          #`(let ([name value]) #,(expand rest))]
                         [_ (raise-syntax-error 'let/assert "unsupported binding" stx entry)]))))])
       (expand (syntax->list #'(entry ...))))]))

(define-syntax-rule (let-assert bindings body ...)
  (let/assert bindings body ...))
