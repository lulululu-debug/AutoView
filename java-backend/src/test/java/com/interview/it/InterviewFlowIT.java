package com.interview.it;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.interview.domain.Job;
import com.interview.mapper.JobMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.http.*;
import org.springframework.http.client.SimpleClientHttpRequestFactory;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

/** 端到端全流程 + 安全 + 并发锁（真 HTTP + PG + Redis）。 */
class InterviewFlowIT extends AbstractIntegrationTest {

    private static final String ANS =
            "首先我负责后端核心链路，用 Redis SETNX 加 Lua 实现分布式锁，TTL 防死锁；"
            + "其次用 RocketMQ 把简历解析等长耗时任务异步化。举个具体例子，把 P99 从 900ms 降到 400ms。";

    @LocalServerPort int port;
    @Autowired ObjectMapper om;
    @Autowired JobMapper jobMapper;

    private TestRestTemplate rest;

    /**
     * 用 JDK SimpleClientHttpRequestFactory 构造 —— 它没有 cookie store，
     * 因此 /auth/login 下发的 HR token cookie 不会串到后续候选人请求上
     * （否则 filter「cookie 优先」会把候选人请求当成 HR，导致 403）。
     */
    @BeforeEach
    void initClient() {
        rest = new TestRestTemplate(new RestTemplateBuilder()
                .rootUri("http://localhost:" + port)
                .requestFactory(SimpleClientHttpRequestFactory.class));
    }

    // ---------- helpers ----------

    private ResponseEntity<String> call(HttpMethod m, String path, Object body, String token) {
        HttpHeaders h = new HttpHeaders();
        h.setContentType(MediaType.APPLICATION_JSON);
        h.setAccept(java.util.List.of(MediaType.APPLICATION_JSON));  // 强制 JSON（否则 JDK 客户端默认 Accept 会协商出 XML）
        if (token != null) h.setBearerAuth(token);
        return rest.exchange(path, m, new HttpEntity<>(body, h), String.class);
    }

    private JsonNode data(ResponseEntity<String> r) throws Exception {
        return om.readTree(r.getBody()).get("data");
    }

    private String hrToken(String username) throws Exception {
        call(HttpMethod.POST, "/auth/register", Map.of("username", username, "password", "pw123456"), null);
        var r = call(HttpMethod.POST, "/auth/login", Map.of("username", username, "password", "pw123456"), null);
        return data(r).get("token").asText();
    }

    private String candidateToken(String candidateId) throws Exception {
        var r = call(HttpMethod.POST, "/auth/candidate-token?candidateId=" + candidateId, null, null);
        return data(r).get("token").asText();
    }

    private String startInterview(String candToken, String jobId) throws Exception {
        String path = "/interview/start" + (jobId == null ? "" : "?jobId=" + jobId);
        var r = call(HttpMethod.POST, path, null, candToken);
        assertThat(r.getStatusCode().value()).isEqualTo(200);
        return data(r).get("sessionId").asText();
    }

    private void runToCompletion(String sid, String candToken) throws Exception {
        boolean done = false;
        for (int i = 0; i < 15 && !done; i++) {
            var r = call(HttpMethod.POST, "/interview/" + sid + "/answer", Map.of("answer", ANS), candToken);
            assertThat(r.getStatusCode().value()).isEqualTo(200);
            done = data(r).get("done").asBoolean();
        }
        assertThat(done).as("面试应在 ≤15 轮内结束").isTrue();
    }

    // ---------- tests ----------

    @Test
    void fullFlow_startToReport() throws Exception {
        String cid = "cand-" + UUID.randomUUID();
        String ct = candidateToken(cid);
        String sid = startInterview(ct, null);
        runToCompletion(sid, ct);

        var fin = call(HttpMethod.POST, "/interview/" + sid + "/finalize", null, ct);
        assertThat(fin.getStatusCode().value()).isEqualTo(200);
        assertThat(data(fin).get("needsHumanReview").asBoolean()).isTrue();
        assertThat(data(fin).get("overall").isNumber()).isTrue();
    }

    @Test
    void security_noToken_403() {
        var r = call(HttpMethod.POST, "/interview/start", null, null);
        assertThat(r.getStatusCode().value()).isEqualTo(403);
    }

    @Test
    void security_wrongCandidate_404() throws Exception {
        String ctA = candidateToken("cand-" + UUID.randomUUID());
        String ctB = candidateToken("cand-" + UUID.randomUUID());
        String sid = startInterview(ctA, null);
        // B 用自己的 token 访问 A 的 session → 越权 404
        var r = call(HttpMethod.POST, "/interview/" + sid + "/answer", Map.of("answer", ANS), ctB);
        assertThat(r.getStatusCode().value()).isEqualTo(404);
    }

    @Test
    void validation_blankAnswer_400() throws Exception {
        String ct = candidateToken("cand-" + UUID.randomUUID());
        String sid = startInterview(ct, null);
        var r = call(HttpMethod.POST, "/interview/" + sid + "/answer", Map.of("answer", ""), ct);
        assertThat(r.getStatusCode().value()).isEqualTo(400);
    }

    @Test
    void hrOwnership_ownerReadsReport_othersGet404() throws Exception {
        // alice 建号 + 名下 job
        String aliceUser = "alice-" + UUID.randomUUID();
        var reg = call(HttpMethod.POST, "/auth/register", Map.of("username", aliceUser, "password", "pw123456"), null);
        String aliceId = data(reg).get("userId").asText();
        String aliceToken = loginToken(aliceUser);

        String jid = "job-" + UUID.randomUUID();
        Job job = new Job();
        job.setId(jid);
        job.setOwnerUserId(aliceId);
        jobMapper.insert(job);

        // 候选人在该 job 下跑完面试
        String ct = candidateToken("cand-" + UUID.randomUUID());
        String sid = startInterview(ct, jid);
        runToCompletion(sid, ct);
        call(HttpMethod.POST, "/interview/" + sid + "/finalize", null, ct);

        // owner 读报告 200
        var ok = call(HttpMethod.GET, "/hr/reports/" + sid, null, aliceToken);
        assertThat(ok.getStatusCode().value()).isEqualTo(200);

        // 另一个 HR 读同一报告 404
        String malUser = "mallory-" + UUID.randomUUID();
        String malToken = hrToken(malUser);
        var no = call(HttpMethod.GET, "/hr/reports/" + sid, null, malToken);
        assertThat(no.getStatusCode().value()).isEqualTo(404);
    }

    private String loginToken(String username) throws Exception {
        var r = call(HttpMethod.POST, "/auth/login", Map.of("username", username, "password", "pw123456"), null);
        return data(r).get("token").asText();
    }

    @Test
    void concurrency_perSessionLock_rejectsWithBusy() throws Exception {
        String ct = candidateToken("cand-" + UUID.randomUUID());
        String sid = startInterview(ct, null);

        int n = 8;
        ExecutorService ex = Executors.newFixedThreadPool(n);
        CountDownLatch start = new CountDownLatch(1);
        AtomicInteger ok = new AtomicInteger(), busy = new AtomicInteger();
        CountDownLatch doneAll = new CountDownLatch(n);

        for (int i = 0; i < n; i++) {
            ex.submit(() -> {
                try {
                    start.await();
                    var r = call(HttpMethod.POST, "/interview/" + sid + "/answer", Map.of("answer", ANS), ct);
                    int code = r.getStatusCode().value();
                    if (code == 200) ok.incrementAndGet();
                    else if (code == 409) busy.incrementAndGet();
                } catch (Exception ignored) {
                } finally {
                    doneAll.countDown();
                }
            });
        }
        start.countDown();              // 同时释放，制造锁竞争
        doneAll.await(30, TimeUnit.SECONDS);
        ex.shutdown();

        assertThat(ok.get()).as("至少一个成功").isGreaterThanOrEqualTo(1);
        assertThat(busy.get()).as("锁生效：至少一个 409").isGreaterThanOrEqualTo(1);
        assertThat(ok.get() + busy.get()).isEqualTo(n);
    }
}
