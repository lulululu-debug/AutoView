"""LLM-as-judge 批次审计 —— Sprint 6.5 task 4。**烧 token (~100+ 次 gpt-4o)。**

    python -m sim.judge                      # 最新 batch-* 目录
    python -m sim.judge sim/runs/batch-xxx

前置: sim.calibrate_judges 必须先通过, 否则本审计结论不算数。
采样: 每 persona 取 r1 (避免 repeat 间重复计费); 题目相关性按
(题目, 维度) 全局去重。

红线:
- 项目题编造 (invented) > 0 → ❌ 硬红线 ("读简历瞎猜项目"是明令要防的失真)
- 题目相关率 < 90% → ❌
- 追问针对率 < 70% → ⚠️
- 报告不忠实 (summary 含无依据事实) 比例 > 20% → ❌
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from sim._env import bootstrap


def _latest_batch() -> Path:
    dirs = sorted(
        d for d in Path("sim/runs").iterdir()
        if d.is_dir() and d.name.startswith("batch-")
    )
    if not dirs:
        raise SystemExit("没有 batch-* 目录")
    return dirs[-1]


def _transcript(session: dict) -> str:
    role_cn = {"interviewer": "面试官", "candidate": "候选人"}
    return "\n".join(
        f"{role_cn.get(t['role'], t['role'])}: {t['text']}"
        for t in session.get("history", [])
    )


def main() -> None:
    bootstrap()
    from sim import judges
    from sim.personas import ALL_PERSONAS

    runs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_batch()
    artifacts = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(runs_dir.glob("*_r1.json"))
    ]
    artifacts = [a for a in artifacts if "report" in a]
    if not artifacts:
        raise SystemExit(f"{runs_dir} 无 r1 artifact")
    # 兜底: 老 artifact 的 plan 是 resolve 前快照 (lazy 项目题 text 为空),
    # 从 session.history 按 ref_id 回捞实际问出的文本
    for a in artifacts:
        asked_text = {
            t["ref_id"]: t["text"] for t in a["session"].get("history", [])
            if t.get("role") == "interviewer" and t.get("ref_id")
        }
        for r in a["plan"]["rounds"]:
            for q in r["questions"]:
                if not q.get("text") and q["question_id"] in asked_text:
                    q["text"] = asked_text[q["question_id"]]
    print(f"[judge] 审计 {runs_dir} 的 {len(artifacts)} 份 r1 artifact "
          f"(judge={judges._judge_model()})")

    failures: list[str] = []
    lines = ["# LLM-as-judge 审计报告", "", f"来源: {runs_dir}/ (r1 采样)", ""]

    # ---- 1. 题目相关性 (全局去重) ----
    seen: set[tuple[str, str]] = set()
    rel_total = rel_pass = 0
    rel_fails: list[str] = []
    for a in artifacts:
        comp_name = {
            c["competency_id"]: c["name"] for c in a["plan"]["competencies"]
        }
        jd = a["job"]["jd"]
        for r in a["plan"]["rounds"]:
            for q in r["questions"]:
                if not q.get("competency_id") or not q.get("text"):
                    continue  # self_intro / 未 resolve 的 lazy
                if q.get("category") == "project_experience":
                    continue  # project 题考察"候选人自己的项目", JD 相关性
                              # 标准失配 (首审误伤), 它由溯源 judge 单独管
                key = (q["text"], q["competency_id"])
                if key in seen:
                    continue
                seen.add(key)
                v = judges.judge_question_relevance(
                    q["text"], jd, comp_name.get(q["competency_id"], "?"),
                )
                rel_total += 1
                if v["relevant"]:
                    rel_pass += 1
                else:
                    rel_fails.append(f"{q['text'][:60]} ({v['reason'][:40]})")
    rel_rate = rel_pass / rel_total if rel_total else 1.0
    ok = rel_rate >= 0.9
    lines.append(f"## 题目相关性: {rel_pass}/{rel_total} = {rel_rate:.0%} "
                 f"{'✅' if ok else '❌'}")
    lines += [f"- ❌ {t}" for t in rel_fails] + [""]
    if not ok:
        failures.append(f"题目相关率 {rel_rate:.0%} < 90%")
    print(f"[judge] 相关性 {rel_pass}/{rel_total}")

    # ---- 2. 追问针对性 ----
    fu_total = fu_pass = 0
    fu_fails: list[str] = []
    for a in artifacts:
        plan_qids = {
            q["question_id"] for r in a["plan"]["rounds"] for q in r["questions"]
        }
        hist = a["session"]["history"]
        assess_by_q: dict[str, list] = {}
        for x in a["session"]["assessments"]:
            assess_by_q.setdefault(x["question_id"], []).append(x)
        cur_q_text, cur_qid, prev_answer = "", None, ""
        for i, t in enumerate(hist):
            if t["role"] == "candidate":
                prev_answer = t["text"]
                continue
            if t.get("ref_id") in plan_qids:
                cur_q_text, cur_qid = t["text"], t["ref_id"]
            else:  # followup turn
                ms = []
                if cur_qid and assess_by_q.get(cur_qid):
                    ms = assess_by_q[cur_qid][0].get("missing_signals", [])
                v = judges.judge_followup_targeting(
                    cur_q_text, prev_answer, ms, t["text"],
                )
                fu_total += 1
                if v["targeted"]:
                    fu_pass += 1
                else:
                    fu_fails.append(
                        f"[{a['persona_id']}] {t['text'][:60]} ({v['reason'][:40]})"
                    )
    fu_rate = fu_pass / fu_total if fu_total else 1.0
    ok = fu_rate >= 0.7
    lines.append(f"## 追问针对性: {fu_pass}/{fu_total} = {fu_rate:.0%} "
                 f"{'✅' if ok else '⚠️'}")
    lines += [f"- ❌ {t}" for t in fu_fails] + [""]
    if not ok:
        failures.append(f"追问针对率 {fu_rate:.0%} < 70%")
    print(f"[judge] 追问针对 {fu_pass}/{fu_total}")

    # ---- 3. 报告忠实性 ----
    fa_total = fa_pass = 0
    fa_fails: list[str] = []
    for a in artifacts:
        v = judges.judge_report_faithfulness(
            a["report"]["summary"], _transcript(a["session"]),
        )
        fa_total += 1
        claims = [c for c in (v.get("unsupported_claims") or []) if c.strip()]
        # 裁决规则: 无具体指控不判不忠实 (校准已知 judge 偶发 faithful=false
        # 但零 claim 的不一致输出; 没有指控的有罪判决不成立)
        if v["faithful"] or not claims:
            fa_pass += 1
        else:
            fa_fails.append(f"[{a['persona_id']}] {'; '.join(claims)[:120]}")
    unfaithful_rate = 1 - fa_pass / fa_total
    ok = unfaithful_rate <= 0.2
    lines.append(f"## 报告忠实性: {fa_pass}/{fa_total} 忠实 "
                 f"(不忠实率 {unfaithful_rate:.0%}) {'✅' if ok else '❌'}")
    lines += [f"- ❌ {t}" for t in fa_fails] + [""]
    if not ok:
        failures.append(f"报告不忠实率 {unfaithful_rate:.0%} > 20%")
    print(f"[judge] 报告忠实 {fa_pass}/{fa_total}")

    # ---- 4. 项目题溯源 (硬红线) ----
    pj_total = pj_pass = 0
    pj_fails: list[str] = []
    for a in artifacts:
        persona = ALL_PERSONAS.get(a["persona_id"])
        if persona is None:
            continue
        intro = a["session"].get("intro_text") or ""
        seen_pj: set[str] = set()
        for r in a["plan"]["rounds"]:
            for q in r["questions"]:
                if q.get("category") != "project_experience" or not q.get("text"):
                    continue
                if q["text"] in seen_pj:
                    continue
                seen_pj.add(q["text"])
                v = judges.judge_project_question_faithfulness(
                    q["text"], persona.resume, intro,
                )
                pj_total += 1
                if v["grounded"]:
                    pj_pass += 1
                else:
                    inv = "; ".join(v.get("invented", []))[:80]
                    pj_fails.append(
                        f"[{a['persona_id']}] {q['text'][:50]} (编造: {inv})"
                    )
    invented = pj_total - pj_pass
    ok = invented == 0
    lines.append(f"## 项目题溯源: {pj_pass}/{pj_total} 有据 "
                 f"(编造 {invented}) {'✅' if ok else '❌ 硬红线'}")
    lines += [f"- ❌ {t}" for t in pj_fails] + [""]
    if not ok:
        failures.append(f"项目题编造 {invented} 道 (硬红线)")
    print(f"[judge] 项目题溯源 {pj_pass}/{pj_total}")

    report = "\n".join(lines)
    out = runs_dir / f"judge_report-{time.strftime('%H%M%S')}.md"
    out.write_text(report, encoding="utf-8")
    print(f"\n{report}\n[judge] 报告: {out}")
    if failures:
        print(f"[judge] ❌ {len(failures)} 项未过:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("[judge] ✅ 四项审计全部通过")


if __name__ == "__main__":
    main()
