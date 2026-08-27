package com.interview.service.orchestrator;

import com.interview.domain.EvaluationReport;
import com.interview.domain.InterviewSession;
import com.interview.mapper.EvaluationReportMapper;
import com.interview.mapper.InterviewSessionMapper;
import com.interview.service.cache.SessionCache;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * finalize 时把会话 + 报告落库并清 Redis。
 * Session/Report 在一个事务里原子写；删 Redis 在事务成功后（失败方向偏安全：顶多 Redis 多留一会儿等 TTL）。
 */
@Service
@RequiredArgsConstructor
public class ArchiveService {

    private final InterviewSessionMapper sessionMapper;
    private final EvaluationReportMapper reportMapper;
    private final SessionCache cache;

    @Transactional
    public void persist(InterviewSession s, EvaluationReport r) {
        upsertSession(s);
        upsertReport(r);                 // session_id 唯一约束保证一场一份
    }

    /** 事务外删 Redis，避免归档回滚了却把热态删掉。 */
    public void archive(InterviewSession s, EvaluationReport r) {
        persist(s, r);
        cache.delete(s.getSessionId());
    }

    private void upsertSession(InterviewSession s) {
        if (sessionMapper.selectById(s.getSessionId()) == null) sessionMapper.insert(s);
        else sessionMapper.updateById(s);
    }

    private void upsertReport(EvaluationReport r) {
        if (reportMapper.selectById(r.getSessionId()) == null) reportMapper.insert(r);
        else reportMapper.updateById(r);
    }
}
