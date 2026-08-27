package com.interview.service.lock;

import com.interview.exception.SessionBusyException;
import lombok.RequiredArgsConstructor;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Service;

import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

/**
 * per-session 分布式锁（Redisson RLock）。
 *
 * 底层同样是 SET NX EX + Lua 校验持有者释放，额外提供：
 *  - 看门狗自动续期：leaseTime 传 -1，业务没跑完锁不会过期
 *    —— 直接补掉裸 Lua 锁「30s TTL 提前过期重开并发窗口」的短板；
 *  - 可重入 + 释放时校验持有线程，天然防误删他人锁。
 */
@Service
@RequiredArgsConstructor
public class SessionLockService {

    private final RedissonClient redisson;

    /** 拿锁执行；拿不到立即抛 SessionBusy（映射 409）。 */
    public <T> T executeLocked(String sessionId, Supplier<T> action) {
        RLock lock = redisson.getLock(key(sessionId));
        boolean ok;
        try {
            // waitTime=0 不排队；leaseTime=-1 启用看门狗续期
            ok = lock.tryLock(0, -1, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new SessionBusyException();
        }
        if (!ok) throw new SessionBusyException();
        try {
            return action.get();
        } finally {
            if (lock.isHeldByCurrentThread()) {
                lock.unlock();
            }
        }
    }

    private String key(String sessionId) {
        return "lock:session:" + sessionId;
    }
}
