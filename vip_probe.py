"""只读验证免费听权益能否放行 VIP 歌曲播放。

本模块只发起查询类请求：账号信息、免费听余额、歌单、歌曲详情与播放地址。
不提交广告完成、点击、停留或权益领取请求，也不下载完整音频，
只对播放地址发起一次有界 Range 取流以确认数据可达。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import time
import urllib.request

import account_gate


ACCOUNT_PATH = "/api/nuser/account/get"
USER_PLAYLIST_PATH = "/api/user/playlist/"
PLAYLIST_DETAIL_PATH = "/api/v6/playlist/detail"
SONG_DETAIL_PATH = "/api/song/detail"
PLAYER_URL_PATH = "/api/song/enhance/player/url"

MAX_CANDIDATE_SONGS = 20
MAX_DETAIL_BATCH = 20
PROBE_BYTES = 32768
MAX_PROBE_BYTES = 65536
MAX_REQUEST_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2.0
DEFAULT_BITRATE = 128000
REQUEST_TIMEOUT = 12


@dataclass(frozen=True)
class PlaybackVerdict:
    """描述一次播放授权判定的结论。"""

    verdict: str
    url_present: bool
    free_listen_user_consumable: bool
    free_listen_remain_time: int
    cannot_listen_reason: object
    duration_ms: int
    bitrate: int
    source_fee: int


class ProbeSession:
    """带请求计数与有界重试的只读查询会话。"""

    def __init__(self, cookies: dict[str, str], *, attempts: int = MAX_REQUEST_ATTEMPTS):
        self.cookies = cookies
        self.attempts = max(1, attempts)
        self.request_count = 0

    def get_json(self, path: str) -> dict:
        """执行只读查询；参数为接口路径；返回解析后的 JSON；重试耗尽后抛出原始异常。"""
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            self.request_count += 1
            try:
                return account_gate.request_json(self.cookies, path)
            except Exception as error:
                last_error = error
                if attempt == self.attempts:
                    break
                time.sleep(RETRY_DELAY_SECONDS)
        raise RuntimeError(f"查询失败（重试 {self.attempts} 次）：{path}") from last_error


def select_playlist(session: ProbeSession, account_id: int) -> dict:
    """挑选用于取候选歌曲的歌单；优先我喜欢的音乐，其次曲目最多的歌单。"""
    payload = session.get_json(f"{USER_PLAYLIST_PATH}?uid={account_id}&limit=50")
    playlists = payload.get("playlist") or []
    if not playlists:
        raise RuntimeError("账号没有可用歌单，无法自动筛选候选歌曲")
    favourite = [item for item in playlists if item.get("specialType") == 5]
    pool = favourite or playlists
    return max(pool, key=lambda item: item.get("trackCount") or 0)


def collect_song_ids(session: ProbeSession, playlist_id: int) -> list[int]:
    """读取歌单内的歌曲 id；数量受候选上限约束。"""
    payload = session.get_json(
        f"{PLAYLIST_DETAIL_PATH}?id={playlist_id}&n=1&s=0"
    )
    track_ids = (payload.get("playlist") or {}).get("trackIds") or []
    ids = [item.get("id") for item in track_ids if item.get("id")]
    return ids[:MAX_CANDIDATE_SONGS]


def fetch_song_details(session: ProbeSession, song_ids: list[int]) -> list[dict]:
    """批量读取歌曲详情；返回含 id、名称与 fee 的歌曲列表。"""
    if not song_ids:
        return []
    details: list[dict] = []
    for start in range(0, len(song_ids), MAX_DETAIL_BATCH):
        batch = song_ids[start:start + MAX_DETAIL_BATCH]
        payload = session.get_json(
            f"{SONG_DETAIL_PATH}?ids={json.dumps(batch).replace(' ', '')}"
        )
        for song in payload.get("songs") or []:
            details.append(
                {
                    "id": song.get("id"),
                    "name": song.get("name"),
                    "fee": song.get("fee"),
                }
            )
    return details


def select_vip_candidate(details: list[dict]) -> dict | None:
    """按 fee 判定 VIP 候选；fee 为 1 表示 VIP 曲目。"""
    for song in details:
        if song.get("fee") == 1:
            return song
    return None


def fetch_player_entry(session: ProbeSession, song_id: int) -> dict:
    """读取指定歌曲的播放授权条目。"""
    payload = session.get_json(
        f"{PLAYER_URL_PATH}?ids=[{song_id}]&br={DEFAULT_BITRATE}"
    )
    entries = payload.get("data") or []
    if not entries:
        raise RuntimeError(f"歌曲 {song_id} 没有返回播放授权条目")
    return entries[0]


def evaluate_playback(entry: dict) -> PlaybackVerdict:
    """依据授权字段判定播放结论；不下载音频。"""
    free_listen = entry.get("freeTimeTrialPrivilege") or {}
    trial = entry.get("freeTrialPrivilege") or {}
    user_consumable = bool(free_listen.get("userConsumable"))
    remain_time = int(free_listen.get("remainTime") or 0)
    cannot_listen_reason = trial.get("cannotListenReason")
    if cannot_listen_reason:
        verdict = "blocked"
    elif user_consumable and remain_time > 0:
        verdict = "entitled"
    else:
        verdict = "unknown"
    return PlaybackVerdict(
        verdict=verdict,
        url_present=bool(entry.get("url")),
        free_listen_user_consumable=user_consumable,
        free_listen_remain_time=remain_time,
        cannot_listen_reason=cannot_listen_reason,
        duration_ms=int(entry.get("time") or 0),
        bitrate=int(entry.get("br") or 0),
        source_fee=int(entry.get("fee") or 0),
    )


def probe_audio_stream(url: str, *, byte_count: int = PROBE_BYTES) -> dict:
    """对播放地址发起有界 Range 取流；返回状态、字节数与音频头标识。"""
    request = urllib.request.Request(
        url, headers={"Range": f"bytes=0-{byte_count - 1}"}
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        chunk = response.read(MAX_PROBE_BYTES)
        status = response.status
    if chunk[:3] == b"ID3":
        audio_header = "ID3"
    elif chunk[:2] == b"\xff\xfb":
        audio_header = "MP3"
    else:
        audio_header = "unknown"
    return {
        "status": status,
        "bytes_read": len(chunk),
        "audio_header": audio_header,
        "byte_budget": byte_count,
        "within_budget": len(chunk) <= MAX_PROBE_BYTES,
    }


def run_probe(
    cookies: dict[str, str],
    *,
    song_id: int | None = None,
    check_stream: bool = True,
) -> dict[str, object]:
    """执行完整只读验证；参数为会话、可选歌曲 id 与是否取流；返回结果字典。"""
    session = ProbeSession(cookies)
    gate = account_gate.read_gate(cookies, session.get_json)
    if not gate.logged_in or not gate.account_id:
        raise RuntimeError("桌面会话未登录，无法执行播放验证")

    result: dict[str, object] = {
        "mode": "read_only_probe",
        "claim_submitted": False,
        "ad_interaction_automated": False,
        "account_hash": gate.account_hash,
        "gate": {
            "rights_remaining_time": gate.rights_remaining_time,
            "ready_for_vip_probe": gate.ready_for_vip_probe,
        },
    }
    if not gate.ready_for_vip_probe:
        result["verdict"] = "gate_closed"
        result["reason"] = "免费听权益为 0，播放验证未发起"
        result["requests_made"] = session.request_count
        return result

    if song_id is None:
        playlist = select_playlist(session, gate.account_id)
        song_ids = collect_song_ids(session, int(playlist.get("id")))
        details = fetch_song_details(session, song_ids)
        candidate = select_vip_candidate(details)
        if candidate is None:
            raise RuntimeError("候选歌曲中没有 VIP 曲目，无法验证播放")
        source = "playlist_auto"
    else:
        details = fetch_song_details(session, [song_id])
        candidate = next((item for item in details if item.get("id") == song_id), None)
        if candidate is None:
            raise RuntimeError(f"歌曲 {song_id} 无详情")
        source = "explicit_song_id"

    entry = fetch_player_entry(session, int(candidate["id"]))
    verdict = evaluate_playback(entry)
    result["candidate"] = {
        "song_id": candidate.get("id"),
        "song_name": candidate.get("name"),
        "fee": candidate.get("fee"),
        "source": source,
    }
    result["playback"] = asdict(verdict)
    result["verdict"] = verdict.verdict

    if check_stream and verdict.url_present and verdict.verdict == "entitled":
        result["stream"] = probe_audio_stream(entry["url"])
    result["requests_made"] = session.request_count
    return result


def main() -> int:
    """执行只读播放验证并输出 JSON；返回码 0 放行、2 门禁关闭、3 被拒绝。"""
    parser = argparse.ArgumentParser(
        description="只读验证免费听权益能否放行 VIP 歌曲播放；不提交任何广告或领奖请求"
    )
    parser.add_argument("--song-id", type=int, default=None)
    parser.add_argument("--skip-stream", action="store_true")
    arguments = parser.parse_args()

    result = run_probe(
        account_gate.load_session_cookies(),
        song_id=arguments.song_id,
        check_stream=not arguments.skip_stream,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    verdict = result.get("verdict")
    if verdict == "entitled":
        return 0
    if verdict == "gate_closed":
        return 2
    if verdict == "blocked":
        return 3
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
