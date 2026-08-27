package com.interview.service.cache;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.interview.domain.InterviewSession;
import com.interview.exception.BizException;
import lombok.RequiredArgsConstructor;
import lombok.SneakyThrows;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;

/**
 * 进行中会话的热状态存取（Redis）。归档后即从 Redis 删除，Postgres 才是权威源。
 * save 是「整体一次性写回」，保证不出现半写脏态。
 */
@Service
@RequiredArgsConstructor
public class SessionCache {

    private final StringRedisTemplate redis;
    private final ObjectMapper om;                       // Spring 托管，已含 jsr310（Instant 可序列化）

    private static final Duration TTL = Duration.ofHours(24);   // 对应 SESSION_TTL_SECONDS

    @SneakyThrows
    public void save(InterviewSession s) {
        redis.opsForValue().set(key(s.getSessionId()), om.writeValueAsString(s), TTL);
    }

    @SneakyThrows
    public InterviewSession load(String sessionId) {
        String json = redis.opsForValue().get(key(sessionId));
        if (json == null) throw new BizException("session not found or expired: " + sessionId);
        return om.readValue(json, InterviewSession.class);
    }

    public boolean exists(String sessionId) {
        return Boolean.TRUE.equals(redis.hasKey(key(sessionId)));
    }

    public void delete(String sessionId) {
        redis.delete(key(sessionId));
    }

    private String key(String sessionId) {
        return "session:" + sessionId;
    }
}
