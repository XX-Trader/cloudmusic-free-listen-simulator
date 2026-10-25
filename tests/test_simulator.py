import math
import unittest
from simulator import DemoServer, Flow, Method, State, simulate


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
        flow.click(server, now=1)
        self.assertFalse(flow.claim(server, now=8.999).approved)
        self.assertEqual(server.balance_minutes, 0)
        self.assertTrue(flow.claim(server, now=9).approved)
        self.assertEqual(flow.state, State.COMPLETED)
        self.assertEqual(server.balance_minutes, 30)

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

    def test_all_scenarios_are_explicitly_offline(self):
        for scenario in ("success", "short-stay", "server-rejected", "duplicate"):
            result = simulate(scenario)
            self.assertEqual(result["mode"], "offline_demo")
            self.assertEqual(result["network_requests"], 0)
            self.assertFalse(result["real_entitlement_granted"])
        self.assertEqual(simulate("duplicate")["demo_balance_minutes"], 30)


if __name__ == "__main__":
    unittest.main()
