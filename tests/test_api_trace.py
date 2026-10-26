"""验证接口链路提取工具的解析、归类与奖励接口识别。"""

import unittest

from api_trace import REWARD_GAIN_PATH, classify, parse_logcat


SAMPLE = """
10-26 11:38:47.900 I cr_request: https://interface3.music.163.com/eapi/ad/monitor/impress   startInternalLocked
10-26 11:38:48.044 I cr_request: https://interface3.music.163.com/eapi/ad/monitor/incentive/video   startInternalLocked
10-26 11:38:48.367 I cr_request: https://interface3.music.163.com/eapi/ad/listening/rights/gain   startInternalLocked
10-26 11:39:22.592 I cr_request: https://interface3.music.163.com/eapi/ad/homepage/free/tab/asset
10-26 11:38:50.558 I cr_request: https://interface3.music.163.com/eapi/song/enhance/privilege
10-26 11:38:51.000 I cr_request: https://clientlogsf.music.163.com/api/clientlog/encrypt/upload
10-26 11:38:52.000 I cr_request: https://ossgw.alicdn.com/creatives-assets/prod/a.jpg
10-26 11:38:53.000 I something else https://interface3.music.163.com/eapi/ad/get?type_ids=%5B%22400000_0%22%5D
10-26 11:38:54.000 I/cr_request(12752): https://interface3.music.163.com/eapi/ad/monitor/conversion
"""


class ApiTraceTests(unittest.TestCase):
    def test_parses_only_request_lines(self):
        result = parse_logcat(SAMPLE)
        self.assertEqual(len(result.entries), 8)
        self.assertEqual(result.entries[-1].path, "/eapi/ad/monitor/conversion")
        self.assertEqual(result.entries[-1].host, "interface3.music.163.com")
        self.assertNotIn(
            "/eapi/ad/get", [entry.path for entry in result.entries]
        )

    def test_classifies_categories(self):
        result = parse_logcat(SAMPLE)
        self.assertEqual(result.categories["ad_monitor"], 3)
        self.assertEqual(result.categories["entitlement"], 2)
        self.assertEqual(result.categories["playback"], 1)
        self.assertEqual(result.categories["telemetry"], 1)
        self.assertEqual(result.categories["third_party"], 1)

    def test_detects_reward_gain_endpoint(self):
        result = parse_logcat(SAMPLE)
        self.assertTrue(result.reward_gain_seen)
        self.assertEqual(len(result.reward_gain_timestamps), 1)

    def test_missing_reward_endpoint_is_reported(self):
        result = parse_logcat("nothing here")
        self.assertFalse(result.reward_gain_seen)
        self.assertEqual(result.reward_gain_timestamps, [])

    def test_classify_falls_back_for_unknown_paths(self):
        self.assertEqual(classify("/eapi/whatever"), "other_eapi")
        self.assertEqual(classify("/api/other"), "other_api")
        self.assertEqual(classify("/static/a.png"), "third_party")

    def test_output_contains_no_credentials(self):
        payload = parse_logcat(SAMPLE).to_dict()
        import json

        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("MUSIC_U", text)
        self.assertIn(REWARD_GAIN_PATH, text)


if __name__ == "__main__":
    unittest.main()
