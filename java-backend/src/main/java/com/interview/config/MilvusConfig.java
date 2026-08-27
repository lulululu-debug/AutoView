package com.interview.config;

import io.milvus.client.MilvusServiceClient;
import io.milvus.param.ConnectParam;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.StringUtils;

/**
 * 仅当 milvus.uri 配置时才创建客户端；否则不建 Bean，MilvusRetriever 降级为空检索。
 */
@Configuration
public class MilvusConfig {

    @Bean(destroyMethod = "close")
    @ConditionalOnProperty(prefix = "milvus", name = "uri")
    public MilvusServiceClient milvusClient(@Value("${milvus.uri}") String uri,
                                            @Value("${milvus.token:}") String token) {
        ConnectParam.Builder b = ConnectParam.newBuilder().withUri(uri);
        if (StringUtils.hasText(token)) b.withToken(token);
        return new MilvusServiceClient(b.build());
    }
}
