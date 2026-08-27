package com.interview.unit;

import com.interview.domain.InterviewSession;
import com.interview.domain.json.QuestionDto;
import com.interview.service.orchestrator.CompletionPolicy;
import com.interview.service.orchestrator.SessionEngine;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class CompletionPolicyTest {

    private final SessionEngine engine = mock(SessionEngine.class);
    private final CompletionPolicy policy = new CompletionPolicy(engine);
    private final InterviewSession s = new InterviewSession();

    @Test
    void hardCapReached_done() {
        when(engine.totalQuestions(s)).thenReturn(15);
        assertThat(policy.isDone(s, null, List.of())).isTrue();
    }

    @Test
    void allMainsAsked_done() {
        when(engine.totalQuestions(s)).thenReturn(6);
        when(engine.nextMainQuestion(eq(s), any())).thenReturn(null);
        assertThat(policy.isDone(s, null, List.of())).isTrue();
    }

    @Test
    void moreMainsRemain_notDone() {
        when(engine.totalQuestions(s)).thenReturn(6);
        when(engine.nextMainQuestion(eq(s), any()))
                .thenReturn(new QuestionDto("k2", "t", "KNOWLEDGE", "comp:tech", "knowledge", false));
        assertThat(policy.isDone(s, null, List.of())).isFalse();
    }
}
