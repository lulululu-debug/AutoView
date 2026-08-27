package com.interview.controller;

import com.interview.dto.req.SubmitAnswerReq;
import com.interview.dto.resp.ApiResult;
import com.interview.dto.resp.ReportResp;
import com.interview.dto.resp.TurnResp;
import com.interview.service.orchestrator.SessionService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.security.Principal;

/**
 * 候选人面试端。/interview/** 要求 ROLE_CANDIDATE token；principal 即 candidateId。
 * 每个操作都校验会话归属，补掉「候选人软鉴权」短板。
 */
@RestController
@RequestMapping("/interview")
@RequiredArgsConstructor
public class InterviewController {

    private final SessionService session;

    /** 开始面试。candidateId 取自 token，不信任前端传参。 */
    @PostMapping("/start")
    public ApiResult<TurnResp> start(@RequestParam(required = false) String jobId, Principal principal) {
        return ApiResult.ok(session.start(principal.getName(), jobId));
    }

    @PostMapping("/{sessionId}/answer")
    public ApiResult<TurnResp> submit(@PathVariable String sessionId,
                                      @RequestBody @Valid SubmitAnswerReq req, Principal principal) {
        session.assertCandidate(sessionId, principal.getName());
        return ApiResult.ok(session.submit(sessionId, req.answer()));
    }

    @GetMapping("/{sessionId}/resume")
    public ApiResult<TurnResp> resume(@PathVariable String sessionId, Principal principal) {
        session.assertCandidate(sessionId, principal.getName());
        return ApiResult.ok(session.resume(sessionId));
    }

    /** 结束面试并归档报告。候选人端不返回报告明细（仅状态）；完整报告在 HR 端。 */
    @PostMapping("/{sessionId}/finalize")
    public ApiResult<ReportResp> finalizeIt(@PathVariable String sessionId, Principal principal) {
        session.assertCandidate(sessionId, principal.getName());
        return ApiResult.ok(session.finalizeSession(sessionId));
    }
}
