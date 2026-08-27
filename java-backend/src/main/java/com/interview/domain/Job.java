package com.interview.domain;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;
import java.time.Instant;

@Data
@TableName("jobs")
public class Job {
    @TableId(value = "id", type = IdType.ASSIGN_UUID)
    private String id;

    private String ownerUserId;                   // 所有权锚点
    private String title;
    private String jd;
    private String roleFamily;
    private String track;                         // campus / lateral

    @TableField(value = "created_at", fill = FieldFill.INSERT)
    private Instant createdAt;
}
