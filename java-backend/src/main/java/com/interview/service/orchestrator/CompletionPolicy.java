package com.interview.service.orchestrator;

import com.interview.domain.InterviewPlan;
import com.interview.domain.InterviewSession;
import com.interview.domain.json.QuestionDto;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 终止判定（纯规则）。绝不动态补题：题集在 plan 阶段确定，问完即止。
 */
@Component
@RequiredArgsConstructor
public class CompletionPolicy {

    private static final int MAX_TOTAL_QUESTIONS = 15;

    private final SessionEngine engine;

    public boolean isDone(InterviewSession s, InterviewPlan plan, List<QuestionDto> mains) {
        if (engine.totalQuestions(s) >= MAX_TOTAL_QUESTIONS) return true;   // 硬顶
        // 所有主问题都问过 → 结束（min_assessed_per_mandatory 的更严格版本可在 Python evaluate 侧补）
        return engine.nextMainQuestion(s, mains) == null;
    }
}
