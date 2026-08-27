package com.interview.controller;

import com.interview.domain.InterviewSession;
import com.interview.domain.json.TurnDto;
import com.interview.dto.resp.ApiResult;
import com.interview.mapper.InterviewSessionMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * S1 冒烟：验证「实体 + MyBatis-Plus CRUD + JSONB(history) 往返」是否打通。
 *
 * 用法：
 *   curl -X POST localhost:8080/smoke/session          # 建一条带 history(jsonb) 的会话，返回 sessionId
 *   curl localhost:8080/smoke/session/{sessionId}       # 读回，history 应完整还原成 TurnDto 列表
 *
 * 这个控制器只为验证骨架，S3 会被真正的 InterviewController 取代。
 */
@RestController
@RequestMapping("/smoke")
@RequiredArgsConstructor
public class SmokeController {

    private final InterviewSessionMapper sessionMapper;

    @PostMapping("/session")
    public ApiResult<InterviewSession> create() {
        InterviewSession s = new InterviewSession();
        s.setPlanId("plan-demo");
        s.setJobId("job-demo");
        s.setCandidateId("cand-demo");
        s.setStatus("IN_PROGRESS");
        s.setIntroText("我是一名后端工程师……");
        s.setHistory(List.of(
                new TurnDto("interviewer", "先做个自我介绍吧", "q1", "q1", true),
                new TurnDto("candidate", "我是一名后端工程师……", null, "q1", true),
                new TurnDto("interviewer", "讲讲你最有挑战的项目", "q2", "q2", false)
        ));
        s.setAnswers(List.of("我是一名后端工程师……"));
        sessionMapper.insert(s);                 // id 由 IdType.ASSIGN_UUID 自动生成
        return ApiResult.ok(s);
    }

    @GetMapping("/session/{id}")
    public ApiResult<InterviewSession> get(@PathVariable String id) {
        InterviewSession s = sessionMapper.selectById(id);
        return s == null ? ApiResult.err("not found") : ApiResult.ok(s);
    }
}
