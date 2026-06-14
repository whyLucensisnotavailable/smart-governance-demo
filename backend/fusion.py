"""事件融合引擎：对多源原始信号做时空窗口聚类，融合为标准化城市事件。

对应方案中的"事件中心：融合 / 去重 / 定位 / 定级 / 标准化记录"。
"""
import math
import time
from typing import Optional

# 时空窗口参数
DISTANCE_THRESHOLD_M = 600   # 空间窗口：600 米内视为同一点位
TIME_WINDOW_MIN = 30         # 时间窗口：30 分钟内
MIN_SIGNALS_FOR_EVENT = 3    # 至少 3 条信号才升级为"事件"
MIN_CHANNELS_FOR_EVENT = 2   # 且来源渠道数 >= 2（交叉印证）


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class FusionEngine:
    """增量式聚类：每收到一条信号，尝试并入已有簇，否则新建簇。"""

    def __init__(self):
        self.clusters: list[dict] = []
        self.events: list[dict] = []
        self._event_seq = 0
        self.context_signals: list[dict] = []  # 全域性信号（如气象预警），作为事件上下文

    def reset(self):
        self.__init__()

    def ingest(self, signal: dict) -> Optional[dict]:
        """处理一条信号。若导致事件生成/升级，返回该事件，否则返回 None。"""
        # 全域信号（无精确点位含义）不参与聚类，作为上下文保留
        if signal.get("type") == "气象预警":
            self.context_signals.append(signal)
            return None

        target = None
        for c in self.clusters:
            if self._belongs(c, signal):
                target = c
                break
        if target is None:
            target = {"signals": [], "lat": signal["lat"], "lng": signal["lng"], "event_id": None}
            self.clusters.append(target)

        target["signals"].append(signal)
        # 簇中心取均值，保证后续匹配稳定
        n = len(target["signals"])
        target["lat"] = sum(s["lat"] for s in target["signals"]) / n
        target["lng"] = sum(s["lng"] for s in target["signals"]) / n

        return self._maybe_promote(target)

    def _belongs(self, cluster: dict, signal: dict) -> bool:
        d = haversine_m(cluster["lat"], cluster["lng"], signal["lat"], signal["lng"])
        if d > DISTANCE_THRESHOLD_M:
            return False
        last_t = max(s["t_offset"] for s in cluster["signals"])
        return abs(signal["t_offset"] - last_t) <= TIME_WINDOW_MIN

    def _maybe_promote(self, cluster: dict) -> Optional[dict]:
        sigs = cluster["signals"]
        channels = {s["channel"] for s in sigs}
        if len(sigs) < MIN_SIGNALS_FOR_EVENT or len(channels) < MIN_CHANNELS_FOR_EVENT:
            return None

        if cluster["event_id"] is None:
            self._event_seq += 1
            cluster["event_id"] = f"E{self._event_seq:03d}"
            event = self._build_event(cluster)
            self.events.append(event)
            return event
        # 已有事件，更新（升级定级/补充信号）
        event = next(e for e in self.events if e["event_id"] == cluster["event_id"])
        updated = self._build_event(cluster)
        updated["created_at"] = event["created_at"]
        self.events[self.events.index(event)] = updated
        return updated

    def _build_event(self, cluster: dict) -> dict:
        sigs = sorted(cluster["signals"], key=lambda s: s["t_offset"])
        channels = sorted({s["channel"] for s in sigs})
        location = max((s["location"] for s in sigs), key=lambda x: sum(1 for s in sigs if s["location"] == x))
        # 简单定级：信号数+渠道数+是否有橙色预警上下文
        score = len(sigs) + len(channels) + (2 if self.context_signals else 0)
        level = "红色（紧急）" if score >= 9 else "橙色（严重）" if score >= 7 else "黄色（关注）"
        confidence = round(min(0.98, sum(s["confidence"] for s in sigs) / len(sigs) + 0.05 * (len(channels) - 1)), 2)
        return {
            "event_id": cluster["event_id"],
            "type": "内涝风险事件",
            "title": f"{location}内涝风险事件",
            "location": location,
            "lat": round(cluster["lat"], 6),
            "lng": round(cluster["lng"], 6),
            "level": level,
            "confidence": confidence,
            "signal_ids": [s["id"] for s in sigs],
            "channels": channels,
            "context": [s["title"] for s in self.context_signals],
            "fusion_basis": (
                f"时空窗口聚类：{len(sigs)} 条信号在 {DISTANCE_THRESHOLD_M}m/{TIME_WINDOW_MIN}min 窗口内聚合，"
                f"覆盖 {len(channels)} 类渠道（{'、'.join(channels)}），多源交叉印证后升级为事件"
            ),
            "status": "待研判",
            "created_at": time.time(),
        }

    def watching_clusters(self) -> list[dict]:
        """尚未升级为事件的观察中簇（用于前端展示'观察中'状态）。"""
        out = []
        for c in self.clusters:
            if c["event_id"] is None and c["signals"]:
                out.append({
                    "location": c["signals"][-1]["location"],
                    "lat": round(c["lat"], 6),
                    "lng": round(c["lng"], 6),
                    "signal_ids": [s["id"] for s in c["signals"]],
                    "note": f"已聚合 {len(c['signals'])} 条信号，未达事件升级阈值（≥{MIN_SIGNALS_FOR_EVENT} 条且 ≥{MIN_CHANNELS_FOR_EVENT} 类渠道），持续观察",
                })
        return out
