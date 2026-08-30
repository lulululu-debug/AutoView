package com.interview.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.lang.NonNull;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Optional;
import java.util.UUID;

/**
 * 全链路 traceId:每个请求生成(或透传上游 X-Trace-Id)一个 traceId,放进 MDC(日志可打)
 * 并回写响应头,便于排查。最高优先级 → 早于安全链执行,覆盖全部请求(含错误)。
 * 仅作为 servlet 级过滤器注册(不进安全链),避免重复注册。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceIdFilter extends OncePerRequestFilter {

    private static final String HEADER = "X-Trace-Id";

    @Override
    protected void doFilterInternal(@NonNull HttpServletRequest req, @NonNull HttpServletResponse resp,
                                    @NonNull FilterChain chain) throws ServletException, IOException {
        String traceId = Optional.ofNullable(req.getHeader(HEADER))
                .filter(s -> !s.isBlank())
                .orElse(UUID.randomUUID().toString().replace("-", "").substring(0, 16));
        MDC.put("traceId", traceId);
        resp.setHeader(HEADER, traceId);
        try {
            chain.doFilter(req, resp);
        } finally {
            MDC.remove("traceId");
        }
    }
}
