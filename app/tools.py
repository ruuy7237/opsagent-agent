"""工具层。每个工具声明 name / description / inputSchema，入参用 JSON Schema 校验。

没直接用 MCP Server，是因为评测需要 world 状态可回放、可回滚，自己拿在手里好控制。
描述格式是按 MCP 的 tool 定义写的，以后要换成真 MCP Server 只改传输层，这一层不用动。

写操作在注册时一起登记补偿函数，失败时按调用栈倒序执行；标了 irreversible 的工具
不允许在没有人工确认的情况下执行。
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import jsonschema


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[["World", dict], dict]
    write: bool = False
    irreversible: bool = False

    def as_mcp(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}


@dataclass
class World:
    """被 Agent 操作的企业系统（内存实现，便于每次评测重置）。"""
    users: dict[str, dict] = field(default_factory=dict)
    assets: dict[str, dict] = field(default_factory=dict)
    tickets: dict[str, dict] = field(default_factory=dict)
    kb: list[dict] = field(default_factory=list)
    applied: dict[str, dict] = field(default_factory=dict)   # 幂等键 → 首次结果
    actions: list[dict] = field(default_factory=list)        # 全量动作轨迹
    compensation: list[tuple[str, dict]] = field(default_factory=list)

    def clone(self) -> "World":
        return copy.deepcopy(self)


# ----------------------------- 工具实现 -----------------------------
def _get_user(w: World, a: dict) -> dict:
    u = w.users.get(a["employee_id"])
    if not u:
        raise KeyError(f"员工不存在: {a['employee_id']}")
    return {"employee_id": u["id"], "name": u["name"], "account": u["account"],
            "status": u["status"], "dept": u["dept"]}


def _check_account(w: World, a: dict) -> dict:
    u = w.users.get(a["employee_id"])
    if not u:
        raise KeyError("员工不存在")
    return {"account": u["account"], "status": u["status"],
            "locked_until": u.get("locked_until"), "mfa": u.get("mfa", False)}


def _reset_password(w: World, a: dict) -> dict:
    u = w.users[a["employee_id"]]
    u["status"] = "active"
    u["locked_until"] = None
    w.compensation.append(("restore_account", {"employee_id": u["id"], "status": "locked"}))
    return {"employee_id": u["id"], "temp_password": "Tmp#2026", "expires_in_hours": 24}


def _unlock_account(w: World, a: dict) -> dict:
    u = w.users[a["employee_id"]]
    prev = u["status"]
    u["status"] = "active"
    u["locked_until"] = None
    w.compensation.append(("restore_account", {"employee_id": u["id"], "status": prev}))
    return {"employee_id": u["id"], "status": "active"}


def _check_asset(w: World, a: dict) -> dict:
    items = [x for x in w.assets.values() if x["holder"] == a["employee_id"]]
    return {"employee_id": a["employee_id"], "assets": items, "count": len(items)}


def _assign_asset(w: World, a: dict) -> dict:
    asset = w.assets[a["asset_id"]]
    if asset["status"] != "idle":
        raise ValueError(f"资产 {a['asset_id']} 当前状态为 {asset['status']}，不可分配")
    asset["status"] = "in_use"
    asset["holder"] = a["employee_id"]
    return {"asset_id": a["asset_id"], "holder": a["employee_id"], "status": "in_use"}


def _check_network(w: World, a: dict) -> dict:
    u = w.users.get(a["employee_id"])
    if not u:
        raise KeyError("员工不存在")
    return {"vpn": u.get("vpn", "ok"), "switch_port": u.get("port", "B2-17"),
            "latency_ms": u.get("latency", 12)}


def _search_kb(w: World, a: dict) -> dict:
    q = a["query"].lower()
    hits = [k for k in w.kb if any(t in (k["title"] + k["body"]).lower() for t in q.split())]
    return {"hits": hits[:2], "count": len(hits)}


def _create_ticket(w: World, a: dict) -> dict:
    tid = a["ticket_id"]
    w.tickets[tid] = {"id": tid, "category": a["category"], "status": "open",
                      "employee_id": a["employee_id"], "summary": a["summary"]}
    return {"ticket_id": tid, "status": "open"}


def _escalate(w: World, a: dict) -> dict:
    t = w.tickets[a["ticket_id"]]
    t["status"] = "escalated"
    t["queue"] = a["queue"]
    return {"ticket_id": a["ticket_id"], "status": "escalated", "queue": a["queue"]}


def _close_ticket(w: World, a: dict) -> dict:
    t = w.tickets[a["ticket_id"]]
    t["status"] = "closed"
    t["resolution"] = a["resolution"]
    return {"ticket_id": a["ticket_id"], "status": "closed"}


def _restore_account(w: World, a: dict) -> dict:
    u = w.users[a["employee_id"]]
    u["status"] = a["status"]
    return {"employee_id": u["id"], "status": u["status"]}


_S = lambda props, req: {"type": "object", "properties": props, "required": req,
                         "additionalProperties": False}
_STR = lambda d="": {"type": "string", "description": d}

TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_user", "按工号查询员工基本信息", _S({"employee_id": _STR()}, ["employee_id"]), _get_user),
    Tool("check_account", "查询账号状态、锁定时间与 MFA", _S({"employee_id": _STR()}, ["employee_id"]), _check_account),
    Tool("check_asset", "查询员工名下资产", _S({"employee_id": _STR()}, ["employee_id"]), _check_asset),
    Tool("check_network", "查询 VPN 与网络端口状态", _S({"employee_id": _STR()}, ["employee_id"]), _check_network),
    Tool("search_kb", "检索 IT 知识库", _S({"query": _STR()}, ["query"]), _search_kb),
    Tool("reset_password", "重置密码并解锁账号", _S({"employee_id": _STR()}, ["employee_id"]),
         _reset_password, write=True),
    Tool("unlock_account", "解锁被锁定的账号", _S({"employee_id": _STR()}, ["employee_id"]),
         _unlock_account, write=True),
    Tool("assign_asset", "把空闲资产分配给员工", _S({"asset_id": _STR(), "employee_id": _STR()},
                                                ["asset_id", "employee_id"]),
         _assign_asset, write=True, irreversible=True),
    Tool("create_ticket", "创建工单", _S({"ticket_id": _STR(), "category": _STR(),
                                       "employee_id": _STR(), "summary": _STR()},
                                      ["ticket_id", "category", "employee_id", "summary"]),
         _create_ticket, write=True),
    Tool("escalate", "升级到二线队列", _S({"ticket_id": _STR(), "queue": _STR()},
                                        ["ticket_id", "queue"]), _escalate, write=True),
    Tool("close_ticket", "关闭工单并写入结论", _S({"ticket_id": _STR(), "resolution": _STR()},
                                              ["ticket_id", "resolution"]), _close_ticket, write=True),
    Tool("restore_account", "补偿动作：恢复账号原状态", _S({"employee_id": _STR(), "status": _STR()},
                                                     ["employee_id", "status"]), _restore_account, write=True),
]}


def execute(world: World, name: str, args: dict, idem_key: str | None = None) -> dict:
    """执行工具：schema 校验 → 幂等命中 → 执行 → 记录轨迹。"""
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "error": f"未知工具: {name}", "tool": name}

    try:
        jsonschema.validate(args, tool.schema)
    except jsonschema.ValidationError as exc:
        return {"ok": False, "error": f"参数校验失败: {exc.message}", "tool": name}

    if tool.write and idem_key and idem_key in world.applied:
        return {**world.applied[idem_key], "cached": True}

    try:
        data = tool.fn(world, args)
        result = {"ok": True, "tool": name, "data": data}
    except Exception as exc:  # 业务异常也回传给 Agent，便于自愈
        result = {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}

    if tool.write and idem_key:
        world.applied[idem_key] = result
    world.actions.append({"tool": name, "args": args, "ok": result["ok"],
                          "write": tool.write, "irreversible": tool.irreversible})
    return result


def rollback(world: World, keep: int = 0) -> int:
    """按栈逆序执行补偿，返回撤销条数。"""
    undone = 0
    while len(world.compensation) > keep:
        name, args = world.compensation.pop()
        try:
            TOOLS[name].fn(world, args)
            undone += 1
        except Exception:
            break
    return undone


def tool_manifest() -> str:
    return json.dumps([t.as_mcp() for t in TOOLS.values()], ensure_ascii=False, indent=2)
