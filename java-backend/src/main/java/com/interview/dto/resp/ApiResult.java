package com.interview.dto.resp;

/** 统一响应包装。 */
public record ApiResult<T>(boolean success, String message, T data) {
    public static <T> ApiResult<T> ok(T data) {
        return new ApiResult<>(true, "ok", data);
    }
    public static <T> ApiResult<T> err(String message) {
        return new ApiResult<>(false, message, null);
    }
}
