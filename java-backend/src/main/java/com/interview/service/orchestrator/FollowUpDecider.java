package com.interview.service.orchestrator;

import com.interview.domain.InterviewSession;
import com.interview.domain.json.AnswerAssessmentDto;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

/**
 * 追问决策（纯规则，Java 本地）。分层短路，对应 Python _decide_followup。
 */
@Component
@RequiredArgsConstructor
public class FollowUpDecider {

    private static final int GLOBAL_FOLLOWUP_BUDGET = 5;
    private static final int MAX_TOTAL_QUESTIONS = 15;
    private static final double RAW_STOP = 0.6;
    private static final double CALIBRATED_STOP = 0.9269;   // sigmoid(8.142082*0.6-2.344218)，与 raw 0.6 等效
    private static final double CONFIDENCE_MIN = 0.5;

    private final SessionEngine engine;

    public boolean shouldFollowUp(InterviewSession s, AnswerAssessmentDto a) {
        String stage = s == null ? null : engine.currentStage(s);

        if (engine.followupCountForCurrent(s) >= engine.maxFollowups(stage)) return false; // 每题上限硬停
        if ("self_intro".equals(stage)) return false;                                      // 自我介绍 0 追问
        if (engine.usedFollowupBudget(s) >= GLOBAL_FOLLOWUP_BUDGET) return false;           // 全局预算
        if (engine.totalQuestions(s) >= MAX_TOTAL_QUESTIONS) return false;                  // 硬顶守护

        // 校准路径：calibrated + confidence + 信念门（Python 回传 belief_allows_probe）
        if (a.calibratedSufficiency() != null) {
            return a.calibratedSufficiency() < CALIBRATED_STOP
                    && conf(a) >= CONFIDENCE_MIN
                    && !Boolean.FALSE.equals(a.beliefAllowsProbe());
        }
        // 原始/启发式路径
        return suff(a) < RAW_STOP && conf(a) >= CONFIDENCE_MIN;
    }

    private double suff(AnswerAssessmentDto a) { return a.sufficiency() == null ? 0 : a.sufficiency(); }
    private double conf(AnswerAssessmentDto a) { return a.confidence() == null ? 0 : a.confidence(); }
}
