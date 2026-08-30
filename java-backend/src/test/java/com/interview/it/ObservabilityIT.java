package com.interview.it;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 可观测性:Actuator 健康检查 / Prometheus 指标 / 响应带 traceId。
 * @AutoConfigureObservability:@SpringBootTest 默认关掉 metrics 导出,这里显式打开,
 * 否则 /actuator/prometheus 因无 PrometheusMeterRegistry 而 404（仅测试环境如此,生产正常）。
 */
@AutoConfigureObservability
class ObservabilityIT extends AbstractIntegrationTest {

    @LocalServerPort int port;
    private TestRestTemplate rest;

    @BeforeEach
    void init() {
        rest = new TestRestTemplate(new RestTemplateBuilder()
                .rootUri("http://localhost:" + port)
                .requestFactory(SimpleClientHttpRequestFactory.class));
    }

    @Test
    void health_isUp() {
        ResponseEntity<String> r = rest.getForEntity("/actuator/health", String.class);
        assertThat(r.getStatusCode().value()).isEqualTo(200);
        assertThat(r.getBody()).contains("\"status\":\"UP\"");
    }

    @Test
    void prometheus_exposesMetrics() {
        ResponseEntity<String> r = rest.getForEntity("/actuator/prometheus", String.class);
        assertThat(r.getStatusCode().value()).isEqualTo(200);
        assertThat(r.getBody()).contains("jvm_");   // 有 JVM 指标即说明 Micrometer/Prometheus 生效
    }

    @Test
    void everyResponse_carriesTraceId() {
        ResponseEntity<String> r = rest.getForEntity("/actuator/health", String.class);
        assertThat(r.getHeaders().getFirst("X-Trace-Id")).as("响应应带 traceId").isNotBlank();
    }
}
