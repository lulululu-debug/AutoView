package com.interview.service.mq;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.spring.core.RocketMQTemplate;
import org.apache.rocketmq.spring.support.RocketMQHeaders;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.messaging.support.MessageBuilder;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

/**
 * ingest 入队。队列可用则异步（RocketMQ），否则同步兜底（对应 Python BackgroundTasks 降级）。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class IngestProducer {

    public static final String TOPIC = "candidate-ingest";

    private final ObjectProvider<RocketMQTemplate> mqProvider;   // 未配 name-server 时不存在
    private final IngestService ingestService;

    @Value("${jobs.queue-enabled}")
    private boolean queueEnabled;
    @Value("${rocketmq.name-server:}")
    private String nameServer;

    public void submit(IngestMsg msg) {
        RocketMQTemplate mq = mqProvider.getIfAvailable();
        if (queueEnabled && mq != null && StringUtils.hasText(nameServer)) {
            mq.syncSend(TOPIC, MessageBuilder.withPayload(msg)
                    .setHeader(RocketMQHeaders.KEYS, msg.candidateId())   // 幂等去重 key
                    .build());
            log.info("ingest enqueued to RocketMQ: {}", msg.candidateId());
        } else {
            log.info("ingest queue disabled, run synchronously: {}", msg.candidateId());
            ingestService.process(msg);
        }
    }
}
