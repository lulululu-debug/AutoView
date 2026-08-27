package com.interview.domain;

import com.baomidou.mybatisplus.annotation.*;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import lombok.Data;

import java.time.Instant;
import java.util.List;
import java.util.Map;

@Data
@TableName(value = "interview_plans", autoResultMap = true)   // ★ autoResultMap 让 typeHandler 生效
public class InterviewPlan {
    @TableId(value = "id", type = IdType.ASSIGN_UUID)
    private String id;

    private String candidateId;
    private String jobId;
    private String contentHash;                   // 候选人+简历内容 hash：消费幂等键

    @TableField(value = "rounds", typeHandler = JacksonTypeHandler.class)
    private List<Map<String, Object>> rounds;

    @TableField(value = "competencies", typeHandler = JacksonTypeHandler.class)
    private List<Map<String, Object>> competencies;

    @TableField(value = "created_at", fill = FieldFill.INSERT)
    private Instant createdAt;
}
