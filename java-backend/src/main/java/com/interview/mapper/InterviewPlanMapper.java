package com.interview.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.interview.domain.InterviewPlan;

public interface InterviewPlanMapper extends BaseMapper<InterviewPlan> {

    /** 候选人最近一次计划（对应 Python load_latest_plan_for_candidate）。 */
    default InterviewPlan latestByCandidate(String candidateId) {
        return selectOne(Wrappers.<InterviewPlan>lambdaQuery()
                .eq(InterviewPlan::getCandidateId, candidateId)
                .orderByDesc(InterviewPlan::getCreatedAt)
                .last("limit 1"));
    }

    /** 内容级幂等：该候选人是否已有相同简历内容 hash 的计划（防重复消费/重跑重复解析）。 */
    default boolean existsByCandidateAndHash(String candidateId, String contentHash) {
        if (contentHash == null) return false;
        return selectCount(Wrappers.<InterviewPlan>lambdaQuery()
                .eq(InterviewPlan::getCandidateId, candidateId)
                .eq(InterviewPlan::getContentHash, contentHash)) > 0;
    }
}
