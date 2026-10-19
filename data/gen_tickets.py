"""生成仿真的 IT 服务台世界与 120 条工单（固定随机种子，结果可复现）。

说明：这是**自建仿真环境**，不是真实企业数据。每条工单都带人工标注的
期望动作序列，用于计算自动闭环率与误操作率。
"""
from __future__ import annotations

import json
import random
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
RNG = random.Random(42)

SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
GIVEN = ["伟", "芳", "娜", "敏", "静", "磊", "洋", "勇", "艳", "杰", "涛", "明", "超", "秀英", "霞"]

KB = [
    {"id": "k1", "title": "密码过期与重置", "body": "密码有效期 90 天，过期后可在自助门户重置，重置后需 5 分钟内修改临时密码。"},
    {"id": "k2", "title": "账号锁定规则", "body": "连续 5 次密码错误锁定 30 分钟，也可由服务台手工解锁。"},
    {"id": "k3", "title": "VPN 连接失败排查", "body": "先确认客户端版本，再检查本地网络与证书有效期，最后检查账号是否被禁用。"},
    {"id": "k4", "title": "资产申领流程", "body": "申领需先确认库存，由服务台分配并登记资产编号，资产不可逆转移需本人签收。"},
    {"id": "k5", "title": "邮件客户端配置", "body": "使用 IMAP 993 与 SMTP 465 加密端口，账号为域账号全称。"},
    {"id": "k6", "title": "打印机离线处理", "body": "检查队列是否卡死、驱动是否为最新版本，必要时重启打印服务。"},
    {"id": "k7", "title": "权限申请", "body": "系统权限需上级审批，服务台仅受理已审批单据。"},
    {"id": "k8", "title": "会议设备故障", "body": "先检查 HDMI 与供电，再检查投屏码是否过期。"},
]


def build_world() -> dict:
    users, assets = {}, []
    for i in range(1, 41):
        eid = f"E{i:03d}"
        status = "active"
        if i % 11 == 0:
            status = "locked"
        users[eid] = {
            "id": eid,
            "name": RNG.choice(SURNAMES) + RNG.choice(GIVEN),
            "account": f"user{i:03d}",
            "dept": RNG.choice(["研发中心", "市场部", "财务部", "人力资源部", "客户成功部"]),
            "status": status,
            "locked_until": "2026-10-19T18:00:00" if status == "locked" else None,
            "mfa": i % 3 == 0,
            "vpn": "fail" if i % 13 == 0 else "ok",
            "port": f"B2-{i}",
            "latency": 180 if i % 13 == 0 else RNG.randint(8, 40),
        }
    for j in range(30):
        assets.append({
            "asset_id": f"PC-{j:03d}",
            "type": RNG.choice(["笔记本", "显示器", "扩展坞"]),
            "status": "idle" if j % 4 else "in_use",
            "holder": None if j % 4 else f"E{RNG.randint(1, 40):03d}",
        })
    return {"users": users, "assets": {a["asset_id"]: a for a in assets}, "kb": KB}


CASES = [
    ("password_reset", "我密码过期了，登录不了 OA", ["check_account", "reset_password", "close_ticket"], False),
    ("account_locked", "账号被锁了，提示密码错误次数过多", ["check_account", "unlock_account", "close_ticket"], False),
    ("asset_request", "新同事入职需要一台笔记本", ["check_asset", "assign_asset", "close_ticket"], True),
    ("network", "连不上公司 VPN，一直转圈", ["check_network", "close_ticket"], False),
    ("software", "Outlook 收不到邮件怎么配置", ["search_kb", "close_ticket"], False),
    ("unknown", "会议室的灯一直闪，能不能派人来看", ["escalate"], False),
]


def build_tickets(world: dict, n: int = 120) -> list[dict]:
    tickets = []
    ids = list(world["users"])
    for i in range(n):
        cat, issue, expected, irreversible = CASES[i % len(CASES)]
        eid = ids[i % len(ids)]
        rec = {
            "ticket_id": f"T{i + 1:04d}",
            "employee_id": eid,
            "category": cat,
            "issue": issue,
            "expected_actions": list(expected),
            "needs_confirmation": irreversible,
        }
        # 每第 5 个资产申领单制造"资产不足"，用于验证失败自愈与改道升级
        if cat == "asset_request" and (i // len(CASES)) % 5 == 0:
            rec["force_asset_shortage"] = True
            rec["expected_actions"] = ["check_asset", "escalate"]
        tickets.append(rec)
    return tickets


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    world = build_world()
    tickets = build_tickets(world)
    (DATA / "world.json").write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8")
    with (DATA / "tickets.jsonl").open("w", encoding="utf-8") as f:
        for t in tickets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"world: {len(world['users'])} users / {len(world['assets'])} assets / {len(world['kb'])} kb")
    print(f"tickets: {len(tickets)}")


if __name__ == "__main__":
    main()
