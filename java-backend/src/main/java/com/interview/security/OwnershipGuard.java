package com.interview.security;

import com.interview.domain.InterviewSession;
import com.interview.domain.Job;
import com.interview.exception.OwnershipException;
import com.interview.mapper.InterviewSessionMapper;
import com.interview.mapper.JobMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

/**
 * HR 所有权链路校验：report/session → job.owner_user_id。
 * 非 owner（且非 admin）一律抛 OwnershipException → 映射 404（防枚举）。
 */
@Component
@RequiredArgsConstructor
public class OwnershipGuard {

    private final InterviewSessionMapper sessionMapper;
    private final JobMapper jobMapper;

    public void assertOwnsSession(String userId, boolean admin, String sessionId) {
        if (admin) return;
        InterviewSession s = sessionMapper.selectById(sessionId);
        if (s == null) throw new OwnershipException();
        Job job = jobMapper.selectById(s.getJobId());
        if (job == null || !userId.equals(job.getOwnerUserId())) throw new OwnershipException();
    }
}
