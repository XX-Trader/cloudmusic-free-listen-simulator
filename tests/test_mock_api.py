"""验证一键 Mock 接口的离线边界与输入约束。"""

import json
import threading
import unittest
import urllib.error
import urllib.request

import mock_api


class MockApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = mock_api.create_server(port=0)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def _post(self, body, path=mock_api.RUN_PATH):
        request = urllib.request.Request(
            self.base + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=5)

    def _get(self, path):
        return urllib.request.urlopen(self.base + path, timeout=5)

    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_health_reports_offline(self):
        with self._get(mock_api.HEALTH_PATH) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["mode"], "offline_demo")
        self.assertTrue(payload["loopback_only"])
        self.assertFalse(payload["real_entitlement_granted"])
        self.assertEqual(payload["network_requests"], 0)
        self.assertEqual(set(payload["scenarios"]), {"success", "short-stay", "server-rejected", "duplicate"})

    def test_one_shot_success_returns_demo_balance(self):
        with self._post(json.dumps({"scenario": "success"}).encode()) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["mode"], "offline_demo")
        self.assertEqual(payload["state"], "demo_completed")
        self.assertEqual(payload["demo_balance_minutes"], 30)
        self.assertEqual(payload["network_requests"], 0)
        self.assertFalse(payload["real_entitlement_granted"])
        self.assertTrue(payload["receipt"]["demo"])
        self.assertEqual(payload["api"]["version"], "v1")
        self.assertEqual(
            [event["action"] for event in payload["audit_log"]],
            ["task_issued", "ad_started", "ad_clicked", "dwell_started", "reward_claimed"],
        )

    def test_empty_body_defaults_to_success(self):
        with self._post(b"") as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["scenario"], "success")

    def test_short_stay_returns_zero(self):
        with self._post(json.dumps({"scenario": "short-stay"}).encode()) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["demo_balance_minutes"], 0)
        self.assertEqual(payload["receipt"]["reason"], "insufficient_stay")

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(json.dumps({"scenario": "real-claim"}).encode())
        self.assertEqual(raised.exception.code, 400)
        payload = json.loads(raised.exception.read())
        self.assertFalse(payload["real_entitlement_granted"])

    def test_non_object_json_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(json.dumps(["success"]).encode())
        self.assertEqual(raised.exception.code, 400)

    def test_invalid_json_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(b"{not json")
        self.assertEqual(raised.exception.code, 400)

    def test_oversized_body_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(b"x" * (mock_api.MAX_BODY_BYTES + 1))
        self.assertEqual(raised.exception.code, 413)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._get("/v1/real/claim")
        self.assertEqual(raised.exception.code, 404)

    def test_get_run_is_405(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._get(mock_api.RUN_PATH)
        self.assertEqual(raised.exception.code, 405)


if __name__ == "__main__":
    unittest.main()
