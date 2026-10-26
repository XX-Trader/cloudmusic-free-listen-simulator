"""验证桌面启动器的参数、边界与摘要结构；不启动真实客户端、不触网。"""

import json
import pathlib
import shutil
import subprocess
import unittest


PROJECT_ROOT = pathlib.Path(__file__).parents[1]
SCRIPT = PROJECT_ROOT / "start_desktop.ps1"
STUB = PROJECT_ROOT / "tests" / "fixtures" / "stub_python.cmd"


class DesktopLauncherTests(unittest.TestCase):
    def test_script_declares_expected_parameters(self):
        script = SCRIPT.read_text(encoding="utf-8")
        for parameter in (
            "$ClientPath",
            "$PythonPath",
            "$WaitSeconds",
            "$StartClient",
            "$Probe",
        ):
            self.assertIn(parameter, script)
        self.assertIn("#requires -Version 7.0", script)

    def test_script_has_no_persistence_or_injection_side_effects(self):
        script = SCRIPT.read_text(encoding="utf-8").lower()
        for forbidden in (
            "new-scheduledtaskscheduledtask",
            "register-scheduledtask",
            "new-service",
            "reg add",
            "set-executionpolicy",
            "start-process -windowstyle hidden",
        ):
            self.assertNotIn(forbidden, script)

    def test_script_does_not_reference_credential_files(self):
        script = SCRIPT.read_text(encoding="utf-8").lower()
        for forbidden in ("cookies", "os_crypt", "localprefs", "music_u"):
            self.assertNotIn(forbidden, script)

    @unittest.skipUnless(shutil.which("pwsh"), "当前环境没有 pwsh")
    def test_summary_reports_gate_with_stub_interpreter(self):
        completed = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-File",
                str(SCRIPT),
                "-ClientPath",
                str(STUB),
                "-PythonPath",
                str(STUB),
                "-WaitSeconds",
                "0",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual(summary["mode"], "desktop_launcher")
        self.assertTrue(summary["client"]["path"])
        self.assertTrue(summary["gate"]["ready_for_vip_probe"])
        self.assertIsNone(summary["probe"])
        self.assertEqual(summary["exit_code"], 0)
        self.assertNotIn("stubhash", completed.stdout.replace('"account_hash": "stubhash"', ""))

    @unittest.skipUnless(shutil.which("pwsh"), "当前环境没有 pwsh")
    def test_missing_client_path_reports_error(self):
        completed = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-File",
                str(SCRIPT),
                "-ClientPath",
                str(PROJECT_ROOT / "tests" / "fixtures" / "not-here.exe"),
                "-PythonPath",
                str(STUB),
                "-WaitSeconds",
                "0",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 5)
        summary = json.loads(completed.stdout)
        self.assertEqual(summary["exit_code"], 5)
        self.assertIn("error", summary)


if __name__ == "__main__":
    unittest.main()
