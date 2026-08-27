package com.interview.unit;

import com.interview.security.JwtService;
import io.jsonwebtoken.Claims;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.*;

class JwtServiceTest {

    private static final String SECRET = "unit-test-secret-at-least-32-bytes-long-0123456789";

    @Test
    void hrTokenRoundTrip() {
        JwtService jwt = new JwtService(SECRET, 60);
        String token = jwt.issueHr("u1", "hr");
        Claims c = jwt.parse(token);
        assertThat(c.getSubject()).isEqualTo("u1");
        assertThat(c.get("scope", String.class)).isEqualTo("hr");
        assertThat(c.get("role", String.class)).isEqualTo("hr");
    }

    @Test
    void candidateTokenHasCandidateScope() {
        JwtService jwt = new JwtService(SECRET, 60);
        Claims c = jwt.parse(jwt.issueCandidate("cand-1"));
        assertThat(c.getSubject()).isEqualTo("cand-1");
        assertThat(c.get("scope", String.class)).isEqualTo("candidate");
    }

    @Test
    void tamperedTokenRejected() {
        JwtService jwt = new JwtService(SECRET, 60);
        String token = jwt.issueHr("u1", "hr");
        String tampered = token.substring(0, token.length() - 2) + (token.endsWith("aa") ? "bb" : "aa");
        assertThatThrownBy(() -> jwt.parse(tampered)).isInstanceOf(Exception.class);
    }

    @Test
    void expiredTokenRejected() {
        JwtService jwt = new JwtService(SECRET, -1);   // 立即过期
        String token = jwt.issueHr("u1", "hr");
        assertThatThrownBy(() -> jwt.parse(token)).isInstanceOf(Exception.class);
    }
}
