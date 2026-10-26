"""验证真实入口：Python CLI、PowerShell 包装器与离线零网络守卫。"""

import ast
import json
import os
import pathlib
import shutil
import subprocess
import sys
import unittest


PROJECT_ROOT = pathlib.Path(__file__).parents[1]
RANDOM_FIELDS = ("task_id", "receipt_id")


def _normalize(payload):
    """剔除每次运行都会变化的随机标识，便于比较两次运行的语义结果。"""
    normalized = json.loads(json.dumps(payload))
    receipt = normalized.get("receipt")
    if isinstance(receipt, dict):
        for field in RANDOM_FIELDS:
            receipt.pop(field, None)
    return normalized


class EntryPointTests(unittest.TestCase):
    def _run(self, command):
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
        )

    def test_python_cli_runs_every_scenario(self):
        import simulator

        for scenario in simulator.SCENARIOS:
            with self.subTest(scenario=scenario):
                completed = self._run(
                    [sys.executable, "-B", "simulator.py", "--scenario", scenario]
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["scenario"], scenario)
                self.assertEqual(payload["mode"], "offline_demo")
                self.assertEqual(payload["network_requests"], 0)
                self.assertFalse(payload["real_entitlement_granted"])
                self.assertTrue(payload["receipt"]["demo"])

    def test_python_cli_rejects_unknown_scenario(self):
        completed = self._run(
            [sys.executable, "-B", "simulator.py", "--scenario", "real-claim"]
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn("real_entitlement_granted", completed.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "当前环境没有 pwsh")
    def test_powershell_wrapper_matches_python_output(self):
        import simulator

        for scenario in simulator.SCENARIOS:
            with self.subTest(scenario=scenario):
                completed = self._run(
                    ["pwsh", "-NoProfile", "-File", "run.ps1", "-Scenario", scenario]
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                expected = json.loads(
                    self._run(
                        [sys.executable, "-B", "simulator.py", "--scenario", scenario]
                    ).stdout
                )
                self.assertEqual(
                    _normalize(json.loads(completed.stdout)),
                    _normalize(expected),
                )

    def test_offline_core_opens_no_network_or_subprocess(self):
        completed = self._run(
            [sys.executable, "-B", os.path.join("tests", "offline_guard_probe.py")]
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["violations"], [])
        self.assertEqual(
            report["results"],
            {
                "success": {"state": "demo_completed", "demo_balance_minutes": 30},
                "short-stay": {"state": "rejected", "demo_balance_minutes": 0},
                "server-rejected": {"state": "rejected", "demo_balance_minutes": 0},
                "duplicate": {"state": "demo_completed", "demo_balance_minutes": 30},
            },
        )

    def test_simulator_source_has_no_network_capable_imports(self):
        tree = ast.parse((PROJECT_ROOT / "simulator.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(
            imported & {"socket", "urllib", "http", "requests", "ssl", "ftplib"},
            set(),
        )


if __name__ == "__main__":
    unittest.main()
