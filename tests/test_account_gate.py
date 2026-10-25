import unittest
from unittest.mock import patch

import account_gate


class AccountGateTests(unittest.TestCase):
    def test_positive_balance_opens_vip_gate(self):
        with patch.object(account_gate, "request_json") as request:
            request.side_effect = [
                {"code": 200, "account": {"id": 123}},
                {"code": 200, "data": {"freeListen": {"rightsRemainingTime": 90}}},
            ]
            result = account_gate.read_gate({"session": "kept-in-memory"})
        self.assertTrue(result.logged_in)
        self.assertTrue(result.ready_for_vip_probe)
        self.assertEqual(result.rights_remaining_time, 90)
        self.assertFalse(result.claim_submitted)

    def test_zero_balance_keeps_vip_gate_closed(self):
        with patch.object(account_gate, "request_json") as request:
            request.side_effect = [
                {"code": 200, "account": {"id": 123}},
                {"code": 200, "data": {"freeListen": {"rightsRemainingTime": 0}}},
            ]
            result = account_gate.read_gate({})
        self.assertFalse(result.ready_for_vip_probe)

    def test_logged_out_does_not_query_assets(self):
        with patch.object(account_gate, "request_json", return_value={"code": 200}) as request:
            result = account_gate.read_gate({})
        self.assertFalse(result.logged_in)
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
