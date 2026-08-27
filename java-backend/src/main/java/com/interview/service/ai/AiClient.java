package com.interview.service.ai;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.interview.domain.InterviewPlan;
import com.interview.domain.InterviewSession;
import com.interview.domain.json.AnswerAssessmentDto;
import com.interview.domain.json.QuestionDto;
import com.interview.domain.EvaluationReport;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.*;

/**
 * 调 Python AI 服务的唯一出口 + 本地兜底（双路径容错）。
 *
 * ai.enabled=false（默认）：全部走 Java 本地 stub，可脱离 Python 端到端跑完面试；
 * ai.enabled=true：真正调用 Python 的 /ai/* 端点，assess 失败/超时仍退回本地启发式。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class AiClient {

    private final WebClient aiWebClient;
    private final ObjectMapper om;

    @Value("${ai.enabled}")
    private boolean enabled;
    @Value("${ai.assess-timeout-ms}")
    private long assessTimeoutMs;

    // ---------- Planner ----------

    /** 生成计划（stub：固定骨架 self_intro→knowledge×2→project×2(lazy)→scenario）。 */
    public InterviewPlan plan(String candidateId, String jobId) {
        if (enabled) {
            try {
                InterviewPlan p = aiWebClient.post().uri("/ai/plan")
                        .bodyValue(Map.of("candidateId", candidateId, "jobId", jobId))
                        .retrieve().bodyToMono(InterviewPlan.class)
                        .block(Duration.ofSeconds(30));
                if (p != null) return p;
            } catch (Exception e) {
                log.warn("ai.plan failed, fallback to stub: {}", e.getMessage());
            }
        }
        InterviewPlan p = new InterviewPlan();
        p.setCandidateId(candidateId);
        p.setJobId(jobId);
        p.setRounds(List.of(
                round("self_intro", q("si1", "先做个自我介绍吧", "SELF_INTRO", "comp:comm", "self_intro", false)),
                round("knowledge",
                        q("k1", "谈谈你对分布式锁的理解，以及 Redis 实现的关键点", "KNOWLEDGE", "comp:tech", "knowledge", false),
                        q("k2", "数据库事务的隔离级别有哪些？各解决什么问题", "KNOWLEDGE", "comp:tech", "knowledge", false)),
                round("project",
                        q("p1", "", "PROJECT_EXPERIENCE", "comp:tech", "project", true),
                        q("p2", "", "PROJECT_EXPERIENCE", "comp:tech", "project", true)),
                round("scenario",
                        q("sc1", "如果线上接口突然 P99 飙高，你会怎么排查", "SCENARIO", "comp:comm", "scenario", false))
        ));
        p.setCompetencies(List.of(
                m("id", "comp:tech", "name", "技术能力", "weight", 2.0, "mandatory", true),
                m("id", "comp:comm", "name", "沟通表达", "weight", 1.0, "mandatory", false)
        ));
        return p;
    }

    /** 现场解析懒题（project 阶段），stub 用 intro_text 拼一道项目深挖题。 */
    public QuestionDto resolveLazy(InterviewSession s, QuestionDto lazy) {
        if (enabled) {
            try {
                QuestionDto r = aiWebClient.post().uri("/ai/next-question")
                        .bodyValue(Map.of("introText", Optional.ofNullable(s.getIntroText()).orElse(""),
                                "goal", "project deep-dive", "questionId", lazy.questionId()))
                        .retrieve().bodyToMono(QuestionDto.class)
                        .block(Duration.ofSeconds(20));
                if (r != null) return r;
            } catch (Exception e) {
                log.warn("ai.resolveLazy failed, fallback: {}", e.getMessage());
            }
        }
        String text = "结合你刚才的自我介绍，挑一个你最深入的项目，讲讲其中最难的技术决策和你的取舍。";
        return new QuestionDto(lazy.questionId(), text, lazy.category(), lazy.competencyId(), lazy.stage(), false);
    }

    // ---------- Assessor（双路径：LLM 主 + 启发式兜底）----------

    public AnswerAssessmentDto assess(InterviewSession s, String question, String answer) {
        // 反作弊短路：与历史回答字节相同 → sufficiency=0
        if (s.getAnswers() != null && answer.length() >= 20
                && s.getAnswers().stream().anyMatch(a -> a.equals(answer) && a != answer)) {
            return new AnswerAssessmentDto(0.0, 1.0, null, List.of(), List.of(),
                    List.of("duplicate answer"), null, "duplicate", "rule_duplicate", false, List.of());
        }
        if (enabled) {
            try {
                @SuppressWarnings("unchecked")
                Map<String, Object> resp = aiWebClient.post().uri("/ai/assess")
                        .bodyValue(Map.of("question", question, "answer", answer,
                                "sessionId", s.getSessionId()))
                        .retrieve().bodyToMono(Map.class)
                        .block(Duration.ofMillis(assessTimeoutMs));
                if (resp != null) return om.convertValue(resp, AnswerAssessmentDto.class);
            } catch (Exception e) {
                log.warn("ai.assess failed/timeout, fallback to heuristic: {}", e.getMessage());
            }
        }
        return heuristic(answer);
    }

    /** 启发式兜底：字数 + 具体性关键词；confidence 固定 0.3 表示降级估计。 */
    private AnswerAssessmentDto heuristic(String answer) {
        int len = answer == null ? 0 : answer.length();
        boolean specific = answer != null && answer.matches("(?s).*(例如|比如|%|具体|首先|其次).*");
        double base = Math.min(1.0, len / 120.0);
        double suff = Math.max(0.0, Math.min(1.0, base * (specific ? 1.0 : 0.6)));
        return new AnswerAssessmentDto(suff, 0.3, null, List.of("depth"), List.of(),
                List.of(), "补充更具体的细节", "heuristic", "heuristic", true, List.of());
    }

    /** 生成一道追问题。 */
    public QuestionDto followUp(InterviewSession s, String parentQuestionId, int n, String goal) {
        String text = "能否就「" + Optional.ofNullable(goal).orElse("刚才这点") + "」再具体展开一下？"
                + "比如给出一个你实际做过的例子或具体数字。";
        if (enabled) {
            try {
                QuestionDto r = aiWebClient.post().uri("/ai/next-question")
                        .bodyValue(Map.of("goal", Optional.ofNullable(goal).orElse(""),
                                "parent", parentQuestionId, "followup", true))
                        .retrieve().bodyToMono(QuestionDto.class)
                        .block(Duration.ofSeconds(15));
                if (r != null) text = r.text();
            } catch (Exception e) {
                log.warn("ai.followUp failed, fallback: {}", e.getMessage());
            }
        }
        return new QuestionDto(parentQuestionId + "#f" + n, text, "FOLLOWUP", null, null, false);
    }

    // ---------- Evaluator ----------

    /** 全场综合（stub：各维度取已评估题最大 sufficiency 均值 ×100，按权重加权）。 */
    public EvaluationReport evaluate(InterviewSession s) {
        if (enabled) {
            try {
                EvaluationReport r = aiWebClient.post().uri("/ai/evaluate")
                        .bodyValue(Map.of("sessionId", s.getSessionId(),
                                "assessments", Optional.ofNullable(s.getAssessments()).orElse(List.of())))
                        .retrieve().bodyToMono(EvaluationReport.class)
                        .block(Duration.ofSeconds(60));
                if (r != null) { r.setSessionId(s.getSessionId()); return r; }
            } catch (Exception e) {
                log.warn("ai.evaluate failed, fallback to formula: {}", e.getMessage());
            }
        }
        double avg = 0.0;
        if (s.getAssessments() != null && !s.getAssessments().isEmpty()) {
            avg = s.getAssessments().stream()
                    .mapToDouble(a -> a.sufficiency() == null ? 0.0 : a.sufficiency())
                    .average().orElse(0.0);
        }
        EvaluationReport r = new EvaluationReport();
        r.setSessionId(s.getSessionId());
        r.setOverall(Math.round(avg * 100.0 * 10) / 10.0);
        r.setNeedsHumanReview(true);                        // 硬编码 true：AI 只初筛，终审留给 HR
        r.setContentScores(List.of(m("competencyId", "comp:tech", "score", r.getOverall())));
        r.setPerformanceObservations(List.of());
        r.setCompetencyCoverage(m("assessed", s.getAssessments() == null ? 0 : s.getAssessments().size()));
        return r;
    }

    // ---------- helpers ----------

    @SuppressWarnings("unchecked")
    private Map<String, Object> round(String stage, QuestionDto... qs) {
        List<Map<String, Object>> questions = new ArrayList<>();
        for (QuestionDto q : qs) questions.add(om.convertValue(q, Map.class));
        return m("stage", stage, "questions", questions);
    }

    private QuestionDto q(String id, String text, String cat, String comp, String stage, boolean lazy) {
        return new QuestionDto(id, text, cat, comp, stage, lazy);
    }

    /** 构造 Map<String,Object>，避免 Map.of 异构值类型推断问题。 */
    private Map<String, Object> m(Object... kv) {
        Map<String, Object> map = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) map.put((String) kv[i], kv[i + 1]);
        return map;
    }
}
