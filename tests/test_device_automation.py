"""验证手机端自动化的定位、点击顺序与结论汇总；不依赖真机。"""

import unittest
from unittest.mock import patch

import account_gate
import device_automation
from device_automation import (
    AdbDevice,
    AutomationError,
    FreeListenFlow,
    UiNode,
    find_node,
)


def node(text, bounds="[0,0][100,100]", description="", resource_id=""):
    """构造一个界面节点。"""
    return UiNode(
        text=text,
        description=description,
        resource_id=resource_id,
        class_name="android.widget.TextView",
        bounds=bounds,
    )


class FakeDevice:
    """按顺序吐出界面结构与前台 Activity 的假设备。"""

    def __init__(self, dumps, activities):
        self.dumps = list(dumps)
        self.activities = list(activities)
        self.taps = []
        self.backs = 0
        self.shells = []
        self.launched = False

    def _next(self, items, default):
        if not items:
            return default
        if len(items) == 1:
            return items[0]
        return items.pop(0)

    def dump_ui(self):
        return self.dumps.pop(0) if self.dumps else []

    def current_activity(self):
        return self._next(
            self.activities, "com.netease.cloudmusic/.activity.MainActivity"
        )

    def is_app_foreground(self):
        return device_automation.APP_PACKAGE in self.current_activity()

    def tap(self, x, y):
        self.taps.append((x, y))

    def press_back(self):
        self.backs += 1
        if self.activities:
            self.activities[-1] = "com.netease.cloudmusic/.activity.MainActivity"

    def shell(self, command):
        self.shells.append(command)
        return ""

    def launch_app(self):
        self.launched = True

    def select_device(self):
        return "fake-serial"


class UiHelpersTests(unittest.TestCase):
    def test_center_is_computed_from_bounds(self):
        self.assertEqual(node("x", "[100,200][300,500]").center, (200, 350))

    def test_center_falls_back_to_origin(self):
        self.assertEqual(node("x", "").center, (0, 0))

    def test_find_node_prefers_text_then_description_then_resource(self):
        nodes = [
            node("免费听", "[827,126][1052,325]"),
            node("", "[0,0][10,10]", description="搜索"),
            node("", "[0,0][10,10]", resource_id="com.x:id/btn"),
        ]
        self.assertEqual(find_node(nodes, text="免费听").center, (939, 225))
        self.assertEqual(find_node(nodes, description="搜索").center, (5, 5))
        self.assertEqual(find_node(nodes, resource_id="com.x:id/btn").center, (5, 5))
        self.assertIsNone(find_node(nodes, text="不存在"))

    def test_find_node_supports_contains_match(self):
        nodes = [node("看广告免费听VIP歌曲")]
        self.assertIsNotNone(find_node(nodes, text="看广告免费听", contains=True))
        self.assertIsNone(find_node(nodes, text="看广告免费听", contains=False))


class ActivityParsingTests(unittest.TestCase):
    SAMPLE = (
        "  topResumedActivity=ActivityRecord{130737127 u0 "
        "com.netease.cloudmusic/.music.biz.rn.activity.CloudMusicRNActivity t59574}\n"
    )

    def test_parses_component_name_not_task_id(self):
        self.assertEqual(
            device_automation.parse_top_activity(self.SAMPLE),
            "com.netease.cloudmusic/.music.biz.rn.activity.CloudMusicRNActivity",
        )

    def test_falls_back_to_resumed_activity_pattern(self):
        output = "  mResumedActivity: ActivityRecord{1 u0 com.example/.MainActivity t1}"
        self.assertEqual(
            device_automation.parse_top_activity(output), "com.example/.MainActivity"
        )

    def test_returns_empty_when_nothing_matches(self):
        self.assertEqual(device_automation.parse_top_activity("no activity here"), "")


class FlowTests(unittest.TestCase):
    def _flow(self, dumps, activities):
        device = FakeDevice(dumps, activities)
        flow = FreeListenFlow(
            device,
            stay_seconds=8,
            wait_seconds=0,
            poll_seconds=1,
            sleeper=lambda seconds: None,
        )
        return flow, device

    def test_complete_task_taps_text_targets_in_order(self):
        flow, device = self._flow(
            dumps=[
                [node("免费听", "[827,126][1052,325]")],
                [
                    node("看广告 免费听", "[52,186][1087,369]"),
                    node("看广告 免费听", "[417,1884][806,2014]"),
                    node("点击后停留8秒", "[130,814][797,971]"),
                ],
                [
                    node("看广告 免费听", "[417,1008][806,1138]"),
                    node("看广告 免费听", "[417,2347][806,2478]"),
                ],
                [node("进入落地页查看详情", "[122,2305][1102,2481]")],
                [node("允许", "[700,2280][1000,2400]"), node("拒绝", "[100,2280][400,2400]")],
            ],
            activities=[
                "com.netease.cloudmusic/.activity.MainActivity",
                "com.landing.app/.MainActivity",
            ],
        )
        with patch.object(account_gate, "load_session_cookies", return_value={}):
            with patch.object(account_gate, "wait_for_rights") as gate:
                gate.return_value = account_gate.GateResult(
                    logged_in=True,
                    account_hash="hash",
                    rights_remaining_time=1_800_000,
                    ready_for_vip_probe=True,
                )
                with patch.object(device_automation.vip_probe, "run_probe") as probe:
                    probe.return_value = {"verdict": "entitled"}
                    result = flow.run()

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.entitlement_detected)
        self.assertTrue(result.vip_playback_verified)
        self.assertEqual(
            device.taps,
            [(939, 225), (611, 2412), (612, 2393), (850, 2340)],
        )
        self.assertTrue(any("swipe" in command for command in device.shells))
        names = [step.name for step in result.steps]
        self.assertEqual(
            names,
            [
                "启动客户端",
                "切换到免费听",
                "打开广告任务",
                "点击落地页入口",
                "系统弹窗",
                "落地页停留",
                "返回客户端",
                "权益到账",
                "VIP 播放验证",
            ],
        )

    def test_returns_to_tab_page_when_inside_embedded_page(self):
        flow, device = self._flow(
            dumps=[
                [node("看广告 免费听", "[52,186][1087,369]")],
                [node("免费听", "[827,126][1052,325]")],
                [node("领30分钟", "[917,544][1088,601]")],
                [node("进入落地页查看详情", "[122,2305][1102,2481]")],
                [],
            ],
            activities=[
                "com.netease.cloudmusic/.activity.MainActivity",
                "com.landing.app/.MainActivity",
            ],
        )
        with patch.object(account_gate, "load_session_cookies", return_value={}):
            with patch.object(account_gate, "wait_for_rights") as gate:
                gate.return_value = account_gate.GateResult(
                    logged_in=True,
                    account_hash="hash",
                    rights_remaining_time=0,
                    ready_for_vip_probe=False,
                )
                result = flow.run()
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(device.backs, 1)
        self.assertEqual(device.taps[0], (939, 225))
        self.assertEqual(device.taps[1], (1002, 572))

    def test_gate_closed_returns_exit_code_two(self):
        flow, _ = self._flow(
            dumps=[
                [node("免费听", "[827,126][1052,325]")],
                [node("看广告 免费听", "[417,1884][806,2014]")],
                [node("进入落地页查看详情", "[122,2305][1102,2481]")],
                [],
            ],
            activities=[
                "com.netease.cloudmusic/.activity.MainActivity",
                "com.landing.app/.MainActivity",
            ],
        )
        with patch.object(account_gate, "load_session_cookies", return_value={}):
            with patch.object(account_gate, "wait_for_rights") as gate:
                gate.return_value = account_gate.GateResult(
                    logged_in=True,
                    account_hash="hash",
                    rights_remaining_time=0,
                    ready_for_vip_probe=False,
                )
                result = flow.run()
        self.assertEqual(result.exit_code, 2)
        self.assertFalse(result.entitlement_detected)
        self.assertIsNone(result.probe)

    def test_missing_target_defers_to_entitlement_state(self):
        flow, _ = self._flow(
            dumps=[[node("别的页面")]],
            activities=[
                "com.netease.cloudmusic/.activity.MainActivity",
                "com.landing.app/.MainActivity",
            ],
        )
        with patch.object(account_gate, "load_session_cookies", return_value={}):
            with patch.object(account_gate, "wait_for_rights") as gate:
                gate.return_value = account_gate.GateResult(
                    logged_in=True,
                    account_hash="hash",
                    rights_remaining_time=0,
                    ready_for_vip_probe=False,
                )
                result = flow.run()
        self.assertEqual(result.exit_code, 2)
        self.assertFalse(result.entitlement_detected)
        self.assertNotIn("打开广告任务", [step.name for step in result.steps])
        self.assertNotIn("手机端任务", [step.name for step in result.steps])

    def test_no_device_is_reported_before_any_tap(self):
        device = AdbDevice(adb_path="adb", serial=None)
        with patch.object(AdbDevice, "list_devices", return_value=[]):
            with self.assertRaises(AutomationError):
                device.select_device()

    def test_stay_seconds_never_below_task_requirement(self):
        flow = FreeListenFlow(FakeDevice([], []), stay_seconds=1)
        self.assertEqual(flow.stay_seconds, device_automation.DEFAULT_STAY_SECONDS)

    def test_output_contains_no_credentials(self):
        flow, _ = self._flow([], [])
        result = flow.run()
        import json

        payload = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn("MUSIC_U", payload)
        self.assertNotIn("Cookie", payload)
        self.assertIn("entitlement_detected", payload)


if __name__ == "__main__":
    unittest.main()
