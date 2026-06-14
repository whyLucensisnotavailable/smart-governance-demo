"""信号回放模拟器：按时间轴回放原始信号（演练加速：1 分钟 → 约 1.5 秒）。"""
import json
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
SPEEDUP_SECONDS_PER_MIN = 1.5  # 回放速度：信号时间轴上的 1 分钟对应真实 1.5 秒


def load_scenario() -> dict:
    return json.loads((DATA_DIR / "signals.json").read_text(encoding="utf-8"))


def load_entities() -> dict:
    return json.loads((DATA_DIR / "entities.json").read_text(encoding="utf-8"))


def load_policies() -> dict:
    return json.loads((DATA_DIR / "policies.json").read_text(encoding="utf-8"))


class Simulator:
    """管理一次演练回放的进度状态。"""

    def __init__(self):
        scenario = load_scenario()
        self.signals = sorted(scenario["signals"], key=lambda s: s["t_offset"])
        self.resources = scenario["resources"]
        self.plans = scenario["plans"]
        self.scenario_name = scenario["scenario"]
        self.started_at: float | None = None
        self.emitted_ids: set[str] = set()

    def start(self):
        self.started_at = time.time()
        self.emitted_ids = set()

    def reset(self):
        self.started_at = None
        self.emitted_ids = set()

    @property
    def running(self) -> bool:
        return self.started_at is not None

    def poll(self) -> list[dict]:
        """返回自启动以来、按加速时间轴应已到达但尚未发出的信号。"""
        if not self.running:
            return []
        elapsed = time.time() - self.started_at
        due = []
        for s in self.signals:
            if s["id"] in self.emitted_ids:
                continue
            if s["t_offset"] * SPEEDUP_SECONDS_PER_MIN <= elapsed:
                due.append(s)
                self.emitted_ids.add(s["id"])
        return due

    @property
    def finished(self) -> bool:
        return self.running and len(self.emitted_ids) == len(self.signals)
