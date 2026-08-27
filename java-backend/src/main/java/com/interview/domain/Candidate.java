package com.interview.domain;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;
import java.time.Instant;

@Data
@TableName("candidates")
public class Candidate {
    @TableId(value = "id", type = IdType.ASSIGN_UUID)
    private String id;

    private String jobId;
    private String name;
    private String resumeText;
    private String contentHash;                   // 幂等键

    @TableField(value = "created_at", fill = FieldFill.INSERT)
    private Instant createdAt;
}
