package com.interview.unit;

import com.interview.domain.InterviewSession;
import com.interview.domain.json.QuestionDto;
import com.interview.domain.json.TurnDto;
import com.interview.service.ai.AiClient;
import com.interview.service.orchestrator.SessionEngine;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

/** 会话推进派生/变更助手单元测。 */
class SessionEngineTest {

    private final AiClient ai = mock(AiClient.class);
    private final SessionEngine engine = new SessionEngine(ai);

    private InterviewSession sessionWith(TurnDto... turns) {
        InterviewSession s = new InterviewSession();
        s.setHistory(new ArrayList<>(List.of(turns)));
        return s;
    }

    private TurnDto interviewer(String qid) { return new TurnDto("interviewer", "Q " + qid, qid, qid, false); }

    @Test
    void maxFollowupsByStage() {
        assertThat(engine.maxFollowups("self_intro")).isZero();
        assertThat(engine.maxFollowups("knowledge")).isEqualTo(1);
        assertThat(engine.maxFollowups("project")).isEqualTo(2);
        assertThat(engine.maxFollowups("scenario")).isEqualTo(2);
    }

    @Test
    void applyAnswer_marksAnswered_andCapturesIntro() {
        InterviewSession s = sessionWith(interviewer("si1"));   // self_intro 题
        engine.applyAnswer(s, "我是后端工程师");

        assertThat(s.getHistory().get(0).answered()).isTrue();          // 提问标记已答
        assertThat(s.getAnswers()).containsExactly("我是后端工程师");    // 落 answers
        assertThat(s.getIntroText()).isEqualTo("我是后端工程师");        // self_intro 落 intro_text
        assertThat(s.getHistory()).hasSize(2);                          // 追加 candidate 轮
        assertThat(s.getHistory().get(1).role()).isEqualTo("candidate");
    }

    @Test
    void currentStage_derivedFromQuestionId() {
        assertThat(engine.currentStage(sessionWith(interviewer("k1")))).isEqualTo("knowledge");
        assertThat(engine.currentStage(sessionWith(interviewer("p1")))).isEqualTo("project");
        assertThat(engine.currentStage(sessionWith(interviewer("sc1")))).isEqualTo("scenario");
        assertThat(engine.currentStage(sessionWith(interviewer("si1")))).isEqualTo("self_intro");
    }

    @Test
    void followupCountForCurrent_countsProbesOfCurrentMain() {
        InterviewSession s = sessionWith(
                interviewer("k1"),
                new TurnDto("interviewer", "追问1", "k1#f1", "k1#f1", false));
        assertThat(engine.followupCountForCurrent(s)).isEqualTo(1);
        assertThat(engine.usedFollowupBudget(s)).isEqualTo(1);
    }

    @Test
    void nextMainQuestion_skipsAsked_returnsNonLazyDirectly() {
        InterviewSession s = sessionWith(interviewer("si1"));   // si1 已问
        List<QuestionDto> mains = List.of(
                new QuestionDto("si1", "自我介绍", "SELF_INTRO", "comp:comm", "self_intro", false),
                new QuestionDto("k1", "知识题", "KNOWLEDGE", "comp:tech", "knowledge", false));
        QuestionDto next = engine.nextMainQuestion(s, mains);
        assertThat(next.questionId()).isEqualTo("k1");
        verify(ai, never()).resolveLazy(any(), any());          // 非懒题不调 AI
    }

    @Test
    void nextMainQuestion_lazy_resolvesViaAi() {
        InterviewSession s = sessionWith();                     // 什么都没问
        QuestionDto lazy = new QuestionDto("p1", "", "PROJECT_EXPERIENCE", "comp:tech", "project", true);
        when(ai.resolveLazy(eq(s), eq(lazy)))
                .thenReturn(new QuestionDto("p1", "现场生成的项目题", "PROJECT_EXPERIENCE", "comp:tech", "project", false));

        QuestionDto next = engine.nextMainQuestion(s, List.of(lazy));
        assertThat(next.text()).isEqualTo("现场生成的项目题");
        verify(ai).resolveLazy(s, lazy);                        // 懒题现场解析
    }
}
