package com.interview.domain.json;

/**
 * 面试历史里的一轮（提问或回答），存进 interview_sessions.history (jsonb)。
 * role: "interviewer" | "candidate"
 * answered: 该提问是否已被回答（用于 resume 找最后一条未答提问）。
 */
public record TurnDto(
        String role,
        String text,
        String refId,
        String questionId,
        Boolean answered
) {}
