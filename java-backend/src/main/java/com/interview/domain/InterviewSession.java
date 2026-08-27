package com.interview.domain;

import com.baomidou.mybatisplus.annotation.*;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import com.interview.domain.json.AnswerAssessmentDto;
import com.interview.domain.json.TurnDto;
import lombok.Data;

import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * 面试会话（进行中活在 Redis，结束归档到此表）。
 * S1 仅做表映射与 CRUD；状态机行为（appendAnswer / lastUnansweredPrompt 等）在 S3 加。
 *
 * ★ JSONB 泛型反序列化：MyBatis-Plus 的 JacksonTypeHandler 在 3.5.x 会通过“字段声明”
 *   解析出具体泛型（List<TurnDto> / List<AnswerAssessmentDto>），配合 autoResultMap=true
 *   即可正确还原，无需手写每字段一个 handler。这正是 §3.3 提到的泛型擦除坑的解法。
 */
@Data
@TableName(value = "interview_sessions", autoResultMap = true)
public class InterviewSession {
    @TableId(value = "session_id", type = IdType.ASSIGN_UUID)
    private String sessionId;

    private String planId;
    private String jobId;
    private String candidateId;

    private String status;                        // IN_PROGRESS / COMPLETED

    @TableField(value = "history", typeHandler = JacksonTypeHandler.class)
    private List<TurnDto> history;

    @TableField(value = "answers", typeHandler = JacksonTypeHandler.class)
    private List<String> answers;

    @TableField(value = "assessments", typeHandler = JacksonTypeHandler.class)
    private List<AnswerAssessmentDto> assessments;

    @TableField(value = "beliefs", typeHandler = JacksonTypeHandler.class)
    private Map<String, Object> beliefs;          // Python 回填，Java 只存取

    @TableField(value = "candidate_model", typeHandler = JacksonTypeHandler.class)
    private Map<String, Object> candidateModel;

    private String introText;

    @TableField(value = "created_at", fill = FieldFill.INSERT)
    private Instant createdAt;

    @TableField(value = "updated_at", fill = FieldFill.INSERT_UPDATE)
    private Instant updatedAt;
}
