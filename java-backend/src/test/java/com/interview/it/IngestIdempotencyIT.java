package com.interview.it;

import com.interview.domain.Candidate;
import com.interview.mapper.CandidateMapper;
import com.interview.mapper.InterviewPlanMapper;
import com.interview.service.mq.IngestMsg;
import com.interview.service.mq.IngestService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/** 消费幂等：同一 ingest 消息重复处理（重试/重复投递）只生成一份计划（真 PG 容器）。 */
class IngestIdempotencyIT extends AbstractIntegrationTest {

    @Autowired IngestService ingestService;
    @Autowired CandidateMapper candidateMapper;
    @Autowired InterviewPlanMapper planMapper;

    @Test
    void sameMessageProcessedTwice_yieldsOnePlan() {
        Candidate c = new Candidate();
        c.setJobId("job-" + UUID.randomUUID());
        c.setResumeText("后端工程师，做过分布式锁与异步化。");
        c.setContentHash("hash-" + UUID.randomUUID());   // 固定 hash 模拟同一份简历
        candidateMapper.insert(c);

        IngestMsg msg = new IngestMsg(c.getId(), c.getJobId());
        ingestService.process(msg);   // 首次：生成计划
        ingestService.process(msg);   // 重复投递：应命中内容 hash 幂等，跳过
        ingestService.process(msg);   // 再一次：仍跳过

        long plans = planMapper.selectCount(
                com.baomidou.mybatisplus.core.toolkit.Wrappers.<com.interview.domain.InterviewPlan>lambdaQuery()
                        .eq(com.interview.domain.InterviewPlan::getCandidateId, c.getId()));
        assertThat(plans).as("同一简历重复 ingest 只应有一份计划").isEqualTo(1);
    }
}
