package com.interview.service.orchestrator;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.interview.domain.InterviewPlan;
import com.interview.domain.json.QuestionDto;
import com.interview.exception.BizException;
import com.interview.mapper.InterviewPlanMapper;
import com.interview.service.ai.AiClient;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class PlanService {

    private final InterviewPlanMapper planMapper;
    private final AiClient ai;
    private final ObjectMapper om;

    /** 取候选人最近计划；没有则生成一份并落库（对应 Python loadOrCreate）。 */
    public InterviewPlan loadOrCreate(String candidateId, String jobId) {
        InterviewPlan p = planMapper.latestByCandidate(candidateId);
        if (p != null) return p;
        p = ai.plan(candidateId, jobId);
        planMapper.insert(p);
        return p;
    }

    /** 生成一份带内容 hash 的计划并落库（ingest 路径用；hash 供消费幂等去重）。 */
    public InterviewPlan createWithHash(String candidateId, String jobId, String contentHash) {
        InterviewPlan p = ai.plan(candidateId, jobId);
        p.setContentHash(contentHash);
        planMapper.insert(p);
        return p;
    }

    public InterviewPlan getById(String planId) {
        InterviewPlan p = planMapper.selectById(planId);
        if (p == null) throw new BizException("plan not found: " + planId);
        return p;
    }

    /** 把 rounds 拍平成有序主问题列表（含 lazy 占位）。 */
    public List<QuestionDto> flatMainQuestions(InterviewPlan plan) {
        List<QuestionDto> out = new ArrayList<>();
        if (plan.getRounds() == null) return out;
        for (Map<String, Object> round : plan.getRounds()) {
            Object qs = round.get("questions");
            if (qs == null) continue;
            List<QuestionDto> parsed = om.convertValue(qs, new TypeReference<List<QuestionDto>>() {});
            out.addAll(parsed);
        }
        return out;
    }
}
