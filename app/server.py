"""OpsAgent HTTP 服务：跑单条工单、查看工具清单。

    uvicorn app.server:app --port 8012
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.graph import OpsAgent, build_world  # noqa: E402
from app.tools import tool_manifest  # noqa: E402

app = FastAPI(title="OpsAgent", description="IT 服务台工单自治多智能体", version="1.0.0")

_payload = json.loads((PROJECT / "data" / "world.json").read_text(encoding="utf-8"))
_tickets = {json.loads(l)["ticket_id"]: json.loads(l)
            for l in (PROJECT / "data" / "tickets.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}


class RunIn(BaseModel):
    ticket_id: str


@app.get("/tools")
def tools() -> list:
    return json.loads(tool_manifest())


@app.get("/tickets")
def tickets(limit: int = 20) -> list:
    return list(_tickets.values())[:limit]


@app.post("/run")
def run(body: RunIn) -> dict:
    t = _tickets.get(body.ticket_id)
    if not t:
        raise HTTPException(404, "工单不存在")
    world = build_world(_payload)
    agent = OpsAgent(world)
    return agent.run(t)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "tickets": len(_tickets), "tools": len(json.loads(tool_manifest()))}
