"""多智能体研判编排：应急 / 交通 / 城管 / 公众服务 四个智能体依次研判。

有 DEEPSEEK_API_KEY 时真实调用 DeepSeek（流式输出）；
无 key 时降级为内置预设文本（同样按流式逐字下发，保证演示效果一致）。
"""
import json
import os
import asyncio
from typing import AsyncGenerator

import httpx

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"


def get_api_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


AGENTS = [
    {
        "key": "emergency",
        "name": "应急智能体",
        "role": "你是城市应急管理智能体。根据内涝事件信息，匹配应急预案并判断响应等级，给出依据。",
        "task": "匹配预案、判断响应等级",
    },
    {
        "key": "traffic",
        "name": "交通智能体",
        "role": "你是交通治理智能体。根据内涝事件与路况信息，规划绕行路线和交通管控建议。",
        "task": "规划绕行、交通管控建议",
    },
    {
        "key": "cityops",
        "name": "城管智能体",
        "role": "你是城市管理智能体。根据事件位置与可用资源，给出排水力量与现场处置力量的调度建议。",
        "task": "调度排水队伍和处置力量",
    },
    {
        "key": "public",
        "name": "公众服务智能体",
        "role": "你是公众服务智能体。根据事件情况，起草面向市民的避让提醒（短信/APP推送文案）。",
        "task": "发布公众避让提醒",
    },
]

# 无 key 时的预设研判输出（与 signals.json 场景一致）
FALLBACK_OUTPUTS = {
    "emergency": (
        "【预案匹配】命中《海淀区防汛应急预案（Ⅲ级响应）》（P-FX-02）。\n"
        "依据：① 市气象台已发布暴雨橙色预警；② 知春路下穿桥积水 18cm，超过 15cm 黄色阈值且 10 分钟内上升 12cm，"
        "呈快速上涨趋势，满足 Ⅲ 级响应启动条件。\n"
        "【建议】立即启动区级 Ⅲ 级响应：排水力量前置、下穿桥封控准备、交通疏导、公众提醒同步启动。"
        "苏州街下穿通道当前 9cm 接近阈值，建议列为重点监测点位。\n"
        "【风险提示】若 1 小时内降雨量超 50mm，知春路点位可能达到 27cm 断路标准，建议提前预置封控力量。"
    ),
    "traffic": (
        "【路况研判】知春路（中关村大街—学院路段）平均车速已由 38km/h 降至 9km/h，拥堵指数 8.4，"
        "通行能力严重下降，主因下穿桥积水致车辆涉水缓行。\n"
        "【绕行方案】① 东西向车流：建议经北四环辅路或大运村路绕行；② 南北向车流：建议改走中关村东路、学院路；"
        "③ 建议在知春路两端入口（中关村大街口、学院路口）设置预告诱导屏提示。\n"
        "【管控建议】交警中关村大队（1.5km，在岗）前出至下穿桥两侧疏导；若积水达 27cm 断路标准，"
        "配合城管实施封控（高风险动作，需指挥中心人工审核）。"
    ),
    "cityops": (
        "【资源研判】可用排水力量 2 支：排水抢险三队（学院路，距离 2.1km，移动泵站×1+抽水车×1）、"
        "排水抢险一队（西北旺，距离 6.8km，大功率抽水车×2）。\n"
        "【调度建议】① 排水抢险三队立即赴知春路下穿桥抽排（最近、装备匹配）；"
        "② 排水抢险一队前置至苏州街区域待命，覆盖次生风险点；"
        "③ 城管知春路网格组（0.8km，在岗）先期到场，设置围挡和警示标志，配合断电、清理雨水箅子。\n"
        "【一物一码联动】调取知春路雨水泵站（BJ-HD-BZ-0101）运行状态，确认满负荷抽排；"
        "检查雨水井 #217/#218 是否堵塞。"
    ),
    "public": (
        "【推送文案（短信/APP）】\n"
        "海淀区防汛提示：受暴雨影响，知春路下穿桥积水较深，请途经车辆和行人绕行中关村东路、学院路；"
        "苏州街下穿通道出现积水，请减速慢行、注意安全。遇车辆涉水熄火请立即弃车至高处，切勿二次启动发动机。"
        "出行建议优先选择地铁 10/13 号线。如发现险情请拨打 12345 或通过随手拍上报。【海淀区应急管理局】\n"
        "【投放渠道】12345 短信通道、随手拍 APP 弹窗、知春路/苏州街沿线诱导屏、属地社区微信群。"
    ),
}


def _build_prompt(agent: dict, event: dict, resources: list, plans: list) -> list[dict]:
    ctx = {
        "事件": {k: event.get(k) for k in ("event_id", "title", "location", "level", "confidence", "fusion_basis", "context")},
        "可用资源": resources,
        "预案库": plans,
    }
    return [
        {"role": "system", "content": agent["role"] + " 输出控制在200字以内，分点表述，语气专业。"},
        {"role": "user", "content": "当前事件与上下文如下（JSON）：\n" + json.dumps(ctx, ensure_ascii=False, indent=1)},
    ]


async def _stream_deepseek(messages: list[dict], api_key: str) -> AsyncGenerator[str, None]:
    payload = {"model": MODEL, "messages": messages, "stream": True, "max_tokens": 600}
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", DEEPSEEK_URL, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


async def _stream_fallback(text: str) -> AsyncGenerator[str, None]:
    """无 key 时模拟流式打字效果。"""
    chunk = 6
    for i in range(0, len(text), chunk):
        yield text[i:i + chunk]
        await asyncio.sleep(0.03)


async def run_agents(event: dict, resources: list, plans: list) -> AsyncGenerator[dict, None]:
    """依次运行 4 个智能体，按 SSE 友好的 dict 流式产出。"""
    api_key = get_api_key()
    mode = "deepseek" if api_key else "fallback"
    yield {"type": "meta", "mode": mode}

    for agent in AGENTS:
        yield {"type": "agent_start", "agent": agent["name"], "key": agent["key"], "task": agent["task"]}
        try:
            if api_key:
                stream = _stream_deepseek(_build_prompt(agent, event, resources, plans), api_key)
            else:
                stream = _stream_fallback(FALLBACK_OUTPUTS[agent["key"]])
            async for delta in stream:
                yield {"type": "delta", "agent": agent["name"], "key": agent["key"], "text": delta}
        except Exception as e:
            # API 异常时切换兜底文本，保证演示不中断
            yield {"type": "delta", "agent": agent["name"], "key": agent["key"],
                   "text": f"（DeepSeek 调用异常，已切换内置研判：{type(e).__name__}）\n"}
            async for delta in _stream_fallback(FALLBACK_OUTPUTS[agent["key"]]):
                yield {"type": "delta", "agent": agent["name"], "key": agent["key"], "text": delta}
        yield {"type": "agent_end", "agent": agent["name"], "key": agent["key"]}

    yield {"type": "done"}


# 研判完成后生成的任务分工（人工审核确认后转为工单）
def proposed_tasks(event: dict) -> list[dict]:
    eid = event["event_id"]
    return [
        {"event_id": eid, "dept": "排水抢险三队（排水集团）", "task": f"赴{event['location']}实施抽排作业", "deadline_min": 30, "high_risk": False},
        {"event_id": eid, "dept": "排水抢险一队（排水集团）", "task": "前置苏州街区域待命，防范次生风险点", "deadline_min": 45, "high_risk": False},
        {"event_id": eid, "dept": "交警中关村大队", "task": "知春路两端交通疏导、发布绕行诱导", "deadline_min": 20, "high_risk": False},
        {"event_id": eid, "dept": "城管知春路网格组", "task": "现场围挡警示、清理雨水箅子；达 27cm 断路标准时实施封控", "deadline_min": 20, "high_risk": True},
        {"event_id": eid, "dept": "区应急管理局（公众服务）", "task": "通过短信/APP/诱导屏发布避让提醒", "deadline_min": 10, "high_risk": False},
    ]
