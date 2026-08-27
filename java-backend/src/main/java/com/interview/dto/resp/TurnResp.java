package com.interview.dto.resp;

import com.interview.domain.InterviewSession;
import com.interview.domain.json.TurnDto;

/**
 * 一轮交互后返回给前端的下一步提示。
 * done=true 表示面试已结束（应引导前端调用 finalize 拿报告）。
 */
public record TurnResp(
        String sessionId,
        String status,
        String prompt,
        String refId,
        boolean done
) {
    public static TurnResp of(InterviewSession s) {
        if ("COMPLETED".equals(s.getStatus())) {
            return new TurnResp(s.getSessionId(), s.getStatus(), null, null, true);
        }
        TurnDto p = lastUnanswered(s);
        return new TurnResp(s.getSessionId(), s.getStatus(),
                p == null ? null : p.text(),
                p == null ? null : p.refId(),
                false);
    }

    private static TurnDto lastUnanswered(InterviewSession s) {
        if (s.getHistory() == null) return null;
        for (int i = s.getHistory().size() - 1; i >= 0; i--) {
            TurnDto t = s.getHistory().get(i);
            if ("interviewer".equals(t.role()) && !Boolean.TRUE.equals(t.answered())) {
                return t;
            }
        }
        return null;
    }
}
