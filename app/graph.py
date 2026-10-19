"""工单处理的状态机，用 LangGraph 的 StateGraph 写。

    triage ──▶ guard ──▶ act ──▶ verify ──┬─▶ act（下一步）
                                          ├─▶ recover（重试 / 改道 / 升级）
                                          └─▶ finalize（收尾）

几点：
- 写操作前过 guard，不可逆的（比如分配资产）挂起等人工确认，不静默执行；
- verify 读工具返回判断状态，失败不抛异常，交给 recover 决定重试还是放弃，
  这样长链路不会因为一步失败整体崩掉；
- 每个写操作带幂等键，重放不会产生第二次副作用。
"""
from __future__ import annotations

import time
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.tools import TOOLS, World, execute, rollback


class S(TypedDict, total=False):
    ticket: dict
    plan: list[dict]
    idx: int
    retry: int
    status: str
    need_human: bool
    approved: bool
    human_checks: int
    last: dict
    trace: list[str]
    errors: list[str]


CATEGORY_PLAN = {
    "password_reset": ["check_account", "reset_password", "close_ticket"],
    "account_locked": ["check_account", "unlock_account", "close_ticket"],
    "asset_request": ["check_asset", "assign_asset", "close_ticket"],
    "network": ["check_network", "close_ticket"],
    "software": ["search_kb", "close_ticket"],
    "unknown": ["escalate"],
}


class OpsAgent:
    def __init__(self, world: World) -> None:
        self.world = world
        self.graph = self._build()

    # ---------------- 节点 ----------------
    def triage(self, s: S) -> S:
        t = s["ticket"]
        steps = CATEGORY_PLAN.get(t["category"], ["escalate"])
        plan: list[dict] = []
        for i, name in enumerate(steps):
            if name == "check_account":
                args = {"employee_id": t["employee_id"]}
            elif name in {"reset_password", "unlock_account"}:
                args = {"employee_id": t["employee_id"]}
            elif name == "check_asset":
                args = {"employee_id": t["employee_id"]}
            elif name == "check_network":
                args = {"employee_id": t["employee_id"]}
            elif name == "search_kb":
                args = {"query": t["issue"]}
            elif name == "assign_asset":
                idle = self._pick_idle_asset()
                if idle is None:  # 资产不足：直接改道升级，不在失败后才救
                    plan.append({"tool": "escalate", "args": {"ticket_id": t["ticket_id"], "queue": "资产管理员"}})
                    break
                args = {"asset_id": idle, "employee_id": t["employee_id"]}
            elif name == "escalate":
                args = {"ticket_id": t["ticket_id"], "queue": "二线支持"}
            elif name == "close_ticket":
                args = {"ticket_id": t["ticket_id"], "resolution": f"已按 {t['category']} 标准流程处理"}
            else:
                args = {}
            plan.append({"tool": name, "args": args})
        return {**s, "plan": plan, "idx": 0, "retry": 0, "status": "running",
                "need_human": False, "approved": False, "human_checks": 0,
                "trace": s.get("trace", []) + [f"triage→{t['category']}:{len(plan)}步"],
                "errors": []}

    def _pick_idle_asset(self) -> str | None:
        for aid, a in self.world.assets.items():
            if a["status"] == "idle":
                return aid
        return None

    def guard(self, s: S) -> S:
        """执行前检查：不可逆动作进入确认分支。"""
        if s["idx"] >= len(s["plan"]):
            return s
        step = s["plan"][s["idx"]]
        tool = TOOLS.get(step["tool"])
        if tool and tool.irreversible and not s.get("approved"):
            return {**s, "need_human": True,
                    "trace": s["trace"] + [f"guard→{step['tool']} 需人工确认"]}
        return s

    def human(self, s: S) -> S:
        """模拟人工审批（真实场景可替换为 IM 卡片回调 / 审批流）。"""
        return {**s, "approved": True, "human_checks": s.get("human_checks", 0) + 1,
                "need_human": False, "trace": s["trace"] + ["human→已确认"]}

    def act(self, s: S) -> S:
        if s["idx"] >= len(s["plan"]):
            return {**s, "status": "done"}
        step = s["plan"][s["idx"]]
        key = f"{s['ticket']['ticket_id']}:{s['idx']}:{step['tool']}"
        result = execute(self.world, step["tool"], step["args"], idem_key=key)
        return {**s, "last": result, "trace": s["trace"] + [f"act→{step['tool']}({'ok' if result['ok'] else 'err'})"]}

    def verify(self, s: S) -> S:
        last = s.get("last") or {}
        if last.get("ok", True):
            return {**s, "idx": s["idx"] + 1, "retry": 0}
        return {**s, "errors": s.get("errors", []) + [last.get("error", "")]}

    def recover(self, s: S) -> S:
        err = s["errors"][-1] if s["errors"] else ""
        tool = s["plan"][s["idx"]]["tool"]
        # 资产被占用：业务不可重试，直接升级，并撤销已执行的前序写操作
        if tool == "assign_asset":
            rollback(self.world)
            s = {**s, "plan": s["plan"][: s["idx"]] + [
                {"tool": "escalate", "args": {"ticket_id": s["ticket"]["ticket_id"],
                                              "queue": "资产管理员"}}],
                "retry": 0,
                "trace": s["trace"] + ["recover→撤销并升级"]}
            return s
        if s.get("retry", 0) < 1:
            return {**s, "retry": s.get("retry", 0) + 1, "trace": s["trace"] + [f"recover→重试{err[:24]}"]}
        return {**s, "plan": s["plan"][: s["idx"]] + [
            {"tool": "escalate", "args": {"ticket_id": s["ticket"]["ticket_id"], "queue": "二线支持"}}],
            "retry": 0, "trace": s["trace"] + ["recover→升级"]}

    def finalize(self, s: S) -> S:
        t = self.world.tickets.get(s["ticket"]["ticket_id"])
        status = t["status"] if t else "not_found"
        return {**s, "status": status}

    # ---------------- 路由 ----------------
    @staticmethod
    def route_after_guard(s: S) -> str:
        if s.get("need_human"):
            return "human"
        return "act" if s["idx"] < len(s["plan"]) else "finalize"

    @staticmethod
    def route_after_verify(s: S) -> str:
        last = s.get("last") or {}
        if not last.get("ok", True):
            return "recover"
        return "guard" if s["idx"] < len(s["plan"]) else "finalize"

    def _build(self):
        g = StateGraph(S)
        g.add_node("triage", self.triage)
        g.add_node("guard", self.guard)
        g.add_node("human", self.human)
        g.add_node("act", self.act)
        g.add_node("verify", self.verify)
        g.add_node("recover", self.recover)
        g.add_node("finalize", self.finalize)
        g.set_entry_point("triage")
        g.add_edge("triage", "guard")
        g.add_conditional_edges("guard", self.route_after_guard,
                                {"human": "human", "act": "act", "finalize": "finalize"})
        g.add_edge("human", "guard")
        g.add_edge("act", "verify")
        g.add_conditional_edges("verify", self.route_after_verify,
                                {"recover": "recover", "guard": "guard", "finalize": "finalize"})
        g.add_edge("recover", "verify")
        g.add_edge("finalize", END)
        return g.compile()

    # ---------------- 对外 ----------------
    def run(self, ticket: dict, max_steps: int = 12) -> dict:
        started = time.perf_counter()
        # 工单已存在于工单系统，Agent 的处理对象是"已受理的工单"
        self.world.tickets.setdefault(ticket["ticket_id"], {
            "id": ticket["ticket_id"], "category": ticket["category"],
            "status": "open", "employee_id": ticket["employee_id"],
            "summary": ticket.get("issue", ""),
        })
        before = len(self.world.actions)
        out = self.graph.invoke({"ticket": ticket, "trace": []},
                                {"recursion_limit": max_steps * 4})
        acts = self.world.actions[before:]
        return {
            "ticket_id": ticket["ticket_id"],
            "status": out.get("status"),
            "steps": len(acts),
            "writes": [a["tool"] for a in acts if a["write"]],
            "human_checks": out.get("human_checks", 0),
            "errors": out.get("errors", []),
            "trace": out.get("trace", []),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }


def build_world(payload: dict) -> World:
    """每张工单一个独立世界快照，避免评测之间互相污染（资产被前一张单占用）。"""
    import copy

    return World(users=copy.deepcopy(payload["users"]),
                 assets=copy.deepcopy(payload["assets"]),
                 kb=copy.deepcopy(payload["kb"]), tickets={})
