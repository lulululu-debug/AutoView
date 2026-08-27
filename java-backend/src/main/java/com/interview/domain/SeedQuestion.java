package com.interview.domain;

import com.baomidou.mybatisplus.annotation.*;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import lombok.Data;

import java.util.List;

@Data
@TableName(value = "seed_questions", autoResultMap = true)
public class SeedQuestion {
    @TableId(value = "id", type = IdType.ASSIGN_UUID)
    private String id;

    private String roleFamily;
    private String competency;
    private String category;                      // KNOWLEDGE / PROJECT_EXPERIENCE / SELF_INTRO / SCENARIO
    private String datasetId;
    private String text;

    @TableField(value = "rubric", typeHandler = JacksonTypeHandler.class)
    private List<String> rubric;                  // 3~6 条二元判分点

    private String difficulty;                    // easy / medium / hard
}
