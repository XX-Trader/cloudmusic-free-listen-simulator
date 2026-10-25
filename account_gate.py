"""Read-only gate for a locally logged-in NetEase Cloud Music desktop session.

The script never submits ad completion, click, monitor, or entitlement-grant
requests. It only reads the current account and free-listening balance.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
from dataclasses import asdict, dataclass
import hashlib
import json
import pathlib
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request


PROFILE = pathlib.Path.home() / "AppData/Local/NetEase/CloudMusic/webapp91x64"
ACCOUNT_PATH = "/api/nuser/account/get"
ASSET_PATH = "/api/ad/homepage/free/tab/asset"


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


@dataclass(frozen=True)
class GateResult:
    logged_in: bool
    account_hash: str | None
    rights_remaining_time: int
    ready_for_vip_probe: bool
    source: str = "desktop_session_read_only"
    claim_submitted: bool = False


def _unprotect(data: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _Blob()
    success = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    )
    if not success:
        raise RuntimeError("无法解密当前 Windows 用户的桌面会话")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)


def _load_aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as error:
        raise RuntimeError("缺少 cryptography；请在独立虚拟环境中安装") from error
    return AESGCM


def load_session_cookies(profile: pathlib.Path = PROFILE) -> dict[str, str]:
    prefs = json.loads((profile / "LocalPrefs.json").read_text(encoding="utf-8"))
    wrapped_key = base64.b64decode(prefs["os_crypt"]["encrypted_key"])
    if not wrapped_key.startswith(b"DPAPI"):
        raise RuntimeError("不支持当前桌面会话密钥格式")
    aes_key = _unprotect(wrapped_key[5:])
    AESGCM = _load_aesgcm()

    database = sqlite3.connect(
        (profile / "Cookies").as_uri() + "?mode=ro", uri=True, timeout=2
    )
    try:
        rows = database.execute(
            "select host_key,name,value,encrypted_value from cookies "
            "where host_key in (?,?) and path=?",
            (".music.163.com", "music.163.com", "/"),
        ).fetchall()
    finally:
        database.close()

    cookies: dict[str, str] = {}
    for host, name, value, encrypted in rows:
        if not value and encrypted:
            if encrypted.startswith(b"v10"):
                plain = AESGCM(aes_key).decrypt(encrypted[3:15], encrypted[15:], None)
            else:
                plain = _unprotect(encrypted)
            host_hash = hashlib.sha256(host.encode()).digest()
            if plain.startswith(host_hash):
                plain = plain[len(host_hash):]
            value = plain.decode("utf-8")
        if value:
            cookies[name] = value
    return cookies


def request_json(cookies: dict[str, str], path: str) -> dict:
    request = urllib.request.Request(
        "https://music.163.com" + path,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
                "Chrome/91.0.4472.164 NeteaseMusicDesktop/3.1.41.205564"
            ),
            "Referer": "https://music.163.com/",
            "Cookie": "; ".join(f"{key}={value}" for key, value in cookies.items()),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = response.read(1_048_577)
            if len(payload) > 1_048_576:
                raise RuntimeError("服务响应超过大小限制")
            return json.loads(payload)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"服务返回 HTTP {error.code}") from error


def read_gate(cookies: dict[str, str]) -> GateResult:
    account_result = request_json(cookies, ACCOUNT_PATH)
    account_id = (account_result.get("account") or {}).get("id")
    asset_result = request_json(cookies, ASSET_PATH) if account_id else {}
    remaining = int(
        (((asset_result.get("data") or {}).get("freeListen") or {}).get(
            "rightsRemainingTime", 0
        ))
        or 0
    )
    return GateResult(
        logged_in=bool(account_id),
        account_hash=(
            hashlib.sha256(str(account_id).encode()).hexdigest()[:12]
            if account_id
            else None
        ),
        rights_remaining_time=max(0, remaining),
        ready_for_vip_probe=bool(account_id and remaining > 0),
    )


def wait_for_rights(
    cookies: dict[str, str], *, wait_seconds: int = 0, poll_seconds: int = 5
) -> GateResult:
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        result = read_gate(cookies)
        if result.ready_for_vip_probe or time.monotonic() >= deadline:
            return result
        time.sleep(max(1, poll_seconds))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只读检查桌面账号的免费听权益；不会提交广告完成或领取请求"
    )
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args()
    result = wait_for_rights(
        load_session_cookies(),
        wait_seconds=args.wait_seconds,
        poll_seconds=args.poll_seconds,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.ready_for_vip_probe else 2


if __name__ == "__main__":
    raise SystemExit(main())
