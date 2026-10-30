#lang racket/base

(provide let-assert)

(define-syntax-rule (let-assert bindings body ...)
  (let bindings body ...))
