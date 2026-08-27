package com.interview.domain.json;

import java.util.List;

/**
 * 单题评估结果（存进 interview_sessions.assessments）。
 * 由 Python AI 服务返回；calibratedSufficiency 仅 LLM 路径有，启发式兜底为 null。
 * via: "llm" | "heuristic" | "rule_duplicate"
 * beliefAllowsProbe: Python 侧信念方差门的结论，Java 追问决策直接用。
 */
public record AnswerAssessmentDto(
        Double sufficiency,
        Double confidence,
        Double calibratedSufficiency,
        List<String> missingSignals,
        List<String> strengths,
        List<String> concerns,
        String followupGoal,
        String stopReason,
        String via,
        Boolean beliefAllowsProbe,
        List<Boolean> rubricHits
) {}
