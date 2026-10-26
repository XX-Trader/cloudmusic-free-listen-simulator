"""通过 ADB 驱动官方客户端完成免费听任务，并验证权益到账与 VIP 播放。

本模块只操作网易云客户端的可见界面：启动应用、点击控件、按键返回。
不构造、不重放任何接口请求，不读取客户端私有数据目录，不提取账号凭证。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.parse
import xml.etree.ElementTree as ElementTree

import account_gate
import vip_probe


APP_PACKAGE = "com.netease.cloudmusic"


def default_adb_path() -> str:
    """返回默认 ADB 路径：优先当前用户目录下的 platform-tools，其次 PATH 中的 adb。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = (
            pathlib.Path(local_app_data) / "Android" / "platform-tools" / "adb.exe"
        )
        if candidate.exists():
            return str(candidate)
    return "adb"


DEFAULT_ADB = default_adb_path()

TEXT_FREE_LISTEN_TAB = "免费听"
TEXT_CLAIM_30_MINUTES = "领30分钟"
TEXT_WATCH_AD = "看广告"
TEXT_WATCH_AD_CARD = "看广告 免费听"
TEXT_FIRST_AD = "看第1个广告"
TEXT_OPEN_LANDING = "进入落地页查看详情"
TEXT_STAY_HINT = "点击后停留"
TEXT_TASK_DONE = "完成广告任务"
TEXT_ALLOW = "允许"
TEXT_DENY = "拒绝"

# 原生广告视频页正在播放视频，uiautomator 拿不到空闲态，无法导出界面结构。
# 登记该页“进入落地页查看详情”的稳定位置作为兜底点击点。
AD_ACTIVITY_MARKERS = ("AdMotivation", "AdEmbedBrowser", "MainProcessAd")
LANDING_FALLBACK_POINT = (612, 2393)
CTA_MAX_WIDTH = 600
SCROLL_FROM_Y = 1900
SCROLL_TO_Y = 900
SCROLL_DURATION_MS = 400

DEFAULT_STAY_SECONDS = 8
DEFAULT_WAIT_SECONDS = 90
DEFAULT_POLL_SECONDS = 5
APP_LAUNCH_TIMEOUT = 30
DUMP_RETRIES = 3
TAP_ATTEMPTS = 6
TAP_RETRY_SECONDS = 2
MAX_PHONE_STEPS = 12
MAX_BACK_PRESSES = 5
COMMAND_TIMEOUT = 60


class AutomationError(RuntimeError):
    """描述手机端自动化过程中的可定位失败。"""


@dataclass(frozen=True)
class UiNode:
    """描述界面结构中的一个节点。"""

    text: str
    description: str
    resource_id: str
    class_name: str
    bounds: str

    @property
    def center(self) -> tuple[int, int]:
        """返回节点中心坐标；bounds 非法时返回原点。"""
        numbers = [int(value) for value in re.findall(r"-?\d+", self.bounds)]
        if len(numbers) < 4:
            return (0, 0)
        left, top, right, bottom = numbers[:4]
        return ((left + right) // 2, (top + bottom) // 2)

    def label(self) -> str:
        """返回用于日志与定位的可读标签。"""
        return self.text or self.description or self.resource_id or self.class_name


@dataclass
class Step:
    """记录自动化过程中的一个步骤。"""

    name: str
    outcome: str
    detail: str = ""


@dataclass
class AutomationResult:
    """汇总一次手机端自动化的结论。"""

    mode: str = "device_automation"
    device: str = ""
    steps: list[Step] = field(default_factory=list)
    baseline_rights: int = 0
    rights_delta: int = 0
    entitlement_detected: bool = False
    vip_playback_verified: bool = False
    gate: dict | None = None
    probe: dict | None = None
    exit_code: int = 0

    def to_dict(self) -> dict[str, object]:
        """转换为可输出字典；不包含凭证与设备标识。"""
        return {
            "mode": self.mode,
            "device": self.device,
            "steps": [asdict(step) for step in self.steps],
            "baseline_rights": self.baseline_rights,
            "rights_delta": self.rights_delta,
            "entitlement_detected": self.entitlement_detected,
            "vip_playback_verified": self.vip_playback_verified,
            "gate": self.gate,
            "probe": self.probe,
            "exit_code": self.exit_code,
        }


def _bounds_area(bounds: str) -> int:
    """返回控件边界面积；解析失败时返回极大值以便排到末尾。"""
    numbers = [int(value) for value in re.findall(r"-?\d+", bounds)]
    if len(numbers) < 4:
        return 10**12
    left, top, right, bottom = numbers[:4]
    return max(0, right - left) * max(0, bottom - top)


def _bounds_top(bounds: str) -> int:
    """返回控件边界顶部坐标；解析失败时返回 0。"""
    numbers = [int(value) for value in re.findall(r"-?\d+", bounds)]
    return numbers[1] if len(numbers) >= 4 else 0


def _bounds_width(bounds: str) -> int:
    """返回控件边界宽度；解析失败时返回极大值。"""
    numbers = [int(value) for value in re.findall(r"-?\d+", bounds)]
    if len(numbers) < 4:
        return 10**12
    return abs(numbers[2] - numbers[0])


def parse_top_activity(dumps_output: str) -> str:
    """从 dumpsys 输出中解析前台 Activity 组件名；解析不出时返回空串。"""
    for pattern in (
        r"topResumedActivity=ActivityRecord\{([^}]*)\}",
        r"mResumedActivity: ActivityRecord\{([^}]*)\}",
    ):
        match = re.search(pattern, dumps_output)
        if match is None:
            continue
        for token in match.group(1).split():
            if "/" in token:
                return token.strip()
    return ""


class AdbDevice:
    """封装 adb 命令调用与界面读取。"""

    def __init__(self, adb_path: str = DEFAULT_ADB, serial: str | None = None):
        self.adb_path = adb_path
        self.serial = serial

    def _base_command(self) -> list[str]:
        command = [self.adb_path]
        if self.serial:
            command += ["-s", self.serial]
        return command

    def run(self, arguments: list[str], *, timeout: int = COMMAND_TIMEOUT) -> str:
        """执行 adb 命令并返回标准输出；失败时抛出 AutomationError。"""
        completed = subprocess.run(
            self._base_command() + arguments,
            capture_output=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            raise AutomationError(f"adb {' '.join(arguments)} 失败：{error}")
        return completed.stdout.decode("utf-8", errors="replace")

    def list_devices(self) -> list[tuple[str, str]]:
        """列出处于 device 状态的设备；返回序列号与型号二元组。"""
        output = self.run(["devices", "-l"], timeout=20)
        devices: list[tuple[str, str]] = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2 or parts[1] != "device":
                continue
            model = ""
            for token in parts[2:]:
                if token.startswith("model:"):
                    model = token.split(":", 1)[1]
            devices.append((parts[0], model))
        return devices

    def select_device(self) -> str:
        """选择目标设备序列号；没有可用设备时抛出 AutomationError。"""
        if self.serial:
            return self.serial
        devices = self.list_devices()
        if not devices:
            raise AutomationError("没有处于 device 状态的设备；请确认无线调试已连接")
        self.serial = devices[0][0]
        return self.serial

    def shell(self, command: str, *, timeout: int = COMMAND_TIMEOUT) -> str:
        """在设备上执行 shell 命令并返回输出。"""
        return self.run(["shell", command], timeout=timeout)

    def exec_out(self, command: str, *, timeout: int = COMMAND_TIMEOUT) -> bytes:
        """执行 exec-out 命令并返回原始字节。"""
        completed = subprocess.run(
            self._base_command() + ["exec-out", command],
            capture_output=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            raise AutomationError(f"adb exec-out {command} 失败：{error}")
        return completed.stdout

    def screencap(self) -> bytes:
        """抓取屏幕图像。"""
        return self.exec_out("screencap -p")

    def current_activity(self) -> str:
        """返回当前前台 Activity 全名。"""
        return parse_top_activity(self.shell("dumpsys activity activities"))

    def is_app_foreground(self) -> bool:
        """判断网易云是否处于前台。"""
        return APP_PACKAGE in self.current_activity()

    def tap(self, x: int, y: int) -> None:
        """点击屏幕坐标。"""
        self.shell(f"input tap {x} {y}")

    def press_back(self) -> None:
        """发送返回键。"""
        self.shell("input keyevent KEYCODE_BACK")

    def launch_app(self) -> None:
        """启动网易云主界面。"""
        self.shell(
            f"monkey -p {APP_PACKAGE} -c android.intent.category.LAUNCHER 1"
        )

    def dump_ui(self) -> list[UiNode]:
        """读取当前界面结构；导出失败时重试，仍失败则抛出 AutomationError。"""
        last_error = ""
        for attempt in range(1, DUMP_RETRIES + 1):
            remote = f"/sdcard/claude-ui-{int(time.time() * 1000) % 100000}.xml"
            try:
                output = self.shell(f"uiautomator dump --compressed {remote}")
                if "dumped to" not in output:
                    last_error = output.strip() or "界面导出失败"
                    time.sleep(1)
                    continue
                raw = self.exec_out(f"cat {remote}")
            except AutomationError as error:
                last_error = str(error)
                time.sleep(1)
                continue
            try:
                root = ElementTree.fromstring(raw.decode("utf-8", errors="replace"))
            except ElementTree.ParseError as error:
                last_error = f"界面结构解析失败：{error}"
                time.sleep(1)
                continue
            return [
                UiNode(
                    text=node.attrib.get("text", ""),
                    description=node.attrib.get("content-desc", ""),
                    resource_id=node.attrib.get("resource-id", ""),
                    class_name=node.attrib.get("class", ""),
                    bounds=node.attrib.get("bounds", ""),
                )
                for node in root.iter("node")
            ]
        raise AutomationError(f"无法读取界面结构：{last_error}")


def find_node(
    nodes: list[UiNode],
    *,
    text: str | None = None,
    description: str | None = None,
    resource_id: str | None = None,
    contains: bool = False,
) -> UiNode | None:
    """按文案、描述或资源标识定位节点；命中返回第一个匹配项。"""
    for node in nodes:
        if text is not None:
            haystack = node.text
            matched = haystack == text if not contains else text in haystack
            if matched:
                return node
        if description is not None:
            haystack = node.description
            matched = haystack == description if not contains else description in haystack
            if matched:
                return node
        if resource_id is not None and node.resource_id == resource_id:
            return node
    return None


class FreeListenFlow:
    """编排手机端免费听任务与到账验证。"""

    def __init__(
        self,
        device: AdbDevice,
        *,
        stay_seconds: int = DEFAULT_STAY_SECONDS,
        wait_seconds: int = DEFAULT_WAIT_SECONDS,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
        sleeper=time.sleep,
    ):
        self.device = device
        self.stay_seconds = max(DEFAULT_STAY_SECONDS, stay_seconds)
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds
        self.sleeper = sleeper
        self.steps: list[Step] = []

    def _record(self, name: str, outcome: str, detail: str = "") -> None:
        self.steps.append(Step(name=name, outcome=outcome, detail=detail))

    def _tap_text(
        self,
        name: str,
        candidates: tuple[str, ...],
        *,
        contains: bool = False,
        attempts: int = TAP_ATTEMPTS,
    ) -> UiNode:
        """在候选文案中依次查找并点击；控件未渲染时重试；仍未命中时抛出 AutomationError。"""
        nodes: list[UiNode] = []
        for attempt in range(1, attempts + 1):
            nodes = self.device.dump_ui()
            for candidate in candidates:
                node = find_node(nodes, text=candidate, contains=contains)
                if node is None:
                    node = find_node(nodes, description=candidate, contains=contains)
                if node is not None:
                    x, y = node.center
                    self.device.tap(x, y)
                    self._record(name, "ok", f"{node.label()} @ {x},{y}")
                    return node
            if attempt < attempts:
                self.sleeper(TAP_RETRY_SECONDS)
        visible = [item.label() for item in nodes if item.label()][:12]
        raise AutomationError(
            f"未找到控件：{' / '.join(candidates)}；当前可见文案：{' | '.join(visible)}"
        )

    def wake_and_unlock(self) -> None:
        """唤醒屏幕并上滑解锁；仍处于锁屏时如实报错。"""
        self.device.shell("input keyevent KEYCODE_WAKEUP")
        self.sleeper(2)
        self.device.shell(f"input swipe 600 {SCROLL_FROM_Y} 600 {SCROLL_TO_Y} 300")
        self.sleeper(2)
        focus = self.device.shell("dumpsys window")
        if "mDreamingLockscreen=true" in focus or "mShowingLockscreen=true" in focus:
            raise AutomationError("手机处于锁屏状态，需要人工解锁后再运行")

    def open_app(self) -> None:
        """启动网易云并等待其进入前台。"""
        self.wake_and_unlock()
        self.device.launch_app()
        deadline = time.monotonic() + APP_LAUNCH_TIMEOUT
        while time.monotonic() < deadline:
            activity = self.device.current_activity()
            if APP_PACKAGE in activity:
                self._record("启动客户端", "ok", activity)
                return
            self.sleeper(1)
        raise AutomationError("网易云未在超时内进入前台")

    def _find_cta(self, nodes: list[UiNode]) -> UiNode | None:
        """定位“看广告”入口；同名节点取面积最小者，避免点到页面标题。"""
        for candidate, exact in (
            (TEXT_WATCH_AD_CARD, True),
            (TEXT_CLAIM_30_MINUTES, True),
            (TEXT_WATCH_AD, False),
        ):
            matches = [
                item
                for item in nodes
                if (item.text == candidate if exact else candidate in item.text)
            ]
            if matches:
                return min(matches, key=lambda item: _bounds_area(item.bounds))
        return None

    def _is_task_page(self, nodes: list[UiNode]) -> bool:
        """判断当前界面是否已经是广告任务页。"""
        for node in nodes:
            haystack = node.text or node.description
            if TEXT_STAY_HINT in haystack or TEXT_OPEN_LANDING in haystack:
                return True
        return False

    def _scroll_down(self) -> None:
        """向下滑动一屏，露出下一张广告卡。"""
        self.device.shell(
            f"input swipe 600 {SCROLL_FROM_Y} 600 {SCROLL_TO_Y} {SCROLL_DURATION_MS}"
        )
        self.sleeper(2)

    def _is_ad_cards_page(self, nodes: list[UiNode]) -> bool:
        """判断当前是否处于广告选择页（同一文案同时出现在标题与按钮上）。"""
        return any(item.text == TEXT_WATCH_AD_CARD for item in nodes)

    def _select_ad_cta(self, nodes: list[UiNode]) -> UiNode | None:
        """在广告选择页挑选按钮；取最靠下的一个，对应下方那张卡片。"""
        matches = [
            item
            for item in nodes
            if item.text == TEXT_WATCH_AD_CARD
            and _bounds_width(item.bounds) <= CTA_MAX_WIDTH
        ]
        if matches:
            return max(matches, key=lambda item: _bounds_top(item.bounds))
        return self._find_cta(nodes)

    def _is_ad_page(self) -> bool:
        """判断当前是否处于原生广告页。"""
        activity = self.device.current_activity()
        return any(marker in activity for marker in AD_ACTIVITY_MARKERS)

    def _drive_phone_flow(self) -> None:
        """按界面状态推进手机端流程，直到进入落地页或无已知控件。"""
        last_activity = ""
        for _ in range(MAX_PHONE_STEPS):
            try:
                nodes = self.device.dump_ui()
            except AutomationError:
                nodes = []
            if self._is_ad_cards_page(nodes):
                self._scroll_down()
                try:
                    nodes = self.device.dump_ui()
                except AutomationError:
                    nodes = []
            done = find_node(nodes, text=TEXT_TASK_DONE, contains=True)
            if done is not None:
                self._record("广告任务已完成", "ok", done.label())
                break
            allow = find_node(nodes, text=TEXT_ALLOW)
            if allow is not None:
                x, y = allow.center
                self.device.tap(x, y)
                self._record("系统弹窗", "ok", f"允许 @ {x},{y}")
                self.sleeper(3)
                continue
            landing = find_node(nodes, text=TEXT_OPEN_LANDING)
            if landing is not None:
                x, y = landing.center
                self.device.tap(x, y)
                self._record("点击落地页入口", "ok", f"{landing.label()} @ {x},{y}")
                self.sleeper(3)
                continue
            if not nodes and self._is_ad_page():
                x, y = LANDING_FALLBACK_POINT
                self.device.tap(x, y)
                self._record("点击落地页入口", "ok", f"坐标兜底 @ {x},{y}")
                self.sleeper(3)
                continue
            tab = find_node(nodes, text=TEXT_FREE_LISTEN_TAB)
            if tab is not None:
                x, y = tab.center
                self.device.tap(x, y)
                self._record("切换到免费听", "ok", f"免费听 @ {x},{y}")
                self.sleeper(2)
                continue
            claim = find_node(nodes, text=TEXT_CLAIM_30_MINUTES)
            if claim is not None:
                x, y = claim.center
                self.device.tap(x, y)
                self._record("打开广告任务", "ok", f"{claim.label()} @ {x},{y}")
                self.sleeper(3)
                continue
            cta = self._select_ad_cta(nodes)
            if cta is not None:
                x, y = cta.center
                self.device.tap(x, y)
                self._record("打开广告任务", "ok", f"{cta.label()} @ {x},{y}")
                self.sleeper(3)
                continue
            if APP_PACKAGE not in self.device.current_activity():
                break
            activity = self.device.current_activity()
            if activity == last_activity:
                break
            last_activity = activity
            self.device.press_back()
            self.sleeper(2)
        else:
            raise AutomationError("未能在限定步骤内进入广告落地页")

    def complete_ad_task(self) -> None:
        """完成广告任务：推进界面状态、停留并返回客户端。"""
        self._drive_phone_flow()
        self.sleeper(self.stay_seconds)
        self._record("落地页停留", "ok", f"{self.stay_seconds} 秒")
        self._return_to_app()

    def _return_to_app(self) -> None:
        """有界返回网易云主界面。"""
        for attempt in range(1, MAX_BACK_PRESSES + 1):
            if self.device.is_app_foreground():
                self._record("返回客户端", "ok", f"{attempt - 1} 次返回")
                return
            self.device.press_back()
            self.sleeper(2)
        raise AutomationError("未能返回网易云界面")

    def wait_entitlement(self, session: vip_probe.ProbeSession) -> dict:
        """轮询等待权益到账；返回门禁结果字典。"""
        cookies = account_gate.load_session_cookies()
        gate = account_gate.wait_for_rights(
            cookies,
            wait_seconds=self.wait_seconds,
            poll_seconds=self.poll_seconds,
            requester=session.get_json,
        )
        self._record(
            "权益到账",
            "ok" if gate.ready_for_vip_probe else "failed",
            f"rights_remaining_time={gate.rights_remaining_time}",
        )
        return gate.to_public_dict()

    def verify_playback(self) -> dict:
        """执行只读播放验证；返回探针结果字典。"""
        result = vip_probe.run_probe(account_gate.load_session_cookies())
        self._record("VIP 播放验证", "ok", str(result.get("verdict")))
        return result

    def run(self) -> AutomationResult:
        """执行完整流程并返回结论。"""
        result = AutomationResult()
        cookies = account_gate.load_session_cookies()
        session = vip_probe.ProbeSession(cookies)
        try:
            baseline = account_gate.read_gate(cookies, session.get_json)
            result.baseline_rights = baseline.rights_remaining_time
            result.device = self.device.select_device()
            self.open_app()
            self.complete_ad_task()
        except AutomationError as error:
            self._record("手机端任务", "failed", str(error))
            result.steps = list(self.steps)
            result.exit_code = 6
            return result
        except Exception as error:  # 网络或设备异常统一转成结构化失败
            self._record("手机端任务", "failed", f"{type(error).__name__}: {error}")
            result.steps = list(self.steps)
            result.exit_code = 6
            return result

        gate = self.wait_entitlement(session)
        result.gate = gate
        result.entitlement_detected = bool(gate.get("ready_for_vip_probe"))
        result.rights_delta = (
            int(gate.get("rights_remaining_time", 0)) - result.baseline_rights
        )
        if result.entitlement_detected:
            probe = self.verify_playback()
            result.probe = probe
            result.vip_playback_verified = probe.get("verdict") == "entitled"
            result.exit_code = 0 if result.vip_playback_verified else 3
        else:
            result.exit_code = 2
        result.steps = list(self.steps)
        return result


def main() -> int:
    """执行手机端自动化流程并输出 JSON；返回码 0 成功、2 未到账、5 无设备、6 流程失败。"""
    parser = argparse.ArgumentParser(
        description="通过 ADB 驱动网易云客户端完成免费听任务，并验证权益与播放"
    )
    parser.add_argument("--serial", default=None)
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--stay-seconds", type=int, default=DEFAULT_STAY_SECONDS)
    parser.add_argument("--wait-seconds", type=int, default=DEFAULT_WAIT_SECONDS)
    arguments = parser.parse_args()

    flow = FreeListenFlow(
        AdbDevice(adb_path=arguments.adb, serial=arguments.serial),
        stay_seconds=arguments.stay_seconds,
        wait_seconds=arguments.wait_seconds,
    )
    result = flow.run()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
