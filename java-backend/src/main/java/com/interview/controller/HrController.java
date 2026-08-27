package com.interview.controller;

import com.interview.domain.EvaluationReport;
import com.interview.dto.resp.ApiResult;
import com.interview.exception.OwnershipException;
import com.interview.mapper.EvaluationReportMapper;
import com.interview.security.OwnershipGuard;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

/**
 * HR 端。所有资源访问都过所有权链路校验，越权返回 404（防枚举）。
 */
@RestController
@RequestMapping("/hr")
@RequiredArgsConstructor
public class HrController {

    private final EvaluationReportMapper reportMapper;
    private final OwnershipGuard ownershipGuard;

    /** 查看某场面试的评估报告（HR 决策依据）。 */
    @GetMapping("/reports/{sessionId}")
    public ApiResult<EvaluationReport> report(@PathVariable String sessionId, Authentication auth) {
        String userId = auth.getName();
        boolean admin = auth.getAuthorities().stream()
                .anyMatch(a -> a.getAuthority().equals("ROLE_ADMIN"));
        ownershipGuard.assertOwnsSession(userId, admin, sessionId);   // 非 owner → 404

        EvaluationReport r = reportMapper.selectById(sessionId);
        if (r == null) throw new OwnershipException();                // 报告不存在也返回 404
        return ApiResult.ok(r);
    }
}
