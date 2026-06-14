"""智慧治理 Demo 后端入口：FastAPI 托管 API + 静态前端。

启动：python main.py（默认 http://127.0.0.1:8000）
"""
import json
import pathlib
import time

from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).parent.parent / ".env")

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agents as agents_mod
import rag
from fusion import FusionEngine
from simulator import Simulator, load_entities
from workorder import WorkOrderManager, STATES

BASE = pathlib.Path(__file__).parent
FRONTEND = BASE.parent / "frontend"

app = FastAPI(title="超大城市智慧治理 Demo")

sim = Simulator()
fusion = FusionEngine()
orders = WorkOrderManager()

emitted_signals: list[dict] = []
timeline: dict[str, float] = {}  # 复盘用关键节点时间戳


class AskBody(BaseModel):
    question: str


class ApproveBody(BaseModel):
    event_id: str


# ---------- 初始化数据 ----------

@app.get("/api/bootstrap")
def bootstrap():
    return {
        "scenario": sim.scenario_name,
        "entities": load_entities()["entities"],
        "resources": sim.resources,
        "plans": sim.plans,
        "deepseek_enabled": bool(agents_mod.get_api_key()),
    }


# ---------- 演练回放 ----------

def _reset_all():
    sim.reset()
    fusion.reset()
    orders.reset()
    emitted_signals.clear()
    timeline.clear()


@app.post("/api/replay/start")
def replay_start():
    _reset_all()
    sim.start()
    timeline["replay_start"] = time.time()
    return {"ok": True}


@app.post("/api/replay/reset")
def replay_reset():
    _reset_all()
    return {"ok": True}


@app.get("/api/state")
def state():
    """前端轮询接口：吐出新到信号并驱动融合引擎。"""
    new_signals = sim.poll()
    for s in new_signals:
        s = dict(s)
        s["received_at"] = time.time()
        emitted_signals.append(s)
        timeline.setdefault("first_signal", s["received_at"])
        ev = fusion.ingest(s)
        if ev is not None:
            timeline.setdefault("event_created", time.time())
    done = sum(1 for o in orders.orders if o["state"] == STATES[-1])
    return {
        "running": sim.running,
        "finished": sim.finished,
        "signals": emitted_signals,
        "new_signals": new_signals,
        "events": fusion.events,
        "watching": fusion.watching_clusters(),
        "orders": orders.orders,
        "order_stats": {"total": len(orders.orders), "done": done},
        "deepseek_enabled": bool(agents_mod.get_api_key()),
    }


# ---------- 多智能体研判（SSE） ----------

@app.get("/api/agents/run")
async def agents_run(event_id: str):
    event = next((e for e in fusion.events if e["event_id"] == event_id), None)

    async def gen():
        if event is None:
            yield f"data: {json.dumps({'type': 'error', 'msg': '事件不存在'}, ensure_ascii=False)}\n\n"
            return
        timeline.setdefault("agents_start", time.time())
        event["status"] = "研判中"
        async for item in agents_mod.run_agents(event, sim.resources, sim.plans):
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        timeline["agents_done"] = time.time()
        event["status"] = "待人工审核"
        tasks = agents_mod.proposed_tasks(event)
        yield f"data: {json.dumps({'type': 'tasks', 'tasks': tasks}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- 人工审核与工单 ----------

@app.post("/api/workorders/approve")
def approve(body: ApproveBody):
    """人工审核确认后才生成工单（"AI 只做辅助决策"原则）。"""
    event = next((e for e in fusion.events if e["event_id"] == body.event_id), None)
    if event is None:
        return {"ok": False, "msg": "事件不存在"}
    event["status"] = "处置中"
    timeline.setdefault("approved", time.time())
    created = [orders.create(**t) for t in agents_mod.proposed_tasks(event)]
    return {"ok": True, "orders": created}


@app.post("/api/workorders/advance")
def advance():
    changed = orders.advance_all()
    if orders.all_done():
        timeline.setdefault("all_done", time.time())
        for e in fusion.events:
            if e["status"] == "处置中":
                e["status"] = "已办结"
    return {"ok": True, "orders": orders.orders, "all_done": orders.all_done(), "changed": len(changed)}


# ---------- 复盘 ----------

@app.get("/api/review")
def review():
    def fmt(a, b):
        if a in timeline and b in timeline:
            return round(timeline[b] - timeline[a], 1)
        return None

    return {
        "metrics": [
            {"name": "信号上报 → 事件融合生成", "seconds": fmt("first_signal", "event_created")},
            {"name": "事件生成 → 多智能体研判完成", "seconds": fmt("event_created", "agents_done")},
            {"name": "研判完成 → 人工审核通过", "seconds": fmt("agents_done", "approved")},
            {"name": "审核通过 → 全部工单办结", "seconds": fmt("approved", "all_done")},
            {"name": "全链路总耗时", "seconds": fmt("first_signal", "all_done")},
        ],
        "note": "演示时间轴按约 40 倍速压缩回放（剧情 1 分钟 ≈ 1.5 秒），真实场景对应分钟级响应链路。",
    }


# ---------- 政务问答 ----------

@app.post("/api/ask")
async def ask(body: AskBody):
    return await rag.answer(body.question)


# ---------- 静态前端 ----------

@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
