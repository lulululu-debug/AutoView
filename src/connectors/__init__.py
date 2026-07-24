"""外部系统连接器 —— Sprint 6.7 起。

约定(与 src/tts、src/stt 同款单一调用点哲学):
- 每个外部系统一个模块, 业务层只 import 这里, 不散落 HTTP 细节
- 未配置 → is_configured() False / 调用抛类型化异常, 绝不静默假数据
- 全部调用带 timeout; 凭证只进 env 与 Redis 短存, 不落 PG
"""
