package com.interview.security;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Date;

/**
 * JWT 签发/校验。claim 精简：sub + scope(+role)，不放用户名/敏感信息（payload 仅 base64 非加密）。
 */
@Service
public class JwtService {

    private final SecretKey key;
    private final long expireMinutes;

    public JwtService(@Value("${jwt.secret}") String secret,
                      @Value("${jwt.expire-minutes}") long expireMinutes) {
        this.key = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
        this.expireMinutes = expireMinutes;
    }

    /** HR/admin token。 */
    public String issueHr(String userId, String role) {
        return build(userId, "hr", role);
    }

    /** 候选人一次性 token（补软鉴权：把候选人端也纳入 token 校验）。 */
    public String issueCandidate(String candidateId) {
        return build(candidateId, "candidate", null);
    }

    private String build(String subject, String scope, String role) {
        Instant now = Instant.now();
        var b = Jwts.builder()
                .subject(subject)
                .claim("scope", scope)
                .issuedAt(Date.from(now))
                .expiration(Date.from(now.plus(expireMinutes, ChronoUnit.MINUTES)));
        if (role != null) b.claim("role", role);
        return b.signWith(key).compact();
    }

    public Claims parse(String token) {
        return Jwts.parser().verifyWith(key).build().parseSignedClaims(token).getPayload();
    }
}
