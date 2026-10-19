# OpsAgent · IT 工单处理 Agent

输入一张员工报障工单，它自己决定调哪几个工具、调完判断算不算解决；解决不了就转人工，
涉及分配资产这种不可逆操作会先挂起等人确认。

## 能做什么

- **读工单**：从自然语言描述判断类别（密码重置 / 账号解锁 / 网络 / 软件 / 资产申请）
- **调工具**：12 个工具，每个都有 JSON Schema 校验，写操作带幂等键，失败可按栈回滚
- **自我纠错**：工具报错不崩，返回结构化错误由状态机决定重试、改道还是升级
- **守边界**：不可逆动作强制人工确认；认不出的工单直接转人工，不瞎猜

## 三步跑起来

1. 双击 **`启动.bat`**。第一次会自动建环境装依赖（1-2 分钟），之后直接启动
2. 浏览器打开 <http://127.0.0.1:8012>
3. 关掉黑窗口即停止

跑评测：双击 **`评测.bat`**，120 张工单跑一遍，结果写 `data/metrics.json`。

## 实际跑一遍是什么样

`POST /run {"ticket_id": "T0001"}`，真实返回：

```json
{
  "ticket": "T0001",
  "category": "password_reset",
  "issue": "我密码过期了，登录不了 OA",
  "status": "closed",
  "steps": 3,
  "writes": ["reset_password", "close_ticket"],
  "human_checks": 0,
  "trace": ["triage→password_reset:3步", "act→check_account(ok)",
            "act→reset_password(ok)", "act→close_ticket(ok)"]
}
```

要分配资产的工单会多一步人工确认：

```json
{
  "ticket": "T0003",
  "category": "asset_request",
  "issue": "新同事入职需要一台笔记本",
  "status": "closed",
  "human_checks": 1,
  "trace": ["triage→asset_request:3步", "act→check_asset(ok)",
            "guard→assign_asset 需人工确认", "human→已确认",
            "act→assign_asset(ok)", "act→close_ticket(ok)"]
}
```

认不出来的工单不会硬做：

```json
{"ticket": "T00xx", "category": "unknown", "status": "escalated", "steps": 1,
 "trace": ["triage→unknown:1步", "act→escalate(ok)"]}
```

## 内部流程

```
        工单
         │
      triage ── 按类别拆动作序列；资产不够直接改道升级
         │
      guard ── 写操作前拦截；不可逆动作 → human 等确认
         │
       act ── 执行工具（JSON Schema 校验 + 幂等键）
         │
      verify ── 成功 → 下一步
         │        失败 → recover：业务性失败先回滚再升级，瞬时失败重试一次
         │
      finalize ── 关单 或 升级人工
```

写操作执行前会把前置状态记下来，失败时按调用栈倒序执行补偿函数，不会留下改了一半的数据。

幂等键是 `ticket_id:step:工具名`，同一张工单重放不会产生第二次副作用。

## 接口

```bash
curl -s http://127.0.0.1:8012/tools          # 看 12 个工具的 schema
curl -s -X POST http://127.0.0.1:8012/run \
  -H 'Content-Type: application/json' -d '{"ticket_id": "T0001"}'
```

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/run` | 跑一张工单 |
| GET | `/tickets` | 列出仿真工单 |
| GET | `/tools` | 工具清单与入参 schema |

## 指标

120 张仿真工单：

| 项 | 数值 |
|---|---|
| 自动关单 | 96 / 120 = 80.0%（Wilson 95% 下界 72.0%） |
| 转人工 | 24 / 120 = 20.0% |
| 越权写操作 | 0 / 120 |
| 人工确认 | 16 次 |
| 平均步数 | 2.3 |
| p95 延迟 | 5~9 ms |

| 类别 | 单量 | 关单 | 升级 | 均步数 |
|---|---|---|---|---|
| password_reset | 20 | 20 | 0 | 3.0 |
| account_locked | 20 | 20 | 0 | 3.0 |
| network | 20 | 20 | 0 | 2.0 |
| software | 20 | 20 | 0 | 2.0 |
| asset_request | 20 | 16 | 4 | 2.8 |
| unknown | 20 | 0 | 20 | 1.0 |

升级的 24 张里 20 张是 unknown（认不出来直接转人工），4 张是资产库存不够。

**越权写操作 0 次不能当成模型效果**：triage 现在是规则匹配，在标注好的类别上本来就不会乱调工具。
换成 LLM 做 triage 之后这个数会涨，那时候它才能反映模型好坏。

## 调过的一个坑

第一版跑出来 13.3% 的误操作，看着像 Agent 乱来。查下来是评测脚本的问题：120 张工单共用一个
world 对象，前面的工单把资产领走了，后面的去领就报"库存不足"，被算成错误操作。

改成每张工单进来先 `deepcopy` 一份 world 之后，误操作降到 0。所以 `build_world()` 里那句
deepcopy 不是保险，是必须的。

## 已知问题

- world 是仿真数据：40 个员工、30 台资产、8 条知识库，不是真实企业数据；
- 工单文本是模板拼的，比真实报障规整得多；
- human 节点用函数模拟审批，没接 IM 卡片回调；
- triage 是规则不是模型，所以"LLM 处理能力"这件事还没被真正验证。

## 目录

```
启动.bat / 评测.bat      一键启动、一键评测
app/graph.py            状态机（LangGraph StateGraph）
app/tools.py            12 个工具、幂等表、回滚
app/server.py           HTTP 接口
data/gen_tickets.py     生成 world 和 120 张工单（seed=42）
data/world.json         仿真世界（已生成，可直接用）
data/tickets.jsonl      120 张工单
eval/run_eval.py        评测
```


