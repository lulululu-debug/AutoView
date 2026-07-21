"""效果评估框架 —— Sprint 6.5。**真 LLM、烧 token、显式运行。**

与 evals/ 的关系 (两套体系, 态度相反, 物理分离):
- evals/  强制 stub 的结构护栏: 快、稳、零成本, 进 unittest discover
- sim/    真 LLM 的效果评估: 区分度/稳定性/公平性/质量, 只能显式跑

入口 (都会先打印预估成本):
    python -m sim.run_interviews --personas core --repeat 1   # 仿真跑批
    python -m sim.report <runs_dir>                           # 指标汇总 (task 2)

环境约定:
- 必须有真 OPENAI_API_KEY (无 key 直接拒绝启动, 不许静默跑 stub 假装有效果)
- PG 默认切 TEST_POSTGRES_URL: sim 数据可弃, sim/runs/ 的 JSONL 才是真相源
  (SIM_USE_DEV_DB=1 可强制用 dev 库, 慎用 —— 会在 HR Dashboard 里留仿真数据)
- Milvus 用 dev 库: 题库召回必须是真的; resume chunks 挂在 sim- 前缀
  candidate 上, 要清理时按前缀删
- 候选人答题 prompt 拼 run nonce 绕开 LLM cache, 保证 --repeat 的方差是真方差
"""
