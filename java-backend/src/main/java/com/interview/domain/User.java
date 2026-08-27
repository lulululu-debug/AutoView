package com.interview.domain;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;
import java.time.Instant;

@Data
@TableName("users")
public class User {
    @TableId(value = "id", type = IdType.ASSIGN_UUID)
    private String id;

    private String username;
    private String passwordHash;
    private String role;                          // hr / admin

    @TableField(value = "created_at", fill = FieldFill.INSERT)
    private Instant createdAt;
}
