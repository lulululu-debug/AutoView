package com.interview.service.mq;

import com.interview.domain.Candidate;
import com.interview.mapper.CandidateMapper;
import com.interview.mapper.InterviewPlanMapper;
import com.interview.service.orchestrator.PlanService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

/**
 * 候选人处理的实际逻辑（简历解析占位 + 计划生成）。
 * 既可被 RocketMQ 消费者调用（异步），也可被生产者直接调用（同步兜底）。
 *
 * 消费幂等：以「候选人 + 简历内容 hash」为幂等键——同一条 ingest 消息被重复投递/重试时，
 * 第二次发现相同 hash 的计划已存在即跳过，避免重复解析浪费 LLM/处理资源。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class IngestService {

    private final CandidateMapper candidateMapper;
    private final InterviewPlanMapper planMapper;
    private final PlanService planService;

    public void process(IngestMsg msg) {
        Candidate c = candidateMapper.selectById(msg.candidateId());
        if (c == null) {
            log.warn("ingest: candidate not found {}", msg.candidateId());
            return;
        }
        String hash = c.getContentHash();
        if (planMapper.existsByCandidateAndHash(c.getId(), hash)) {   // 内容级幂等
            log.info("ingest: same resume already processed (candidate={}, hash={}), skip", c.getId(), hash);
            return;
        }
        // TODO(S 后续): 简历分段 + 图片 OCR + Milvus 写入；当前 stub 直接生成计划
        planService.createWithHash(c.getId(), msg.jobId(), hash);
        log.info("ingest: plan generated for {} (hash={})", c.getId(), hash);
    }
}
