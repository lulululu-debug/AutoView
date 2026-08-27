package com.interview.dto.req;

import jakarta.validation.constraints.NotBlank;

public record UploadCandidateReq(
        @NotBlank String jobId,
        String name,
        @NotBlank String resumeText
) {}
