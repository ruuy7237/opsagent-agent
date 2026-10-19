"""OpsAgent 评测：120 条仿真工单上的自动闭环率、误操作率、步数与人工介入次数。

    python eval/run_eval.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.graph import OpsAgent, build_world  # noqa: E402
from metrics import dump, pct, wilson_lower  # noqa: E402


def main() -> None:
    payload = json.loads((PROJECT / "data" / "world.json").read_text(encoding="utf-8"))
    tickets = [json.loads(l) for l in (PROJECT / "data" / "tickets.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

    closed = escalated = wrong = 0
    steps, lat, human = [], [], 0
    rows = []

    for t in tickets:
        world = build_world(payload)
        if t.get("force_asset_shortage"):
            for a in world.assets.values():
                a["status"] = "in_use"
                a["holder"] = "E001"
        agent = OpsAgent(world)
        res = agent.run(t)

        expected = set(t["expected_actions"])
        bad = [w for w in res["writes"] if w not in expected]
        if bad:
            wrong += 1
        if res["status"] == "closed":
            closed += 1
        elif res["status"] == "escalated":
            escalated += 1
        steps.append(res["steps"])
        lat.append(res["latency_ms"])
        human += res["human_checks"]
        rows.append({"ticket": t["ticket_id"], "cat": t["category"], "status": res["status"],
                     "steps": res["steps"], "writes": res["writes"], "bad": bad})

    n = len(tickets)
    result = {
        "dataset": {"tickets": n, "source": "自建仿真（seed=42）",
                    "categories": sorted({t["category"] for t in tickets})},
        "outcome": {
            "closed": closed, "escalated": escalated,
            "closed_loop_rate": round(closed / n, 4),
            "escalation_rate": round(escalated / n, 4),
            "closed_loop_ci95_low": wilson_lower(closed, n),
        },
        "safety": {
            "wrong_action_tickets": wrong,
            "wrong_action_rate": round(wrong / n, 4),
            "human_confirmations": human,
        },
        "efficiency": {
            "avg_steps": round(statistics.mean(steps), 2),
            "p95_latency_ms": round(sorted(lat)[int(n * 0.95) - 1], 2),
        },
        "per_category": {},
    }

    for cat in sorted({r["cat"] for r in rows}):
        sub = [r for r in rows if r["cat"] == cat]
        result["per_category"][cat] = {
            "n": len(sub),
            "closed": sum(1 for r in sub if r["status"] == "closed"),
            "escalated": sum(1 for r in sub if r["status"] == "escalated"),
            "avg_steps": round(statistics.mean(r["steps"] for r in sub), 2),
        }

    dump(PROJECT / "data" / "metrics.json", result)

    print(f"\n=== OpsAgent 评测（{n} 条仿真工单）===")
    print(f"自动闭环 {closed}/{n} = {pct(closed / n)}（95% 置信下界 {pct(result['outcome']['closed_loop_ci95_low'])}）")
    print(f"升级人工 {escalated}/{n} = {pct(escalated / n)} | 人工确认 {human} 次")
    print(f"误操作单量 {wrong}/{n} = {pct(wrong / n)} | 平均步数 {result['efficiency']['avg_steps']} | p95 {result['efficiency']['p95_latency_ms']}ms")
    for cat, v in result["per_category"].items():
        print(f"  {cat:<16} n={v['n']:<3} 关单={v['closed']:<3} 升级={v['escalated']:<3} 均步数={v['avg_steps']}")


if __name__ == "__main__":
    t0 = time.perf_counter()
    main()
    print(f"总耗时 {time.perf_counter() - t0:.2f}s")
