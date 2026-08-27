package com.interview.service.vector;

import io.milvus.client.MilvusServiceClient;
import io.milvus.param.R;
import io.milvus.param.collection.HasCollectionParam;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 向量检索封装（documents / questions 两个 collection）。
 * 未配置 Milvus 客户端时，所有方法优雅降级：ready()=false、检索返回空，调用方走原文截断/模板兜底。
 *
 * 注：真正的相似度检索需要查询向量（OpenAI embedding），当前 Java 侧未接入 embedding，
 * 故 searchResumeChunks 暂返回空并留 TODO；此处先把连接与健康检查打通（hasCollection）。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class MilvusRetriever {

    private final ObjectProvider<MilvusServiceClient> clientProvider;

    public boolean ready() {
        MilvusServiceClient client = clientProvider.getIfAvailable();
        if (client == null) return false;
        try {
            R<Boolean> r = client.hasCollection(
                    HasCollectionParam.newBuilder().withCollectionName("documents").build());
            return r.getData() != null && r.getData();
        } catch (Exception e) {
            log.warn("milvus hasCollection failed: {}", e.getMessage());
            return false;
        }
    }

    /** 检索候选人简历片段（source_id == candidateId 逻辑隔离）。当前降级为空，见类注释。 */
    public List<String> searchResumeChunks(String candidateId, List<Float> queryVector, int topK) {
        MilvusServiceClient client = clientProvider.getIfAvailable();
        if (client == null) return List.of();
        // TODO: 接入 embedding 后用 SearchParam(withExpr "source_id == candidateId", COSINE, topK) 做真实检索
        return List.of();
    }
}
