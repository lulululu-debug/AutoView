package com.interview.unit;

import com.interview.domain.json.AnswerAssessmentDto;

import java.util.List;

/** 测试用构造助手。 */
final class TestFixtures {
    private TestFixtures() {}

    static AnswerAssessmentDto assessment(Double sufficiency, Double confidence,
                                          Double calibrated, Boolean beliefAllowsProbe) {
        return new AnswerAssessmentDto(sufficiency, confidence, calibrated,
                List.of(), List.of(), List.of(), "goal", null, "llm", beliefAllowsProbe, List.of());
    }
}
