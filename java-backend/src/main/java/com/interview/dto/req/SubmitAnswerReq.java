package com.interview.dto.req;

import jakarta.validation.constraints.NotBlank;

public record SubmitAnswerReq(
        @NotBlank(message = "must not be blank") String answer
) {}
