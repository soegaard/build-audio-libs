#lang racket/base

(provide define/return)

(define-syntax-rule (define/return (name argument ...) return body ...)
  (define (name argument ...)
    (let/ec return body ...)))
