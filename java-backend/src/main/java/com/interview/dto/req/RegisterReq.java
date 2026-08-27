package com.interview.dto.req;

import jakarta.validation.constraints.NotBlank;

public record RegisterReq(
        @NotBlank String username,
        @NotBlank String password
) {}
