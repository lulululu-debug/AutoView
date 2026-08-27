package com.interview.unit;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.interview.domain.InterviewSession;
import com.interview.domain.json.AnswerAssessmentDto;
import com.interview.service.ai.AiClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.reactive.function.client.WebClient;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

/** AiClient 本地兜底路径单元测（ai.enabled=false，不触碰 WebClient）。 */
class AiClientHeuristicTest {

    private AiClient ai;

    @BeforeEach
    void setup() {
        ai = new AiClient(mock(WebClient.class), new ObjectMapper());
        ReflectionTestUtils.setField(ai, "enabled", false);
        ReflectionTestUtils.setField(ai, "assessTimeoutMs", 10000L);
    }

    @Test
    void heuristic_scoresByLengthAndKeywords() {
        InterviewSession s = new InterviewSession();            // answers=null → 不触发去重
        String answer = "我在项目里用 Redis 分布式锁解决并发问题，例如通过 SETNX 加过期时间，"
                + "并用 Lua 脚本保证释放的原子性，具体把 P99 从 900ms 降到 400ms。再补一些细节让长度足够。";
        AnswerAssessmentDto a = ai.assess(s, "谈谈分布式锁", answer);

        assertThat(a.via()).isEqualTo("heuristic");
        assertThat(a.confidence()).isEqualTo(0.3);             // 降级估计固定置信度
        assertThat(a.sufficiency()).isGreaterThan(0.5);        // 长且含具体性关键词 → 较高
    }

    @Test
    void ruleDuplicate_shortCircuitsWithoutLlm() {
        String answer = "这是一段被复制粘贴的重复回答内容需要足够长";  // ≥20 字符
        InterviewSession s = new InterviewSession();
        s.setAnswers(new ArrayList<>(List.of(new String(answer)))); // 等值但不同引用 → 命中去重

        AnswerAssessmentDto a = ai.assess(s, "任意问题", answer);

        assertThat(a.via()).isEqualTo("rule_duplicate");
        assertThat(a.sufficiency()).isZero();                  // 复制粘贴直接判 0
    }
}
