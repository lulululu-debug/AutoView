package com.interview.it;

import com.interview.domain.InterviewSession;
import com.interview.domain.json.AnswerAssessmentDto;
import com.interview.service.ai.AiClient;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.context.TestPropertySource;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Resilience4j:ai.enabled=true 但 Python 服务不可达(打不通的端口)时,
 * assess 走本地启发式兜底;连续失败后熔断打开、后续调用直接短路。
 */
@TestPropertySource(properties = {
        "ai.enabled=true",
        "ai.base-url=http://127.0.0.1:59999"   // 无人监听 → 连接被拒
})
class ResilienceIT extends AbstractIntegrationTest {

    @Autowired AiClient ai;
    @Autowired CircuitBreakerRegistry cbRegistry;

    @Test
    void remoteDown_fallsBackToHeuristic_andBreakerOpens() {
        InterviewSession s = new InterviewSession();   // answers=null → 不触发去重短路

        for (int i = 0; i < 8; i++) {
            AnswerAssessmentDto a = ai.assess(s, "问题", "这是第 " + i + " 个足够长的回答,用于触发远程调用与兜底。");
            assertThat(a.via()).as("远程不可达 → 走启发式").isEqualTo("heuristic");
        }

        assertThat(cbRegistry.circuitBreaker("ai").getState())
                .as("连续失败后熔断应打开")
                .isEqualTo(CircuitBreaker.State.OPEN);
    }
}
