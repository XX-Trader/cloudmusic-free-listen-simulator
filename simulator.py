"""Offline task/receipt model. Does not contact NetEase or grant real rights."""
from dataclasses import asdict, dataclass
from enum import Enum
import argparse
import json
import math
import uuid


class Method(str, Enum):
    CLICK = "click"
    CLICK_STAY = "click_stay"


class State(str, Enum):
    ISSUED = "issued"
    CLICKED = "clicked"
    WAITING = "waiting_for_receipt"
    COMPLETED = "demo_completed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Task:
    task_id: str
    method: Method
    stay_seconds: float
    reward_minutes: int
    expires_at: float


@dataclass(frozen=True)
class Receipt:
    task_id: str
    approved: bool
    reason: str
    demo_minutes: int = 0
    receipt_id: str = ""
    demo: bool = True


def valid_time(value):
    if not math.isfinite(value) or value < 0:
        raise ValueError("时间必须是有限的非负数")
    return value


class DemoServer:
    """Synthetic authority for a local demo; no HTTP or platform credentials."""

    def __init__(self):
        self.tasks = {}
        self.clicked_at = {}
        self.receipts = {}
        self.balance_minutes = 0

    def issue(self, *, now=0.0, method=Method.CLICK_STAY,
              stay_seconds=8.0, reward_minutes=30, ttl=600.0):
        valid_time(now)
        valid_time(stay_seconds)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("任务有效期必须为正数")
        if not isinstance(reward_minutes, int) or reward_minutes <= 0:
            raise ValueError("奖励分钟数必须为正整数")
        task = Task("demo-" + uuid.uuid4().hex, Method(method), stay_seconds,
                    reward_minutes, now + ttl)
        self.tasks[task.task_id] = task
        return task

    def click(self, task_id, *, now):
        valid_time(now)
        task = self.tasks.get(task_id)
        if task is None or now >= task.expires_at:
            return False
        # Repeated clicks do not rewrite the original timestamp.
        self.clicked_at.setdefault(task_id, now)
        return True

    def claim(self, task_id, *, now, reject=False):
        valid_time(now)
        if task_id in self.receipts:
            return self.receipts[task_id]
        task = self.tasks.get(task_id)
        if task is None:
            return Receipt(task_id, False, "unknown_task")
        if now >= task.expires_at:
            return Receipt(task_id, False, "task_expired")
        clicked = self.clicked_at.get(task_id)
        if clicked is None:
            return Receipt(task_id, False, "click_required")
        if now < clicked:
            return Receipt(task_id, False, "clock_moved_backwards")
        if task.method is Method.CLICK_STAY and now - clicked < task.stay_seconds:
            return Receipt(task_id, False, "insufficient_stay")
        if reject:
            return Receipt(task_id, False, "demo_server_rejected")
        receipt = Receipt(task_id, True, "demo_approved",
                          task.reward_minutes, "demo-receipt-" + uuid.uuid4().hex)
        self.receipts[task_id] = receipt
        self.balance_minutes += receipt.demo_minutes
        return receipt


class Flow:
    def __init__(self, task):
        self.task = task
        self.state = State.ISSUED
        self.receipt = None
        self.events = [self.state.value]

    def _transition(self, state):
        self.state = state
        self.events.append(state.value)

    def click(self, server, *, now):
        if self.state not in (State.ISSUED, State.CLICKED):
            raise ValueError("当前状态不允许点击")
        if not server.click(self.task.task_id, now=now):
            self._transition(State.REJECTED)
            return False
        self._transition(State.CLICKED)
        return True

    def claim(self, server, *, now, reject=False):
        if self.state is State.COMPLETED:
            return self.receipt
        self._transition(State.WAITING)
        receipt = server.claim(self.task.task_id, now=now, reject=reject)
        if receipt.task_id != self.task.task_id or not receipt.demo:
            raise ValueError("回执不属于当前演示任务")
        self.receipt = receipt
        self._transition(State.COMPLETED if receipt.approved else State.REJECTED)
        return receipt


def simulate(scenario="success"):
    server = DemoServer()
    task = server.issue(now=0, stay_seconds=8, reward_minutes=30)
    flow = Flow(task)
    flow.click(server, now=1)
    claim_at = 8.99 if scenario == "short-stay" else 9
    receipt = flow.claim(server, now=claim_at, reject=scenario == "server-rejected")
    if scenario == "duplicate":
        server.claim(task.task_id, now=10)
    return {
        "mode": "offline_demo",
        "real_entitlement_granted": False,
        "network_requests": 0,
        "scenario": scenario,
        "state": flow.state.value,
        "events": flow.events,
        "receipt": asdict(receipt),
        "demo_balance_minutes": server.balance_minutes,
        "clock": "virtual; no real advertisement was viewed",
    }


def main():
    parser = argparse.ArgumentParser(description="免费听任务流程的离线模拟器，不领取真实权益")
    parser.add_argument("--scenario", choices=(
        "success", "short-stay", "server-rejected", "duplicate"), default="success")
    args = parser.parse_args()
    print(json.dumps(simulate(args.scenario), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
