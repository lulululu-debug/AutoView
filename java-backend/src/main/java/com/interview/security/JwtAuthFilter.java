package com.interview.security;

import io.jsonwebtoken.Claims;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.lang.NonNull;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;

/**
 * 从 httpOnly cookie("token") 优先、Authorization: Bearer 兜底 解析身份。
 * scope=candidate → ROLE_CANDIDATE(principal=candidateId)；scope=hr → ROLE_<role>。
 *
 * 注意：本类**不是** @Component —— 由 SecurityConfig 直接 new 并 addFilterBefore 进安全链。
 * 若加 @Component，Spring Boot 会把它同时注册成顶层 servlet 过滤器（在安全链之外运行），
 * 其设置的 SecurityContext 会被安全链的 SecurityContextHolderFilter 覆盖，导致认证丢失。
 */
@RequiredArgsConstructor
public class JwtAuthFilter extends OncePerRequestFilter {

    private final JwtService jwt;

    @Override
    protected void doFilterInternal(@NonNull HttpServletRequest req, @NonNull HttpServletResponse resp,
                                    @NonNull FilterChain chain) throws ServletException, IOException {
        String token = fromCookie(req);
        if (token == null) token = fromHeader(req);
        if (token != null && SecurityContextHolder.getContext().getAuthentication() == null) {
            try {
                Claims c = jwt.parse(token);
                String scope = c.get("scope", String.class);
                String principal = c.getSubject();
                String authority = "candidate".equals(scope)
                        ? "ROLE_CANDIDATE"
                        : "ROLE_" + String.valueOf(c.get("role", String.class)).toUpperCase();
                var auth = new UsernamePasswordAuthenticationToken(
                        principal, null, List.of(new SimpleGrantedAuthority(authority)));
                SecurityContextHolder.getContext().setAuthentication(auth);
            } catch (Exception ignored) {
                // token 无效 → 保持未认证，后续授权规则决定 401/403
            }
        }
        chain.doFilter(req, resp);
    }

    private String fromCookie(HttpServletRequest req) {
        if (req.getCookies() == null) return null;
        for (Cookie c : req.getCookies()) if ("token".equals(c.getName())) return c.getValue();
        return null;
    }

    private String fromHeader(HttpServletRequest req) {
        String h = req.getHeader("Authorization");
        return (h != null && h.startsWith("Bearer ")) ? h.substring(7) : null;
    }
}
