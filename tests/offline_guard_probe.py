"""在子进程中安装审计钩子，证明离线模拟不触碰网络、子进程或文件写入。"""

import json
import os
import sys


FORBIDDEN_EVENTS = {
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
}
FORBIDDEN_PREFIXES = ("socket.", "urllib.")


def _install_audit_hook():
    """安装全局审计钩子；返回记录到的违规事件列表。"""
    violations = []

    def hook(event, args):
        if event in FORBIDDEN_EVENTS or event.startswith(FORBIDDEN_PREFIXES):
            violations.append(event)
        elif event == "open" and len(args) > 1 and args[1]:
            mode = args[1]
            if isinstance(mode, str) and any(flag in mode for flag in ("w", "a", "x", "+")):
                violations.append("file-write")

    sys.addaudithook(hook)
    return violations


def main() -> int:
    """运行全部离线场景并输出审计结果；无参数；有违规时返回 1。"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import simulator

    violations = _install_audit_hook()
    results = {}
    for scenario in simulator.SCENARIOS:
        result = simulator.simulate(scenario)
        results[scenario] = {
            "state": result["state"],
            "demo_balance_minutes": result["demo_balance_minutes"],
        }
    print(
        json.dumps(
            {"violations": sorted(set(violations)), "results": results},
            ensure_ascii=False,
        )
    )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
