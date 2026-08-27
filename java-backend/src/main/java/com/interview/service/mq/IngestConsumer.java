package com.interview.service.mq;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
import org.apache.rocketmq.spring.core.RocketMQListener;
import org.springframework.stereotype.Service;

/**
 * ingest 消费者。仅当 rocketmq.name-server 配置时才被 RocketMQ 自动装配启动。
 * 抛异常 → RocketMQ 按 maxReconsumeTimes 重试；耗尽进死信队列 %DLQ%ingest-group。
 */
@Slf4j
@Service
@RequiredArgsConstructor
@RocketMQMessageListener(
        topic = IngestProducer.TOPIC,
        consumerGroup = "ingest-group",
        maxReconsumeTimes = 3)
public class IngestConsumer implements RocketMQListener<IngestMsg> {

    private final IngestService ingestService;

    @Override
    public void onMessage(IngestMsg msg) {
        ingestService.process(msg);   // 失败抛出 → 触发重试
    }
}
