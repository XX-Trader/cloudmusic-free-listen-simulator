#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('success', 'short-stay', 'server-rejected', 'duplicate')]
    [string]$Scenario = 'success',
    [string]$PythonPath = 'python'
)
$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
& $PythonPath (Join-Path $PSScriptRoot 'simulator.py') --scenario $Scenario
if ($LASTEXITCODE -ne 0) { throw "模拟器退出码：$LASTEXITCODE" }
