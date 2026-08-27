package com.interview.service.mq;

import java.io.Serializable;

/** 候选人 ingest 任务消息体。 */
public record IngestMsg(String candidateId, String jobId) implements Serializable {}
