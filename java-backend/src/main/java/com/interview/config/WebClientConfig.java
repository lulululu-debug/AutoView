package com.interview.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
public class WebClientConfig {

    /** 调 Python AI 服务的 WebClient。仅 ai.enabled=true 时真正发起请求。 */
    @Bean
    public WebClient aiWebClient(@Value("${ai.base-url}") String baseUrl) {
        return WebClient.builder().baseUrl(baseUrl).build();
    }
}
