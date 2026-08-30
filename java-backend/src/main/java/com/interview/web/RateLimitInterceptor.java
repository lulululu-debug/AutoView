package com.interview.web;

import com.interview.service.ratelimit.RateLimiterService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

import java.io.IOException;

/**
 * 按「客户端身份」维度限流(已登录用主体,否则用来源 IP)。超限返回 429。
 * 拦截器在安全过滤器链之后、Controller 之前执行,故能拿到 SecurityContext。
 */
@Component
@RequiredArgsConstructor
public class RateLimitInterceptor implements HandlerInterceptor {

    private final RateLimiterService limiter;

    @Value("${rate-limit.enabled:true}")
    private boolean enabled;
    @Value("${rate-limit.capacity:60}")
    private int capacity;
    @Value("${rate-limit.refill-per-second:30}")
    private double refillPerSec;

    @Override
    public boolean preHandle(HttpServletRequest req, HttpServletResponse resp, Object handler) throws IOException {
        if (!enabled) return true;
        String id = identity(req);
        if (limiter.tryAcquire(id, capacity, refillPerSec)) return true;

        resp.setStatus(429);
        resp.setContentType(MediaType.APPLICATION_JSON_VALUE);
        resp.getWriter().write("{\"success\":false,\"message\":\"RATE_LIMITED\",\"data\":null}");
        return false;
    }

    private String identity(HttpServletRequest req) {
        Authentication a = SecurityContextHolder.getContext().getAuthentication();
        if (a != null && a.isAuthenticated() && !"anonymousUser".equals(a.getName())) {
            return "user:" + a.getName();
        }
        String xff = req.getHeader("X-Forwarded-For");
        String ip = (xff != null && !xff.isBlank()) ? xff.split(",")[0].trim() : req.getRemoteAddr();
        return "ip:" + ip;
    }
}
