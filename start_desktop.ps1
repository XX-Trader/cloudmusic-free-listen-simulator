#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$ClientPath = '',
    [string]$PythonPath = '',
    [int]$WaitSeconds = 60,
    [switch]$StartClient,
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$projectRoot = $PSScriptRoot

$candidatePaths = @(
    'D:\Programs\CloudMusic\cloudmusic.exe',
    "$env:LOCALAPPDATA\Programs\Netease\CloudMusic\CloudMusic.exe",
    "$env:LOCALAPPDATA\Programs\NetEase\CloudMusic\CloudMusic.exe",
    'C:\Program Files\Netease\CloudMusic\CloudMusic.exe',
    'C:\Program Files (x86)\Netease\CloudMusic\CloudMusic.exe'
)

function Resolve-ClientPath {
    <#
    .SYNOPSIS
    解析网易云桌面客户端可执行文件路径。
    #>
    if ($ClientPath) {
        if (Test-Path -LiteralPath $ClientPath) { return $ClientPath }
        return $null
    }
    $running = Get-Process -Name 'cloudmusic' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path } | Select-Object -First 1
    if ($running) { return $running.Path }
    foreach ($candidate in $candidatePaths) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Get-ClientRunning {
    <#
    .SYNOPSIS
    判断客户端进程是否在运行。
    #>
    return [bool](Get-Process -Name 'cloudmusic' -ErrorAction SilentlyContinue)
}

function Wait-ClientReady {
    <#
    .SYNOPSIS
    有界等待客户端进程出现。
    #>
    $deadline = (Get-Date).AddSeconds([Math]::Max(0, $WaitSeconds))
    while ((Get-Date) -lt $deadline) {
        if (Get-ClientRunning) { return $true }
        Start-Sleep -Seconds 1
    }
    return Get-ClientRunning
}

function Invoke-ReadOnlyScript {
    <#
    .SYNOPSIS
    以 JSON 方式调用只读 Python 脚本。
    #>
    param(
        [Parameter(Mandatory)][string]$Script,
        [string[]]$Arguments = @()
    )
    $scriptPath = Join-Path $projectRoot $Script
    $output = & $PythonPath '-B' $scriptPath @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    $payload = $null
    if ($text) {
        try { $payload = $text | ConvertFrom-Json } catch { $payload = $null }
    }
    return [pscustomobject]@{
        script = $Script
        exit_code = $exitCode
        payload = $payload
        raw = if ($payload) { $null } else { $text }
    }
}

function Resolve-PythonPath {
    <#
    .SYNOPSIS
    解析 Python 解释器，优先使用项目虚拟环境。
    #>
    if ($PythonPath) { return $PythonPath }
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) { return $venvPython }
    return 'python'
}

$PythonPath = Resolve-PythonPath
$resolvedClient = Resolve-ClientPath
$wasRunning = Get-ClientRunning
$started = $false

if ($resolvedClient -and -not $wasRunning -and $StartClient) {
    Start-Process -FilePath $resolvedClient | Out-Null
    $started = $true
}
$clientReady = if ($wasRunning) { $true } elseif ($started) { Wait-ClientReady } else { $false }

$summary = [ordered]@{
    mode = 'desktop_launcher'
    client = [ordered]@{
        path = $resolvedClient
        running = $wasRunning
        started = $started
        ready = [bool]$clientReady
    }
    python = $PythonPath
    gate = $null
    probe = $null
    exit_code = 0
}

if (-not $resolvedClient) {
    $summary.exit_code = 5
    $summary.error = '未找到网易云桌面客户端；请用 -ClientPath 指定可执行文件路径'
    $summary | ConvertTo-Json -Depth 6
    exit 5
}

$gateResult = Invoke-ReadOnlyScript -Script 'account_gate.py'
$summary.gate = $gateResult.payload
if ($gateResult.exit_code -notin @(0, 2)) {
    $summary.exit_code = 6
    $summary.error = '权益门禁执行失败'
    $summary.gate_raw = $gateResult.raw
    $summary | ConvertTo-Json -Depth 6
    exit 6
}

if ($Probe) {
    $probeResult = Invoke-ReadOnlyScript -Script 'vip_probe.py'
    $summary.probe = $probeResult.payload
    if ($probeResult.exit_code -notin @(0, 2, 3, 4)) {
        $summary.exit_code = 6
        $summary.error = '播放验证执行失败'
        $summary.probe_raw = $probeResult.raw
    } else {
        $summary.exit_code = switch ($probeResult.payload.verdict) {
            'entitled' { 0 }
            'blocked' { 3 }
            'gate_closed' { 2 }
            default { 4 }
        }
    }
} else {
    $summary.exit_code = if ($gateResult.payload.ready_for_vip_probe) { 0 } else { 2 }
}

$summary | ConvertTo-Json -Depth 6
exit $summary.exit_code
