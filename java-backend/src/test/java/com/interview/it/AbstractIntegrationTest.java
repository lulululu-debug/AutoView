package com.interview.it;

import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

/**
 * 集成测试基类。单例容器模式：Postgres + Redis 全 JVM 只起一次，所有 IT 子类共享，
 * Ryuk 在 JVM 退出时回收。datasource/redis 连接注入到测试上下文。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
public abstract class AbstractIntegrationTest {

    static final PostgreSQLContainer<?> PG;
    static final GenericContainer<?> REDIS;

    static {
        // ★ withUrlParam 正确拼出 ?stringtype=unspecified，让 MyBatis-Plus 写 jsonb 列成立
        PG = new PostgreSQLContainer<>(DockerImageName.parse("postgres:16-alpine"))
                .withUrlParam("stringtype", "unspecified");
        REDIS = new GenericContainer<>(DockerImageName.parse("redis:7-alpine")).withExposedPorts(6379);
        PG.start();
        REDIS.start();
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        r.add("spring.datasource.url", PG::getJdbcUrl);
        r.add("spring.datasource.username", PG::getUsername);
        r.add("spring.datasource.password", PG::getPassword);
        r.add("spring.data.redis.host", REDIS::getHost);
        r.add("spring.data.redis.port", () -> REDIS.getMappedPort(6379));
    }
}
