package com.interview.dto.resp;

import com.interview.domain.EvaluationReport;

public record ReportResp(
        String sessionId,
        Double overall,
        Boolean needsHumanReview,
        Object contentScores
) {
    public static ReportResp of(EvaluationReport r) {
        return new ReportResp(r.getSessionId(), r.getOverall(),
                r.getNeedsHumanReview(), r.getContentScores());
    }
}
