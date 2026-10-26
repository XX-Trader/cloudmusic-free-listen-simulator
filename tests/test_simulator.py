import math
import pathlib
import re
import unittest
from unittest.mock import patch

from simulator import (
    SCENARIOS,
    AuditEvent,
    DemoServer,
    Flow,
    Method,
    ScenarioSpec,
    State,
    simulate,
)


SCENARIO_EXPECTATIONS = {
    "success": ("demo_completed", True, "demo_approved", 30),
    "short-stay": ("rejected", False, "insufficient_stay", 0),
    "server-rejected": ("rejected", False, "demo_server_rejected", 0),
    "duplicate": ("demo_completed", True, "demo_approved", 30),
}


class SimulatorTests(unittest.TestCase):
    def test_clock_alone_does_not_grant_rights(self):
        server = DemoServer()
        task = server.issue()
        self.assertFalse(server.claim(task.task_id, now=30).approved)
        self.assertEqual(server.balance_minutes, 0)

    def test_stay_threshold_and_receipt(self):
        server = DemoServer()
        task = server.issue(stay_seconds=8)
        flow = Flow(task)
        flow.start_ad(now=0.5)
        flow.click(server, now=1)
        flow.begin_dwell(now=1)
        self.assertTrue(flow.claim(server, now=9).approved)
        self.assertEqual(flow.state, State.COMPLETED)
        self.assertEqual(server.balance_minutes, 30)

    def test_insufficient_stay_can_be_retried_after_waiting(self):
        server = DemoServer()
        task = server.issue(stay_seconds=8)
        flow = Flow(task)
        flow.start_ad(now=0.5)
        flow.click(server, now=1)
        flow.begin_dwell(now=1)
        self.assertFalse(flow.claim(server, now=8.999).approved)
        self.assertEqual(flow.state, State.REJECTED)
        self.assertEqual(server.balance_minutes, 0)
        self.assertTrue(flow.claim(server, now=9).approved)
        self.assertEqual(flow.state, State.COMPLETED)
        self.assertEqual(server.balance_minutes, 30)

    def test_terminal_rejection_cannot_be_retried(self):
        server = DemoServer()
        task = server.issue(stay_seconds=8)
        flow = Flow(task)
        flow.start_ad(now=0.5)
        flow.click(server, now=1)
        flow.begin_dwell(now=1)
        self.assertFalse(flow.claim(server, now=9, reject=True).approved)
        with self.assertRaises(ValueError):
            flow.claim(server, now=10)

    def test_claim_exception_restores_retryable_state(self):
        server = DemoServer()
        task = server.issue(stay_seconds=8)
        flow = Flow(task)
        flow.start_ad(now=0.5)
        flow.click(server, now=1)
        flow.begin_dwell(now=1)
        with patch.object(DemoServer, "claim", side_effect=OSError("模拟服务中断")):
            with self.assertRaises(OSError):
                flow.claim(server, now=9)
        self.assertEqual(flow.state, State.DWELLING)
        self.assertTrue(flow.claim(server, now=9).approved)

    def test_click_stay_task_cannot_skip_dwell_stage(self):
        server = DemoServer()
        task = server.issue(stay_seconds=8)
        flow = Flow(task)
        flow.start_ad(now=0.5)
        flow.click(server, now=1)
        with self.assertRaises(ValueError):
            flow.claim(server, now=9)
        self.assertEqual(server.balance_minutes, 0)

    def test_click_only_task_claims_without_dwell_stage(self):
        server = DemoServer()
        task = server.issue(method=Method.CLICK)
        flow = Flow(task)
        flow.start_ad(now=0.5)
        flow.click(server, now=1)
        self.assertTrue(flow.claim(server, now=2).approved)
        self.assertEqual(server.balance_minutes, 30)

    def test_phase_time_cannot_move_backwards(self):
        server = DemoServer()
        flow = Flow(server.issue())
        flow.start_ad(now=10)
        with self.assertRaises(ValueError):
            flow.click(server, now=1)

    def test_duplicates_are_idempotent(self):
        server = DemoServer()
        task = server.issue()
        server.click(task.task_id, now=1)
        first = server.claim(task.task_id, now=9)
        self.assertEqual(first, server.claim(task.task_id, now=99))
        self.assertEqual(server.balance_minutes, 30)

    def test_expired_and_unknown_tasks(self):
        server = DemoServer()
        task = server.issue(ttl=5)
        self.assertFalse(server.click(task.task_id, now=5))
        self.assertEqual(server.claim(task.task_id, now=6).reason, "task_expired")
        self.assertEqual(server.claim("unknown", now=0).reason, "unknown_task")

    def test_server_rejection_is_not_success(self):
        result = simulate("server-rejected")
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(result["demo_balance_minutes"], 0)
        self.assertFalse(result["real_entitlement_granted"])
        self.assertEqual(result["audit_log"][-1]["outcome"], "rejected")

    def test_click_only_and_backwards_clock(self):
        server = DemoServer()
        task = server.issue(method=Method.CLICK)
        server.click(task.task_id, now=10)
        self.assertEqual(server.claim(task.task_id, now=9).reason, "clock_moved_backwards")
        self.assertTrue(server.claim(task.task_id, now=10).approved)

    def test_invalid_numbers_are_rejected(self):
        for value in (-1, math.inf, math.nan):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DemoServer().issue(stay_seconds=value)

    def test_flow_rejects_invalid_action_order(self):
        server = DemoServer()
        flow = Flow(server.issue())
        with self.assertRaises(ValueError):
            flow.click(server, now=1)
        with self.assertRaises(ValueError):
            flow.begin_dwell(now=1)

    def test_success_automates_click_dwell_and_claim(self):
        result = simulate("success")
        self.assertEqual(
            [event["action"] for event in result["audit_log"]],
            [
                "task_issued",
                "ad_started",
                "ad_clicked",
                "dwell_started",
                "reward_claimed",
            ],
        )
        self.assertEqual(result["audit_log"][-1]["outcome"], "approved")
        self.assertEqual(
            result["automation"],
            {
                "scope": "local_demo_only",
                "simulated_ad_started": True,
                "simulated_click_executed": True,
                "simulated_claim_executed": True,
            },
        )
        self.assertEqual(result["demo_balance_minutes"], 30)

    def test_duplicate_auto_claim_is_audited_as_idempotent(self):
        result = simulate("duplicate")
        self.assertEqual(result["audit_log"][-1]["action"], "reward_reclaimed")
        self.assertEqual(result["audit_log"][-1]["outcome"], "idempotent")
        self.assertEqual(result["demo_balance_minutes"], 30)

    def test_all_scenarios_are_explicitly_offline(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                result = simulate(scenario)
                self.assertEqual(result["mode"], "offline_demo")
                self.assertEqual(result["network_requests"], 0)
                self.assertFalse(result["real_entitlement_granted"])
                self.assertTrue(result["receipt"]["demo"])

    def test_powershell_and_python_scenarios_match(self):
        script_path = pathlib.Path(__file__).parents[1] / "run.ps1"
        script = script_path.read_text(encoding="utf-8")
        validate_set = re.search(r"\[ValidateSet\((.*?)\)\]", script, re.DOTALL)
        self.assertIsNotNone(validate_set)
        powershell_scenarios = set(re.findall(r"'([^']+)'", validate_set.group(1)))
        self.assertEqual(powershell_scenarios, set(SCENARIOS))

    def test_scenario_expectations_match_registry(self):
        self.assertEqual(set(SCENARIO_EXPECTATIONS), set(SCENARIOS))
        for scenario, expected in SCENARIO_EXPECTATIONS.items():
            with self.subTest(scenario=scenario):
                state, approved, reason, balance = expected
                result = simulate(scenario)
                self.assertEqual(result["state"], state)
                self.assertEqual(result["receipt"]["approved"], approved)
                self.assertEqual(result["receipt"]["reason"], reason)
                self.assertEqual(result["demo_balance_minutes"], balance)

    def test_audit_log_fields_are_complete_and_ordered(self):
        expected_fields = set(AuditEvent.__dataclass_fields__)
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                events = simulate(scenario)["audit_log"]
                self.assertTrue(events)
                for index, event in enumerate(events, start=1):
                    self.assertEqual(set(event), expected_fields)
                    self.assertEqual(event["sequence"], index)
                    self.assertTrue(math.isfinite(event["virtual_time"]))
                    self.assertGreaterEqual(event["virtual_time"], 0)
                    self.assertTrue(event["action"])
                    self.assertTrue(event["outcome"])
                    self.assertTrue(event["detail"])
                times = [event["virtual_time"] for event in events]
                self.assertEqual(times, sorted(times))

    def test_audit_log_is_isolated_between_runs(self):
        from simulator import AutoRunner

        runner = AutoRunner(DemoServer())
        first = runner.run("success", SCENARIOS["success"])
        second = runner.run("short-stay", SCENARIOS["short-stay"])
        self.assertEqual(len(first["audit_log"]), 5)
        self.assertEqual(len(second["audit_log"]), 5)
        self.assertEqual(second["audit_log"][-1]["outcome"], "rejected")
        self.assertEqual(
            [event["action"] for event in second["audit_log"]],
            [
                "task_issued",
                "ad_started",
                "ad_clicked",
                "dwell_started",
                "reward_claimed",
            ],
        )

    def test_invalid_scenario_timing_is_rejected(self):
        from simulator import AutoRunner

        runner = AutoRunner(DemoServer())
        with self.assertRaises(ValueError):
            runner.run(
                "backwards",
                ScenarioSpec(click_at=1.0, claim_at=9.0, ad_started_at=10.0),
            )


if __name__ == "__main__":
    unittest.main()
