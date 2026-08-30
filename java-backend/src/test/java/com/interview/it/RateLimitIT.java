package com.interview.it;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.http.*;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.test.context.TestPropertySource;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/** 令牌桶限流:突发超过容量后返回 429（专用低容量配置,单独上下文）。 */
@TestPropertySource(properties = {
        "rate-limit.enabled=true",
        "rate-limit.capacity=5",
        "rate-limit.refill-per-second=0.1"   // 回填极慢,突发期间基本不回血
})
class RateLimitIT extends AbstractIntegrationTest {

    @LocalServerPort int port;
    private TestRestTemplate rest;

    @BeforeEach
    void init() {
        rest = new TestRestTemplate(new RestTemplateBuilder()
                .rootUri("http://localhost:" + port)
                .requestFactory(SimpleClientHttpRequestFactory.class));
    }

    @Test
    void burstBeyondCapacity_gets429() {
        int ok = 0, limited = 0;
        for (int i = 0; i < 15; i++) {
            var r = rest.postForEntity("/auth/candidate-token?candidateId=c" + i, null, String.class);
            int code = r.getStatusCode().value();
            if (code == 200) ok++;
            else if (code == 429) limited++;
        }
        // 容量 5、几乎不回血:约前 5 个放行,其余被限流
        assertThat(ok).as("容量内放行").isBetween(1, 6);
        assertThat(limited).as("超限被 429").isGreaterThanOrEqualTo(1);
        assertThat(ok + limited).isEqualTo(15);
    }
}
