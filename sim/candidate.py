"""LLM 扮演候选人作答 —— Sprint 6.5 task 1。

- persona 的简历 + 答题风格拼进 system prompt, 严格入戏
- 带对话历史: 追问要与此前回答一致, 不许凭空换项目
- nonce (run_id) 拼进 system: 不同 run 的 prompt 不同 → 不撞 LLM Redis cache,
  --repeat 测出来的方差才是真方差
- 拿到 stub 输出直接抛错: sim 不许在无 key 环境下假装跑效果
"""
from __future__ import annotations

from src import llm

# 历史里的单条回答截断长度: 控 token, 不影响一致性判断
_HISTORY_ANSWER_CLIP = 300

_SYSTEM_TMPL = """你在一场模拟技术面试中扮演候选人「{name}」。你的任务是严格入戏, \
按下面的简历背景和回答风格作答, 用于测试 AI 面试系统的评估效果。

你的简历:
{resume}

{answer_style}

硬约束:
- 只输出回答正文, 第一人称口语, 不要旁白、引号或角色标注
- 与之前的回答保持一致, 不编造与简历冲突的新经历
- 仿真批次: {nonce}"""


def answer(
    persona,
    question: str,
    history: list[tuple[str, str]],
    nonce: str,
) -> str:
    """以 persona 身份回答一道面试题。history: [(角色, 文本), ...]"""
    system = _SYSTEM_TMPL.format(
        name=persona.display_name,
        resume=persona.resume,
        answer_style=persona.answer_style,
        nonce=nonce,
    )
    lines = [
        f"{role}: {text[:_HISTORY_ANSWER_CLIP]}" for role, text in history
    ]
    context = ("之前的对话:\n" + "\n".join(lines) + "\n\n") if lines else ""
    user = f"{context}面试官: {question}\n\n请以候选人身份回答这个问题。"

    text = llm.complete(system, user, max_tokens=600, timeout=60.0)
    if llm.is_stub(text):
        raise RuntimeError(
            "LLM 返回 stub, sim 拒绝继续 (OPENAI_API_KEY 失效或被清)"
        )
    return text.strip()
