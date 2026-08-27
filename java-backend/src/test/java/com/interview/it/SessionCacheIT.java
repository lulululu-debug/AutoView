package com.interview.it;

import com.interview.domain.InterviewSession;
import com.interview.domain.json.TurnDto;
import com.interview.exception.BizException;
import com.interview.service.cache.SessionCache;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.*;

/** Redis 会话热态 save/load/delete 往返（真 Redis 容器）。 */
class SessionCacheIT extends AbstractIntegrationTest {

    @Autowired
    SessionCache cache;

    @Test
    void roundTrip_andDelete() {
        InterviewSession s = new InterviewSession();
        s.setSessionId("sc-" + UUID.randomUUID());
        s.setStatus("IN_PROGRESS");
        s.setHistory(new ArrayList<>(List.of(new TurnDto("interviewer", "hi", "q1", "q1", false))));

        cache.save(s);
        InterviewSession loaded = cache.load(s.getSessionId());
        assertThat(loaded.getStatus()).isEqualTo("IN_PROGRESS");
        assertThat(loaded.getHistory()).hasSize(1);
        assertThat(loaded.getHistory().get(0).text()).isEqualTo("hi");

        cache.delete(s.getSessionId());
        assertThatThrownBy(() -> cache.load(s.getSessionId())).isInstanceOf(BizException.class);
    }
}
