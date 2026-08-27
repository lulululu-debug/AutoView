package com.interview.domain;

import com.baomidou.mybatisplus.annotation.*;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import lombok.Data;

import java.time.Instant;
import java.util.List;
import java.util.Map;

@Data
@TableName(value = "evaluation_reports", autoResultMap = true)
public class EvaluationReport {
    /** session_id 既是主键也是唯一约束：一场面试一份报告（幂等保证）。 */
    @TableId(value = "session_id", type = IdType.INPUT)   // 由业务传入，不自动生成
    private String sessionId;

    private Double overall;
    private Boolean needsHumanReview;

    @TableField(value = "content_scores", typeHandler = JacksonTypeHandler.class)
    private List<Map<String, Object>> contentScores;

    @TableField(value = "performance_observations", typeHandler = JacksonTypeHandler.class)
    private List<Map<String, Object>> performanceObservations;

    @TableField(value = "competency_coverage", typeHandler = JacksonTypeHandler.class)
    private Map<String, Object> competencyCoverage;

    @TableField(value = "created_at", fill = FieldFill.INSERT)
    private Instant createdAt;
}
