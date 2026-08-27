package com.interview.service.orchestrator;

import com.interview.domain.EvaluationReport;
import com.interview.domain.InterviewPlan;
import com.interview.domain.InterviewSession;
import com.interview.domain.json.AnswerAssessmentDto;
import com.interview.domain.json.QuestionDto;
import com.interview.dto.resp.ReportResp;
import com.interview.dto.resp.TurnResp;
import com.interview.exception.OwnershipException;
import com.interview.service.ai.AiClient;
import com.interview.service.cache.SessionCache;
import com.interview.service.lock.SessionLockService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/**
 * 会话状态机（确定性编排）。三段式：start → submit*N → finalize，中途可 resume。
 * 所有对会话的写都在锁内「读-改-一次性写回」，保证不出半写脏态。
 */
@Service
@RequiredArgsConstructor
public class SessionService {

    private final SessionCache cache;
    private final SessionLockService lock;
    private final AiClient ai;
    private final PlanService planService;
    private final SessionEngine engine;
    private final FollowUpDecider followUp;
    private final CompletionPolicy completion;
    private final ArchiveService archive;

    // ---------- start ----------

    public TurnResp start(String candidateId, String jobId) {
        InterviewPlan plan = planService.loadOrCreate(candidateId, jobId);
        List<QuestionDto> mains = planService.flatMainQuestions(plan);

        InterviewSession s = new InterviewSession();
        s.setSessionId(UUID.randomUUID().toString());
        s.setPlanId(plan.getId());
        s.setJobId(plan.getJobId());
        s.setCandidateId(candidateId);
        s.setStatus("IN_PROGRESS");
        s.setHistory(new ArrayList<>());
        s.setAnswers(new ArrayList<>());
        s.setAssessments(new ArrayList<>());

        QuestionDto first = engine.nextMainQuestion(s, mains);   // 第一题（self_intro）
        engine.pushQuestion(s, first);

        cache.save(s);
        return TurnResp.of(s);
    }

    // ---------- submit ----------

    public TurnResp submit(String sessionId, String answer) {
        return lock.executeLocked(sessionId, () -> {
            InterviewSession s = cache.load(sessionId);
            if ("COMPLETED".equals(s.getStatus())) return TurnResp.of(s);

            InterviewPlan plan = planService.getById(s.getPlanId());
            List<QuestionDto> mains = planService.flatMainQuestions(plan);

            String currentPrompt = currentPromptText(s);
            engine.applyAnswer(s, answer);

            // 1) 单题评估（LLM 主 + 启发式兜底）
            AnswerAssessmentDto a = ai.assess(s, currentPrompt, answer);
            if (s.getAssessments() == null) s.setAssessments(new ArrayList<>());
            s.getAssessments().add(a);

            // 2) 追问 or 下一题 or 结束
            if (followUp.shouldFollowUp(s, a)) {
                String mainId = engine.currentMainId(s);
                int n = engine.followupCountForCurrent(s) + 1;
                QuestionDto probe = ai.followUp(s, mainId, n, a.followupGoal());
                engine.pushQuestion(s, probe);
            } else if (completion.isDone(s, plan, mains)) {
                s.setStatus("COMPLETED");
            } else {
                QuestionDto next = engine.nextMainQuestion(s, mains);
                if (next == null) s.setStatus("COMPLETED");
                else engine.pushQuestion(s, next);
            }

            cache.save(s);      // ★一次性写回
            return TurnResp.of(s);
        });
    }

    // ---------- resume（无副作用读取）----------

    public TurnResp resume(String sessionId) {
        InterviewSession s = cache.load(sessionId);
        return TurnResp.of(s);
    }

    // ---------- finalize ----------

    public ReportResp finalizeSession(String sessionId) {
        return lock.executeLocked(sessionId, () -> {
            InterviewSession s = cache.load(sessionId);
            EvaluationReport report = ai.evaluate(s);
            report.setSessionId(s.getSessionId());
            archive.archive(s, report);
            return ReportResp.of(report);
        });
    }

    // ---------- 候选人所有权校验（补软鉴权）----------

    /** 校验 token 里的 candidateId 与会话归属一致，否则越权。 */
    public void assertCandidate(String sessionId, String candidateId) {
        InterviewSession s = cache.load(sessionId);
        if (!candidateId.equals(s.getCandidateId())) throw new OwnershipException();
    }

    // ---------- helpers ----------

    private String currentPromptText(InterviewSession s) {
        for (int i = s.getHistory().size() - 1; i >= 0; i--) {
            var t = s.getHistory().get(i);
            if ("interviewer".equals(t.role()) && !Boolean.TRUE.equals(t.answered())) return t.text();
        }
        return "";
    }
}
