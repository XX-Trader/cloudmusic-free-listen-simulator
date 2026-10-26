@echo off
rem Stub interpreter: prints a fixed gate JSON for launcher tests. No network.
echo {"logged_in": true, "account_hash": "stubhash", "rights_remaining_time": 1800000, "ready_for_vip_probe": true, "source": "stub", "claim_submitted": false}
exit /b 0
