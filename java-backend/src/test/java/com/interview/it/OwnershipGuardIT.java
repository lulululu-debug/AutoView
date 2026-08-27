package com.interview.it;

import com.interview.domain.InterviewSession;
import com.interview.domain.Job;
import com.interview.domain.User;
import com.interview.exception.OwnershipException;
import com.interview.mapper.InterviewSessionMapper;
import com.interview.mapper.JobMapper;
import com.interview.mapper.UserMapper;
import com.interview.security.OwnershipGuard;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

import java.util.UUID;

import static org.assertj.core.api.Assertions.*;

/** 所有权链路 session→job→owner（真 Postgres 容器）。 */
class OwnershipGuardIT extends AbstractIntegrationTest {

    @Autowired OwnershipGuard guard;
    @Autowired UserMapper userMapper;
    @Autowired JobMapper jobMapper;
    @Autowired InterviewSessionMapper sessionMapper;

    @Test
    void ownerPasses_othersRejected_adminBypasses() {
        User u = new User();
        u.setUsername("own-" + UUID.randomUUID());
        u.setPasswordHash("x");
        u.setRole("hr");
        userMapper.insert(u);

        Job j = new Job();
        j.setId("job-" + UUID.randomUUID());
        j.setOwnerUserId(u.getId());
        jobMapper.insert(j);

        InterviewSession s = new InterviewSession();
        s.setSessionId("os-" + UUID.randomUUID());
        s.setStatus("COMPLETED");
        s.setJobId(j.getId());
        sessionMapper.insert(s);

        assertThatCode(() -> guard.assertOwnsSession(u.getId(), false, s.getSessionId()))
                .doesNotThrowAnyException();                                   // owner 通过
        assertThatThrownBy(() -> guard.assertOwnsSession("someone-else", false, s.getSessionId()))
                .isInstanceOf(OwnershipException.class);                       // 非 owner 拒绝
        assertThatCode(() -> guard.assertOwnsSession("someone-else", true, s.getSessionId()))
                .doesNotThrowAnyException();                                   // admin 放行
    }
}
