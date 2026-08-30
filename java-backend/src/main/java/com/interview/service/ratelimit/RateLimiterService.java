package com.interview.service.ratelimit;

import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * Redis + Lua 令牌桶限流。Lua 保证「取令牌 + 回填 + 扣减」在 Redis 单线程里原子完成,
 * 避免并发下的 check-then-act 竞态。
 */
@Service
@RequiredArgsConstructor
public class RateLimiterService {

    // KEYS[1]=bucket; ARGV: capacity, refillPerSec, nowMs, requested
    private static final String LUA = """
        local key = KEYS[1]
        local capacity = tonumber(ARGV[1])
        local refill = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])
        local requested = tonumber(ARGV[4])
        local b = redis.call('HMGET', key, 'tokens', 'ts')
        local tokens = tonumber(b[1])
        local ts = tonumber(b[2])
        if tokens == nil then tokens = capacity; ts = now end
        local delta = math.max(0, now - ts) / 1000.0
        tokens = math.min(capacity, tokens + delta * refill)
        local allowed = 0
        if tokens >= requested then tokens = tokens - requested; allowed = 1 end
        redis.call('HSET', key, 'tokens', tokens, 'ts', now)
        redis.call('EXPIRE', key, math.ceil(capacity / refill) + 1)
        return allowed
        """;

    private final StringRedisTemplate redis;
    private final DefaultRedisScript<Long> script = new DefaultRedisScript<>(LUA, Long.class);

    /** 尝试取 1 个令牌;true=放行,false=超限。 */
    public boolean tryAcquire(String key, int capacity, double refillPerSec) {
        Long r = redis.execute(script, List.of("ratelimit:" + key),
                String.valueOf(capacity), String.valueOf(refillPerSec),
                String.valueOf(System.currentTimeMillis()), "1");
        return r != null && r == 1L;
    }
}
