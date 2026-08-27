package com.interview.exception;

import com.interview.dto.resp.ApiResult;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(SessionBusyException.class)
    public ResponseEntity<ApiResult<Void>> busy(SessionBusyException e) {
        return ResponseEntity.status(409).body(ApiResult.err("SESSION_BUSY"));   // 并发冲突
    }

    @ExceptionHandler(OwnershipException.class)
    public ResponseEntity<ApiResult<Void>> ownership(OwnershipException e) {
        return ResponseEntity.status(404).body(ApiResult.err("NOT_FOUND"));      // 越权 → 404 防枚举
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiResult<Void>> invalid(MethodArgumentNotValidException e) {
        String msg = e.getBindingResult().getFieldErrors().stream()
                .findFirst().map(f -> f.getField() + " " + f.getDefaultMessage())
                .orElse("invalid request");
        return ResponseEntity.badRequest().body(ApiResult.err(msg));
    }

    @ExceptionHandler(BizException.class)
    public ResponseEntity<ApiResult<Void>> biz(BizException e) {
        return ResponseEntity.badRequest().body(ApiResult.err(e.getMessage()));
    }
}
