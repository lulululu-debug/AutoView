package com.interview.controller;

import com.interview.domain.Candidate;
import com.interview.dto.req.UploadCandidateReq;
import com.interview.dto.resp.ApiResult;
import com.interview.mapper.CandidateMapper;
import com.interview.service.mq.IngestMsg;
import com.interview.service.mq.IngestProducer;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.nio.charset.StandardCharsets;
import java.util.HexFormat;
import java.security.MessageDigest;
import java.util.Map;

/**
 * 候选人上传端。上传后立即入队/兜底处理，接口快速返回“处理中”（对应把长耗时移出 HTTP 请求）。
 */
@RestController
@RequestMapping("/candidate")
@RequiredArgsConstructor
public class CandidateController {

    private final CandidateMapper candidateMapper;
    private final IngestProducer ingestProducer;

    @PostMapping("/upload")
    public ApiResult<Map<String, String>> upload(@RequestBody @Valid UploadCandidateReq req) {
        Candidate c = new Candidate();
        c.setJobId(req.jobId());
        c.setName(req.name());
        c.setResumeText(req.resumeText());
        c.setContentHash(sha256(req.resumeText()));      // 幂等键
        candidateMapper.insert(c);                        // id 自动生成

        ingestProducer.submit(new IngestMsg(c.getId(), req.jobId()));   // 异步/同步兜底
        return ApiResult.ok(Map.of("candidateId", c.getId(), "status", "processing"));
    }

    private String sha256(String s) {
        try {
            byte[] d = MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(d).substring(0, 16);
        } catch (Exception e) {
            return Integer.toHexString(s.hashCode());
        }
    }
}
