package com.interview.controller;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.interview.domain.User;
import com.interview.dto.req.LoginReq;
import com.interview.dto.req.RegisterReq;
import com.interview.dto.resp.ApiResult;
import com.interview.exception.BizException;
import com.interview.mapper.UserMapper;
import com.interview.security.JwtService;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/auth")
@RequiredArgsConstructor
public class AuthController {

    private final UserMapper userMapper;
    private final PasswordEncoder encoder;
    private final JwtService jwt;

    /** 注册 HR 账号（demo）。 */
    @PostMapping("/register")
    public ApiResult<Map<String, String>> register(@RequestBody @Valid RegisterReq req) {
        if (userMapper.selectOne(Wrappers.<User>lambdaQuery().eq(User::getUsername, req.username())) != null)
            throw new BizException("username already exists");
        User u = new User();
        u.setUsername(req.username());
        u.setPasswordHash(encoder.encode(req.password()));
        u.setRole("hr");
        userMapper.insert(u);
        return ApiResult.ok(Map.of("userId", u.getId(), "role", u.getRole()));
    }

    /** 登录，签发 HR token（同时写 httpOnly cookie + 返回 body）。 */
    @PostMapping("/login")
    public ApiResult<Map<String, String>> login(@RequestBody @Valid LoginReq req, HttpServletResponse resp) {
        User u = userMapper.selectOne(Wrappers.<User>lambdaQuery().eq(User::getUsername, req.username()));
        if (u == null || !encoder.matches(req.password(), u.getPasswordHash()))
            throw new BizException("invalid username or password");
        String token = jwt.issueHr(u.getId(), u.getRole());
        Cookie cookie = new Cookie("token", token);
        cookie.setHttpOnly(true);
        cookie.setPath("/");
        resp.addCookie(cookie);
        return ApiResult.ok(Map.of("token", token, "role", u.getRole()));
    }

    /**
     * 签发候选人一次性 token（demo 用；真实系统应由 HR 邀请链路下发）。
     * 这是补掉「候选人软鉴权」短板的入口：候选人端从此需要携带此 token。
     */
    @PostMapping("/candidate-token")
    public ApiResult<Map<String, String>> candidateToken(@RequestParam String candidateId) {
        return ApiResult.ok(Map.of("token", jwt.issueCandidate(candidateId), "candidateId", candidateId));
    }
}
