package com.interview.unit;

import com.interview.domain.InterviewSession;
import com.interview.domain.json.AnswerAssessmentDto;
import com.interview.service.orchestrator.FollowUpDecider;
import com.interview.service.orchestrator.SessionEngine;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

/** 追问决策纯规则单元测（Mock SessionEngine）。 */
class FollowUpDeciderTest {

    private SessionEngine engine;
    private FollowUpDecider decider;
    private final InterviewSession s = new InterviewSession();

    @BeforeEach
    void setup() {
        engine = mock(SessionEngine.class);
        decider = new FollowUpDecider(engine);
        // 默认：knowledge 阶段、还没追问、预算未用、题量不多
        when(engine.currentStage(s)).thenReturn("knowledge");
        when(engine.maxFollowups("knowledge")).thenReturn(1);
        when(engine.maxFollowups("self_intro")).thenReturn(0);
        when(engine.followupCountForCurrent(s)).thenReturn(0);
        when(engine.usedFollowupBudget(s)).thenReturn(0);
        when(engine.totalQuestions(s)).thenReturn(3);
    }

    @Test
    void selfIntroNeverFollowsUp() {
        when(engine.currentStage(s)).thenReturn("self_intro");
        when(engine.followupCountForCurrent(s)).thenReturn(0);
        assertThat(decider.shouldFollowUp(s, TestFixtures.assessment(0.1, 0.9, null, null))).isFalse();
    }

    @Test
    void rawPath_lowSufficiency_followsUp() {
        AnswerAssessmentDto a = TestFixtures.assessment(0.3, 0.6, null, null);
        assertThat(decider.shouldFollowUp(s, a)).isTrue();
    }

    @Test
    void rawPath_highSufficiency_stops() {
        AnswerAssessmentDto a = TestFixtures.assessment(0.9, 0.6, null, null);
        assertThat(decider.shouldFollowUp(s, a)).isFalse();
    }

    @Test
    void perQuestionCapReached_stops() {
        when(engine.followupCountForCurrent(s)).thenReturn(1);   // knowledge 上限 1
        assertThat(decider.shouldFollowUp(s, TestFixtures.assessment(0.1, 0.9, null, null))).isFalse();
    }

    @Test
    void globalBudgetExhausted_stops() {
        when(engine.usedFollowupBudget(s)).thenReturn(5);
        assertThat(decider.shouldFollowUp(s, TestFixtures.assessment(0.1, 0.9, null, null))).isFalse();
    }

    @Test
    void calibratedPath_belowThreshold_followsUp() {
        AnswerAssessmentDto a = TestFixtures.assessment(0.99, 0.6, 0.5, true);   // calibrated<0.9269
        assertThat(decider.shouldFollowUp(s, a)).isTrue();
    }

    @Test
    void calibratedPath_aboveThreshold_stops() {
        AnswerAssessmentDto a = TestFixtures.assessment(0.1, 0.6, 0.95, true);   // calibrated>0.9269
        assertThat(decider.shouldFollowUp(s, a)).isFalse();
    }

    @Test
    void calibratedPath_beliefGateClosed_stops() {
        AnswerAssessmentDto a = TestFixtures.assessment(0.1, 0.6, 0.5, false);   // belief 门关
        assertThat(decider.shouldFollowUp(s, a)).isFalse();
    }
}
