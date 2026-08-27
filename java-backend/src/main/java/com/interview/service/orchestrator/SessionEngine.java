package com.interview.service.orchestrator;

import com.interview.domain.InterviewPlan;
import com.interview.domain.InterviewSession;
import com.interview.domain.json.QuestionDto;
import com.interview.domain.json.TurnDto;
import com.interview.service.ai.AiClient;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 会话推进的纯派生/变更助手（不碰 Redis/DB）。
 * 「当前 stage」由「当前题所在 round」派生，不单独存字段（对应 Python Sprint 5.5 设计）。
 */
@Component
@RequiredArgsConstructor
public class SessionEngine {

    private final AiClient ai;

    /** 追问上限（按 stage）。 */
    public int maxFollowups(String stage) {
        return switch (stage == null ? "" : stage) {
            case "self_intro" -> 0;
            case "knowledge"  -> 1;
            case "project"    -> 2;
            case "scenario"   -> 2;
            default -> 1;
        };
    }

    /** 把候选人回答落进会话：标记最后一条提问已答 + 追加 candidate 轮 + 追加 answers。 */
    public void applyAnswer(InterviewSession s, String answer) {
        List<TurnDto> h = s.getHistory();
        for (int i = h.size() - 1; i >= 0; i--) {
            TurnDto t = h.get(i);
            if ("interviewer".equals(t.role()) && !Boolean.TRUE.equals(t.answered())) {
                h.set(i, new TurnDto(t.role(), t.text(), t.refId(), t.questionId(), true));
                // self_intro 的回答落进 intro_text 供后续 stage 用
                if (stageOf(t.questionId(), s).equals("self_intro")) s.setIntroText(answer);
                break;
            }
        }
        if (s.getAnswers() == null) s.setAnswers(new ArrayList<>());
        s.getAnswers().add(answer);
        h.add(new TurnDto("candidate", answer, null, currentMainId(s), true));
    }

    /** 追加一道面试官提问到 history。 */
    public void pushQuestion(InterviewSession s, QuestionDto q) {
        s.getHistory().add(new TurnDto("interviewer", q.text(), q.questionId(), q.questionId(), false));
    }

    /** 当前主问题 id（history 里最后一条 interviewer 轮，去掉 #f 后缀）。 */
    public String currentMainId(InterviewSession s) {
        for (int i = s.getHistory().size() - 1; i >= 0; i--) {
            TurnDto t = s.getHistory().get(i);
            if ("interviewer".equals(t.role())) return stripProbe(t.questionId());
        }
        return null;
    }

    public String currentStage(InterviewSession s) {
        return stageOf(currentMainId(s), s);
    }

    /** 当前主问题已追问次数。 */
    public int followupCountForCurrent(InterviewSession s) {
        String main = currentMainId(s);
        if (main == null) return 0;
        int n = 0;
        for (TurnDto t : s.getHistory()) {
            if ("interviewer".equals(t.role()) && t.questionId() != null
                    && t.questionId().startsWith(main + "#f")) n++;
        }
        return n;
    }

    /** 全局已用追问预算。 */
    public int usedFollowupBudget(InterviewSession s) {
        int n = 0;
        for (TurnDto t : s.getHistory()) {
            if ("interviewer".equals(t.role()) && t.questionId() != null && t.questionId().contains("#f")) n++;
        }
        return n;
    }

    /** 面试官提问总数（含追问），对应 max_total_questions 的计数口径。 */
    public int totalQuestions(InterviewSession s) {
        return (int) s.getHistory().stream().filter(t -> "interviewer".equals(t.role())).count();
    }

    /** 已问过的主问题 id 集合。 */
    public Set<String> askedMainIds(InterviewSession s) {
        Set<String> ids = new HashSet<>();
        for (TurnDto t : s.getHistory()) {
            if ("interviewer".equals(t.role())) ids.add(stripProbe(t.questionId()));
        }
        return ids;
    }

    /** 下一道未问的主问题（懒题现场解析），没有则 null。 */
    public QuestionDto nextMainQuestion(InterviewSession s, List<QuestionDto> mains) {
        Set<String> asked = askedMainIds(s);
        for (QuestionDto q : mains) {
            if (!asked.contains(q.questionId())) {
                return Boolean.TRUE.equals(q.lazy()) ? ai.resolveLazy(s, q) : q;
            }
        }
        return null;
    }

    /** 通过 questionId 在计划里查 stage；查不到按 self_intro 兜底。 */
    private String stageOf(String questionId, InterviewSession s) {
        // stage 编码在题 id 语义里较弱，这里用简单前缀约定：si*->self_intro, k*->knowledge, p*->project, sc*->scenario
        if (questionId == null) return "self_intro";
        String id = stripProbe(questionId);
        if (id.startsWith("si")) return "self_intro";
        if (id.startsWith("sc")) return "scenario";
        if (id.startsWith("k"))  return "knowledge";
        if (id.startsWith("p"))  return "project";
        return "knowledge";
    }

    private String stripProbe(String id) {
        if (id == null) return null;
        int i = id.indexOf('#');
        return i >= 0 ? id.substring(0, i) : id;
    }
}
