package com.interview.exception;

/** 同一 session 并发写冲突（拿不到锁）→ HTTP 409。 */
public class SessionBusyException extends RuntimeException {
    public SessionBusyException() {
        super("another request is processing this session");
    }
}
