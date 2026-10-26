"""验证 VIP 播放探针的门禁、候选筛选、授权判定与输出边界。"""

import json
import unittest
from unittest.mock import patch

import account_gate
import vip_probe


COOKIES = {"MUSIC_U": "session-value-that-must-not-leak"}


class FakeService:
    """按路径前缀返回预置响应的只读假服务。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, cookies, path):
        self.calls.append(path)
        for prefix, payload in self.routes.items():
            if path.startswith(prefix):
                return payload
        raise AssertionError(f"未预期的请求：{path}")


def _routes(*, remaining=1_800_000, player_entry=None, songs=None, playlist_detail=None):
    return {
        account_gate.ACCOUNT_PATH: {"code": 200, "account": {"id": 3344139335}},
        "/api/ad/homepage/free/tab/asset": {
            "code": 200,
            "data": {"freeListen": {"rightsRemainingTime": remaining}},
        },
        vip_probe.USER_PLAYLIST_PATH: {
            "code": 200,
            "playlist": [
                {"id": 11, "name": "空歌单", "specialType": 0, "trackCount": 0},
                {
                    "id": 5033803097,
                    "name": "喜欢的音乐",
                    "specialType": 5,
                    "trackCount": 237,
                },
            ],
        },
        vip_probe.PLAYLIST_DETAIL_PATH: playlist_detail
        or {
            "code": 200,
            "playlist": {
                "id": 5033803097,
                "trackIds": [{"id": 25731646}, {"id": 65800}],
            },
        },
        vip_probe.SONG_DETAIL_PATH: {
            "code": 200,
            "songs": songs
            if songs is not None
            else [
                {"id": 25731646, "name": "愿得一人心", "fee": 0},
                {"id": 65800, "name": "最佳损友", "fee": 1},
            ],
        },
        vip_probe.PLAYER_URL_PATH: {
            "code": 200,
            "data": [player_entry if player_entry else _entitled_entry()],
        },
    }


def _entitled_entry():
    return {
        "id": 65800,
        "url": "http://cdn.example.invalid/stream.mp3",
        "code": 200,
        "fee": 1,
        "br": 128000,
        "time": 267232,
        "freeTimeTrialPrivilege": {
            "resConsumable": True,
            "userConsumable": True,
            "type": 1,
            "remainTime": 1_800_000,
        },
        "freeTrialPrivilege": {
            "resConsumable": True,
            "userConsumable": True,
            "listenType": 1,
            "cannotListenReason": 0,
        },
    }


def _blocked_entry():
    entry = _entitled_entry()
    entry["freeTimeTrialPrivilege"] = {
        "resConsumable": False,
        "userConsumable": False,
        "type": 0,
        "remainTime": 0,
    }
    entry["freeTrialPrivilege"] = {
        "resConsumable": False,
        "userConsumable": False,
        "listenType": 0,
        "cannotListenReason": 1,
    }
    return entry


class VipProbeTests(unittest.TestCase):
    def _run(self, routes, **kwargs):
        service = FakeService(routes)
        with patch.object(account_gate, "request_json", side_effect=service):
            with patch.object(vip_probe, "probe_audio_stream") as stream:
                stream.return_value = {
                    "status": 206,
                    "bytes_read": 32768,
                    "audio_header": "ID3",
                    "byte_budget": 32768,
                    "within_budget": True,
                }
                result = vip_probe.run_probe(COOKIES, **kwargs)
        return result, service

    def test_gate_closed_stops_before_candidate_requests(self):
        result, service = self._run(_routes(remaining=0))
        self.assertEqual(result["verdict"], "gate_closed")
        self.assertFalse(result["gate"]["ready_for_vip_probe"])
        self.assertNotIn("candidate", result)
        self.assertEqual(len(service.calls), 2)
        for path in service.calls:
            self.assertFalse(path.startswith(vip_probe.USER_PLAYLIST_PATH))
            self.assertFalse(path.startswith(vip_probe.PLAYER_URL_PATH))

    def test_auto_candidate_picks_first_vip_song(self):
        result, service = self._run(_routes())
        self.assertEqual(result["verdict"], "entitled")
        self.assertEqual(result["candidate"]["song_id"], 65800)
        self.assertEqual(result["candidate"]["fee"], 1)
        self.assertEqual(result["candidate"]["source"], "playlist_auto")
        self.assertTrue(result["playback"]["url_present"])
        self.assertTrue(result["playback"]["free_listen_user_consumable"])
        self.assertEqual(result["playback"]["free_listen_remain_time"], 1_800_000)
        self.assertTrue(result["stream"]["within_budget"])
        self.assertGreaterEqual(result["requests_made"], 5)

    def test_blocked_when_cannot_listen_reason_present(self):
        result, _ = self._run(_routes(player_entry=_blocked_entry()))
        self.assertEqual(result["verdict"], "blocked")
        self.assertEqual(result["playback"]["cannot_listen_reason"], 1)
        self.assertNotIn("stream", result)

    def test_explicit_song_id_skips_playlist_lookup(self):
        result, service = self._run(_routes(), song_id=65800)
        self.assertEqual(result["verdict"], "entitled")
        self.assertEqual(result["candidate"]["song_id"], 65800)
        self.assertEqual(result["candidate"]["source"], "explicit_song_id")
        for path in service.calls:
            self.assertFalse(path.startswith(vip_probe.USER_PLAYLIST_PATH))
            self.assertFalse(path.startswith(vip_probe.PLAYLIST_DETAIL_PATH))

    def test_no_vip_candidate_is_reported(self):
        routes = _routes(
            songs=[{"id": 25731646, "name": "愿得一人心", "fee": 0}],
        )
        with patch.object(account_gate, "request_json", side_effect=FakeService(routes)):
            with self.assertRaises(RuntimeError) as raised:
                vip_probe.run_probe(COOKIES)
        self.assertIn("VIP", str(raised.exception))

    def test_output_never_contains_credentials(self):
        result, _ = self._run(_routes())
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("session-value-that-must-not-leak", serialized)
        self.assertNotIn("MUSIC_U", serialized)
        self.assertNotIn("3344139335", serialized)
        self.assertFalse(result["claim_submitted"])
        self.assertFalse(result["ad_interaction_automated"])

    def test_evaluate_playback_classifies_unknown_state(self):
        entry = _entitled_entry()
        entry["freeTimeTrialPrivilege"] = {
            "resConsumable": False,
            "userConsumable": False,
            "type": 0,
            "remainTime": 0,
        }
        entry["freeTrialPrivilege"] = {
            "resConsumable": False,
            "userConsumable": False,
            "listenType": 0,
            "cannotListenReason": 0,
        }
        verdict = vip_probe.evaluate_playback(entry)
        self.assertEqual(verdict.verdict, "unknown")


if __name__ == "__main__":
    unittest.main()
