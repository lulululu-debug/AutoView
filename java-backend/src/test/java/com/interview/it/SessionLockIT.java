package com.interview.it;

import com.interview.exception.SessionBusyException;
import com.interview.service.lock.SessionLockService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

import java.util.UUID;
import java.util.concurrent.*;

import static org.assertj.core.api.Assertions.*;

/** Redisson 分布式锁并发语义（真 Redis 容器）。 */
class SessionLockIT extends AbstractIntegrationTest {

    @Autowired
    SessionLockService lock;

    @Test
    void concurrentAcquire_secondGetsBusy_thenReusable() throws Exception {
        String sid = "lock-" + UUID.randomUUID();
        CountDownLatch acquired = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        ExecutorService ex = Executors.newSingleThreadExecutor();

        Future<?> holder = ex.submit(() -> lock.executeLocked(sid, () -> {
            acquired.countDown();
            try { release.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            return null;
        }));

        acquired.await();   // 确保 holder 已持锁

        // 第二个请求拿不到锁 → SessionBusy
        assertThatThrownBy(() -> lock.executeLocked(sid, () -> "x"))
                .isInstanceOf(SessionBusyException.class);

        release.countDown();
        holder.get();

        // 释放后可再次获取
        assertThat(lock.executeLocked(sid, () -> "ok")).isEqualTo("ok");
        ex.shutdown();
    }
}
