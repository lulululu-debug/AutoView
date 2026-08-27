package com.interview.exception;

/** 越权访问 → 映射 HTTP 404（不泄露资源是否存在，防枚举）。 */
public class OwnershipException extends RuntimeException {
    public OwnershipException() {
        super("resource not found");
    }
}
