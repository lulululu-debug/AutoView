package com.interview.domain.json;

/**
 * 一道题的运行时表示（存进 plan.rounds / session.history）。
 * category: KNOWLEDGE / PROJECT_EXPERIENCE / SELF_INTRO / SCENARIO
 * lazy: project 阶段的题为 true（懒生成）。
 */
public record QuestionDto(
        String questionId,
        String text,
        String category,
        String competencyId,
        String stage,
        Boolean lazy
) {}
