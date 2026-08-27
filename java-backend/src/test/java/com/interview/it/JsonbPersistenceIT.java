package com.interview.it;

import com.interview.domain.EvaluationReport;
import com.interview.domain.InterviewSession;
import com.interview.domain.json.AnswerAssessmentDto;
import com.interview.domain.json.TurnDto;
import com.interview.mapper.EvaluationReportMapper;
import com.interview.mapper.InterviewSessionMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.*;

/** JSONB 泛型往返 + 报告唯一约束（真 Postgres 容器）。 */
class JsonbPersistenceIT extends AbstractIntegrationTest {

    @Autowired InterviewSessionMapper sessionMapper;
    @Autowired EvaluationReportMapper reportMapper;

    @Test
    void jsonbGenericRoundTrip() {
        InterviewSession s = new InterviewSession();
        s.setSessionId("js-" + UUID.randomUUID());
        s.setStatus("IN_PROGRESS");
        s.setHistory(new ArrayList<>(List.of(new TurnDto("interviewer", "Q1", "q1", "q1", true))));
        s.setAssessments(new ArrayList<>(List.of(new AnswerAssessmentDto(
                0.8, 0.9, 0.92, List.of("depth"), List.of(), List.of(),
                "goal", "ok", "llm", true, List.of(true, false)))));
        sessionMapper.insert(s);

        InterviewSession loaded = sessionMapper.selectById(s.getSessionId());
        // ★ 泛型 List<TurnDto> / List<AnswerAssessmentDto> 完整还原
        assertThat(loaded.getHistory().get(0).text()).isEqualTo("Q1");
        assertThat(loaded.getHistory().get(0).answered()).isTrue();
        assertThat(loaded.getAssessments().get(0).sufficiency()).isEqualTo(0.8);
        assertThat(loaded.getAssessments().get(0).rubricHits()).containsExactly(true, false);
    }

    @Test
    void reportUniqueConstraint_upsertKeepsOneRow() {
        String sid = "rep-" + UUID.randomUUID();
        EvaluationReport r = new EvaluationReport();
        r.setSessionId(sid);
        r.setOverall(80.0);
        r.setNeedsHumanReview(true);
        reportMapper.insert(r);

        r.setOverall(90.0);
        reportMapper.updateById(r);                 // upsert：一场一份
        assertThat(reportMapper.selectById(sid).getOverall()).isEqualTo(90.0);

        // 主键唯一：重复 insert 同 session_id 抛异常
        EvaluationReport dup = new EvaluationReport();
        dup.setSessionId(sid);
        dup.setOverall(1.0);
        dup.setNeedsHumanReview(true);
        assertThatThrownBy(() -> reportMapper.insert(dup)).isInstanceOf(Exception.class);
    }
}
