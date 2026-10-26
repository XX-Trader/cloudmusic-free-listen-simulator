"""免费听任务的纯离线模拟器，不连接网易云或发放真实权益。"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from enum import Enum
import json
import math
import uuid


TASK_ISSUED_AT = 0.0
DEFAULT_AD_STARTED_AT = 0.5
DEFAULT_CLICK_AT = 1.0
DEFAULT_STAY_SECONDS = 8.0
DEFAULT_REWARD_MINUTES = 30
DEFAULT_TASK_TTL = 600.0
RECOVERABLE_REJECTIONS = frozenset({"insufficient_stay", "click_required"})


class Method(str, Enum):
    """定义演示任务的完成方式。"""

    CLICK = "click"
    CLICK_STAY = "click_stay"


class State(str, Enum):
    """定义客户端演示流程状态。"""

    ISSUED = "issued"
    AD_STARTED = "ad_started"
    CLICKED = "clicked"
    DWELLING = "dwelling"
    WAITING = "waiting_for_receipt"
    COMPLETED = "demo_completed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Task:
    """描述本地演示服务签发的任务。"""

    task_id: str
    method: Method
    stay_seconds: float
    reward_minutes: int
    expires_at: float


@dataclass(frozen=True)
class Receipt:
    """描述本地演示服务返回的领取回执。"""

    task_id: str
    approved: bool
    reason: str
    demo_minutes: int = 0
    receipt_id: str = ""
    demo: bool = True


@dataclass(frozen=True)
class ScenarioSpec:
    """描述一个自动模拟场景的虚拟时间和服务行为。"""

    click_at: float
    claim_at: float
    reject_claim: bool = False
    duplicate_claim_at: float | None = None
    ad_started_at: float = DEFAULT_AD_STARTED_AT


@dataclass(frozen=True)
class AuditEvent:
    """描述自动模拟器产生的一条结构化审计事件。"""

    sequence: int
    virtual_time: float
    action: str
    outcome: str
    detail: str


SCENARIOS: dict[str, ScenarioSpec] = {
    "success": ScenarioSpec(click_at=DEFAULT_CLICK_AT, claim_at=9.0),
    "short-stay": ScenarioSpec(click_at=DEFAULT_CLICK_AT, claim_at=8.99),
    "server-rejected": ScenarioSpec(
        click_at=DEFAULT_CLICK_AT,
        claim_at=9.0,
        reject_claim=True,
    ),
    "duplicate": ScenarioSpec(
        click_at=DEFAULT_CLICK_AT,
        claim_at=9.0,
        duplicate_claim_at=10.0,
    ),
}


def valid_time(value: float) -> float:
    """校验虚拟时间；参数为秒数；返回原值；负数或非有限值抛出 ValueError。"""
    if not math.isfinite(value) or value < 0:
        raise ValueError("时间必须是有限的非负数")
    return value


class DemoServer:
    """提供不含网络与真实凭证的本地演示任务权威。"""

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.clicked_at: dict[str, float] = {}
        self.receipts: dict[str, Receipt] = {}
        self.balance_minutes = 0

    def issue(
        self,
        *,
        now: float = TASK_ISSUED_AT,
        method: Method = Method.CLICK_STAY,
        stay_seconds: float = DEFAULT_STAY_SECONDS,
        reward_minutes: int = DEFAULT_REWARD_MINUTES,
        ttl: float = DEFAULT_TASK_TTL,
    ) -> Task:
        """签发演示任务；参数定义规则；返回 Task；非法时间或奖励抛出 ValueError。"""
        valid_time(now)
        valid_time(stay_seconds)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("任务有效期必须为正数")
        if not isinstance(reward_minutes, int) or reward_minutes <= 0:
            raise ValueError("奖励分钟数必须为正整数")
        task = Task(
            task_id="demo-" + uuid.uuid4().hex,
            method=Method(method),
            stay_seconds=stay_seconds,
            reward_minutes=reward_minutes,
            expires_at=now + ttl,
        )
        self.tasks[task.task_id] = task
        return task

    def click(self, task_id: str, *, now: float) -> bool:
        """记录首次模拟点击；参数为任务和时间；返回是否接受；非法时间抛出 ValueError。"""
        valid_time(now)
        task = self.tasks.get(task_id)
        if task is None or now >= task.expires_at:
            return False
        self.clicked_at.setdefault(task_id, now)
        return True

    def claim(self, task_id: str, *, now: float, reject: bool = False) -> Receipt:
        """领取演示奖励；参数为任务、时间和拒绝开关；返回 Receipt；非法时间抛出 ValueError。"""
        valid_time(now)
        if task_id in self.receipts:
            return self.receipts[task_id]
        task = self.tasks.get(task_id)
        if task is None:
            return Receipt(task_id, False, "unknown_task")
        if now >= task.expires_at:
            return Receipt(task_id, False, "task_expired")
        clicked_at = self.clicked_at.get(task_id)
        if clicked_at is None:
            return Receipt(task_id, False, "click_required")
        if now < clicked_at:
            return Receipt(task_id, False, "clock_moved_backwards")
        if task.method is Method.CLICK_STAY and now - clicked_at < task.stay_seconds:
            return Receipt(task_id, False, "insufficient_stay")
        if reject:
            return Receipt(task_id, False, "demo_server_rejected")
        receipt = Receipt(
            task_id=task_id,
            approved=True,
            reason="demo_approved",
            demo_minutes=task.reward_minutes,
            receipt_id="demo-receipt-" + uuid.uuid4().hex,
        )
        self.receipts[task_id] = receipt
        self.balance_minutes += receipt.demo_minutes
        return receipt


class Flow:
    """管理单个演示任务的客户端状态转换。"""

    def __init__(self, task: Task) -> None:
        self.task = task
        self.state = State.ISSUED
        self.receipt: Receipt | None = None
        self.events = [self.state.value]
        self._last_time: float | None = None
        self._last_rejection: str | None = None

    def _require_state(self, action: str, allowed: tuple[State, ...]) -> None:
        if self.state not in allowed:
            allowed_names = ", ".join(state.value for state in allowed)
            raise ValueError(
                f"状态 {self.state.value} 不允许 {action}；允许状态：{allowed_names}"
            )

    def _transition(self, state: State) -> None:
        self.state = state
        self.events.append(state.value)

    def _advance_time(self, now: float, action: str) -> None:
        """校验并推进虚拟时间；时间倒退直接抛出 ValueError。"""
        valid_time(now)
        if self._last_time is not None and now < self._last_time:
            raise ValueError(
                f"{action}的时间 {now} 早于上一步的 {self._last_time}，流程时间必须单调"
            )
        self._last_time = now

    def _claim_states(self) -> tuple[State, ...]:
        """返回当前允许发起领取的状态；停留任务必须经过停留阶段。"""
        if self.state is State.REJECTED:
            if self._last_rejection not in RECOVERABLE_REJECTIONS:
                raise ValueError(
                    f"任务已因 {self._last_rejection} 终态拒绝，不能再次领取"
                )
            return (State.REJECTED,)
        if self.task.method is Method.CLICK:
            return (State.CLICKED,)
        return (State.DWELLING,)

    def start_ad(self, *, now: float) -> None:
        """开始模拟广告；参数为虚拟时间；无返回值；非法状态或时间抛出 ValueError。"""
        self._require_state("开始广告", (State.ISSUED,))
        self._advance_time(now, "开始广告")
        self._transition(State.AD_STARTED)

    def click(self, server: DemoServer, *, now: float) -> bool:
        """提交模拟点击；参数为演示服务和时间；返回是否接受；非法状态或时间抛出 ValueError。"""
        self._require_state("点击", (State.AD_STARTED, State.CLICKED))
        self._advance_time(now, "点击")
        if not server.click(self.task.task_id, now=now):
            self._transition(State.REJECTED)
            return False
        self._transition(State.CLICKED)
        return True

    def begin_dwell(self, *, now: float) -> None:
        """开始虚拟停留；参数为虚拟时间；无返回值；非法状态或时间抛出 ValueError。"""
        self._require_state("开始停留", (State.CLICKED,))
        self._advance_time(now, "开始停留")
        self._transition(State.DWELLING)

    def claim(
        self,
        server: DemoServer,
        *,
        now: float,
        reject: bool = False,
    ) -> Receipt:
        """提交演示领取；参数为服务、时间和拒绝开关；返回 Receipt；非法状态抛出 ValueError。"""
        if self.state is State.COMPLETED:
            if self.receipt is None:
                raise RuntimeError("完成状态缺少演示回执")
            return self.receipt
        self._require_state("领取", self._claim_states())
        self._advance_time(now, "领取")
        previous_state = self.state
        self._transition(State.WAITING)
        try:
            receipt = server.claim(self.task.task_id, now=now, reject=reject)
            if receipt.task_id != self.task.task_id or not receipt.demo:
                raise ValueError("回执不属于当前演示任务")
        except Exception:
            self._transition(previous_state)
            raise
        self.receipt = receipt
        self._last_rejection = None if receipt.approved else receipt.reason
        self._transition(State.COMPLETED if receipt.approved else State.REJECTED)
        return receipt


class AutoRunner:
    """按场景配置自动执行完整的本地演示流程。"""

    def __init__(self, server: DemoServer) -> None:
        self.server = server
        self.audit_log: list[AuditEvent] = []

    def _record(
        self,
        virtual_time: float,
        action: str,
        outcome: str,
        detail: str,
    ) -> None:
        valid_time(virtual_time)
        self.audit_log.append(
            AuditEvent(
                sequence=len(self.audit_log) + 1,
                virtual_time=virtual_time,
                action=action,
                outcome=outcome,
                detail=detail,
            )
        )

    @staticmethod
    def _validate_specification(specification: ScenarioSpec) -> None:
        """校验场景时间配置；顺序非法直接抛出 ValueError。"""
        if not specification.ad_started_at <= specification.click_at:
            raise ValueError("广告开始时间不得晚于点击时间")
        if not specification.click_at <= specification.claim_at:
            raise ValueError("点击时间不得晚于领取时间")
        duplicate_at = specification.duplicate_claim_at
        if duplicate_at is not None and duplicate_at < specification.claim_at:
            raise ValueError("重复领取时间不得早于首次领取时间")

    def _open_advertisement(self, flow: Flow, specification: ScenarioSpec) -> None:
        flow.start_ad(now=specification.ad_started_at)
        self._record(
            specification.ad_started_at,
            "ad_started",
            "accepted",
            "offline_simulation",
        )

    def _click_advertisement(self, flow: Flow, specification: ScenarioSpec) -> None:
        if not flow.click(self.server, now=specification.click_at):
            raise RuntimeError("演示任务在配置的点击时间已失效")
        self._record(
            specification.click_at,
            "ad_clicked",
            "accepted",
            "first_click_recorded",
        )

    def _start_dwell(
        self, flow: Flow, specification: ScenarioSpec, task: Task
    ) -> None:
        flow.begin_dwell(now=specification.click_at)
        self._record(
            specification.click_at,
            "dwell_started",
            "accepted",
            f"required_seconds={task.stay_seconds:g}",
        )

    def _claim_reward(
        self, flow: Flow, specification: ScenarioSpec
    ) -> Receipt:
        receipt = flow.claim(
            self.server,
            now=specification.claim_at,
            reject=specification.reject_claim,
        )
        self._record(
            specification.claim_at,
            "reward_claimed",
            "approved" if receipt.approved else "rejected",
            receipt.reason,
        )
        return receipt

    def _replay_claim(
        self, flow: Flow, specification: ScenarioSpec, receipt: Receipt
    ) -> None:
        if specification.duplicate_claim_at is None:
            return
        repeated_receipt = flow.claim(
            self.server,
            now=specification.duplicate_claim_at,
        )
        if repeated_receipt != receipt:
            raise RuntimeError("重复领取未返回首次演示回执")
        self._record(
            specification.duplicate_claim_at,
            "reward_reclaimed",
            "idempotent",
            repeated_receipt.reason,
        )

    def _build_result(
        self, scenario: str, flow: Flow, receipt: Receipt
    ) -> dict[str, object]:
        return {
            "mode": "offline_demo",
            "real_entitlement_granted": False,
            "network_requests": 0,
            "scenario": scenario,
            "state": flow.state.value,
            "events": flow.events,
            "audit_log": [asdict(event) for event in self.audit_log],
            "automation": {
                "scope": "local_demo_only",
                "simulated_ad_started": True,
                "simulated_click_executed": True,
                "simulated_claim_executed": True,
            },
            "receipt": asdict(receipt),
            "demo_balance_minutes": self.server.balance_minutes,
            "clock": "virtual; no real advertisement was viewed",
        }

    def run(self, scenario: str, specification: ScenarioSpec) -> dict[str, object]:
        """运行自动场景；参数为名称和配置；返回离线结果；流程不一致时抛出异常。"""
        self._validate_specification(specification)
        self.audit_log = []
        task = self.server.issue(
            now=TASK_ISSUED_AT,
            stay_seconds=DEFAULT_STAY_SECONDS,
            reward_minutes=DEFAULT_REWARD_MINUTES,
        )
        flow = Flow(task)
        self._record(TASK_ISSUED_AT, "task_issued", "accepted", task.method.value)
        self._open_advertisement(flow, specification)
        self._click_advertisement(flow, specification)
        self._start_dwell(flow, specification, task)
        receipt = self._claim_reward(flow, specification)
        self._replay_claim(flow, specification, receipt)
        return self._build_result(scenario, flow, receipt)


def simulate(scenario: str = "success") -> dict[str, object]:
    """执行指定离线场景；参数为场景名；返回结果字典；未知场景抛出 ValueError。"""
    specification = SCENARIOS.get(scenario)
    if specification is None:
        raise ValueError(f"未知演示场景：{scenario}")
    return AutoRunner(DemoServer()).run(scenario, specification)


def main() -> int:
    """解析 CLI 并输出离线结果；无参数；成功返回 0；参数错误由 argparse 终止。"""
    parser = argparse.ArgumentParser(
        description="免费听任务流程的离线自动模拟器，只发放演示权益"
    )
    parser.add_argument(
        "--scenario",
        choices=tuple(SCENARIOS),
        default="success",
    )
    arguments = parser.parse_args()
    print(json.dumps(simulate(arguments.scenario), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
