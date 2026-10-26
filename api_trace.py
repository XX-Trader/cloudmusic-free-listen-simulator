"""从手机端日志中提取网易云官方接口调用链（只读观测）。

客户端自身会把每个请求的 URL 写进 logcat 的 `cr_request` 标签。本模块只读取
这些已经发出的请求并做归类，不构造、不重放任何请求。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.parse


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
REQUEST_LINE = re.compile(r"cr_request(?:\(\d+\))?:\s+(https?://\S+)")

REWARD_GAIN_PATH = "/eapi/ad/listening/rights/gain"

CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ad_task", ("/eapi/ad/get", "/eapi/ad/flow/get", "/eapi/ad/loading/", "/eapi/ad/prefetch/")),
    ("ad_monitor", ("/eapi/ad/monitor/", "/eapi/ad/start/info/upload", "/eapi/ad/sniffing")),
    ("entitlement", ("/eapi/ad/listening/", "/eapi/ad/homepage/free/tab/", "/eapi/vipnewcenter/")),
    ("playback", ("/eapi/song/enhance/", "/eapi/song/detail", "/eapi/song/player/")),
    ("telemetry", ("/api/clientlog/", "/eapi/rtrs/", "/eapi/link/")),
)


@dataclass
class TraceEntry:
    """描述一次被观测到的接口调用。"""

    timestamp: str
    host: str
    path: str
    category: str

    def to_dict(self) -> dict[str, str]:
        """返回可输出字典。"""
        return {
            "timestamp": self.timestamp,
            "host": self.host,
            "path": self.path,
            "category": self.category,
        }


@dataclass
class TraceResult:
    """汇总一次接口链路观测。"""

    source: str = "logcat"
    entries: list[TraceEntry] = field(default_factory=list)
    categories: dict[str, int] = field(default_factory=dict)
    reward_gain_seen: bool = False
    reward_gain_timestamps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """返回可输出字典。"""
        return {
            "source": self.source,
            "request_count": len(self.entries),
            "categories": self.categories,
            "reward_gain_seen": self.reward_gain_seen,
            "reward_gain_timestamps": self.reward_gain_timestamps,
            "entries": [entry.to_dict() for entry in self.entries],
        }


def classify(path: str) -> str:
    """按路径前缀归类接口；无法归类时返回 other。"""
    for name, prefixes in CATEGORIES:
        if any(path.startswith(prefix) for prefix in prefixes):
            return name
    if path.startswith("/eapi/"):
        return "other_eapi"
    if "/api/" in path:
        return "other_api"
    return "third_party"


def parse_logcat(text: str, *, source: str = "logcat") -> TraceResult:
    """解析 logcat 文本，提取接口调用链。"""
    result = TraceResult(source=source)
    for line in text.splitlines():
        match = REQUEST_LINE.search(line)
        if match is None:
            continue
        url = match.group(1)
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path
        timestamp = line[:18].strip() if len(line) >= 18 else ""
        category = classify(path)
        result.entries.append(
            TraceEntry(
                timestamp=timestamp,
                host=parsed.netloc,
                path=path,
                category=category,
            )
        )
        result.categories[category] = result.categories.get(category, 0) + 1
        if path == REWARD_GAIN_PATH:
            result.reward_gain_seen = True
            result.reward_gain_timestamps.append(timestamp)
    return result


def capture_logcat(
    adb_path: str = DEFAULT_ADB,
    serial: str | None = None,
    *,
    seconds: int = 60,
) -> str:
    """从设备抓取一段时间的 logcat 文本；设备不可用时抛出 RuntimeError。"""
    command = [adb_path]
    if serial:
        command += ["-s", serial]
    command += ["logcat", "-d", "-v", "time"]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=seconds + 30)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"抓取 logcat 超时：{error}") from error
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"抓取 logcat 失败：{error}")
    return completed.stdout.decode("utf-8", errors="replace")


def wait_for_window(adb_path: str, serial: str | None, seconds: int) -> None:
    """等待指定时长，让客户端完成一轮请求后再读取日志。"""
    time.sleep(max(0, seconds))


def main() -> int:
    """输出接口链路；支持解析本地日志或从设备抓取；成功返回 0。"""
    parser = argparse.ArgumentParser(
        description="从手机端日志提取网易云官方接口调用链（只读观测）"
    )
    parser.add_argument("--log", default="", help="本地 logcat 文件路径")
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--serial", default="")
    parser.add_argument("--wait", type=int, default=0, help="抓取前等待的秒数")
    args = parser.parse_args()

    if args.log:
        text = pathlib.Path(args.log).read_text(encoding="utf-8", errors="replace")
        result = parse_logcat(text, source=str(args.log))
    else:
        wait_for_window(args.adb, args.serial, args.wait)
        text = capture_logcat(args.adb, args.serial or None, seconds=30)
        result = parse_logcat(text, source="device_logcat")

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
