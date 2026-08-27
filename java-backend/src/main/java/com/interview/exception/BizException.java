package com.interview.exception;

/** 一般业务错误 → HTTP 400。 */
public class BizException extends RuntimeException {
    public BizException(String message) {
        super(message);
    }
}
