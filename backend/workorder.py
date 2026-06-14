"""工单状态机：生成 → 签收 → 处置中 → 复核 → 办结。

高风险动作（如封路）只有在人工审核确认后才会生成工单，
对应方案中"AI 只做辅助决策，高风险事项保留人工审核"。
"""
import time

STATES = ["已生成", "已签收", "处置中", "复核中", "已办结"]


class WorkOrderManager:
    def __init__(self):
        self.orders: list[dict] = []
        self._seq = 0

    def reset(self):
        self.__init__()

    def create(self, event_id: str, dept: str, task: str, deadline_min: int, high_risk: bool = False) -> dict:
        self._seq += 1
        order = {
            "order_id": f"WO{self._seq:04d}",
            "event_id": event_id,
            "dept": dept,
            "task": task,
            "deadline_min": deadline_min,
            "high_risk": high_risk,
            "state": STATES[0],
            "history": [{"state": STATES[0], "at": time.time()}],
        }
        self.orders.append(order)
        return order

    def advance(self, order_id: str) -> dict | None:
        for o in self.orders:
            if o["order_id"] == order_id:
                idx = STATES.index(o["state"])
                if idx < len(STATES) - 1:
                    o["state"] = STATES[idx + 1]
                    o["history"].append({"state": o["state"], "at": time.time()})
                return o
        return None

    def advance_all(self) -> list[dict]:
        """演示用：把所有未办结工单推进一步。"""
        changed = []
        for o in self.orders:
            idx = STATES.index(o["state"])
            if idx < len(STATES) - 1:
                o["state"] = STATES[idx + 1]
                o["history"].append({"state": o["state"], "at": time.time()})
                changed.append(o)
        return changed

    def all_done(self) -> bool:
        return bool(self.orders) and all(o["state"] == STATES[-1] for o in self.orders)
