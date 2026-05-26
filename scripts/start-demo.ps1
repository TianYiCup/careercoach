# scripts/start-demo.ps1
# ─────────────────────────────────────────────────────────────────────
# One-command CareerCoach demo launcher (Windows PowerShell).
#
# Orchestrates: uvicorn (API) → vite preview (web) → cloudflared tunnel.
# Output: a Cloudflare Quick Tunnel URL + ASCII QR code printed to the
# terminal that judges can scan.
#
# Ctrl+C stops everything in the right order.
#
# Prereqs (see docs/demo-deployment.md §2):
#   · apps/api/.env exists with JWT_SECRET + DeepSeek/Qwen keys
#   · pnpm install --frozen-lockfile + uv sync done
#   · winget install Cloudflare.cloudflared
# ─────────────────────────────────────────────────────────────────────

[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$WebPort = 4173,
    [switch]$DevMode  # use `vite dev` instead of `vite build && preview`
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "  CareerCoach AI · Demo Launcher" -ForegroundColor Cyan
Write-Host "  ──────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  API  → 127.0.0.1:$ApiPort"
Write-Host "  WEB  → 127.0.0.1:$WebPort"
Write-Host "  Mode → $(if ($DevMode) { 'dev (HMR, MSW on)' } else { 'preview (built, MSW off)' })"
Write-Host ""

# ── Preflight checks ────────────────────────────────────────────────
$apiEnv = Join-Path $repoRoot 'apps\api\.env'
if (-not (Test-Path $apiEnv)) {
    Write-Host "  ✗ apps/api/.env not found." -ForegroundColor Red
    Write-Host "    Run: Copy-Item apps/api/.env.demo.example apps/api/.env" -ForegroundColor Yellow
    Write-Host "    Then edit it: set JWT_SECRET / DEEPSEEK_API_KEY / QWEN_API_KEY" -ForegroundColor Yellow
    exit 1
}

$cloudflaredCmd = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflaredCmd) {
    Write-Host "  ✗ cloudflared not on PATH." -ForegroundColor Red
    Write-Host "    Run: winget install Cloudflare.cloudflared" -ForegroundColor Yellow
    exit 1
}

# ── Cleanup hook ────────────────────────────────────────────────────
$global:demoJobs = @()

function Stop-DemoJobs {
    Write-Host ""
    Write-Host "  Stopping demo services..." -ForegroundColor DarkGray
    foreach ($j in $global:demoJobs) {
        if ($j -and $j.State -eq 'Running') {
            Stop-Job $j -ErrorAction SilentlyContinue
            Remove-Job $j -Force -ErrorAction SilentlyContinue
        }
    }
    # Quick Tunnel cloudflared sometimes detaches; also kill by name.
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "  All stopped." -ForegroundColor DarkGray
}

# Register Ctrl+C handler. PowerShell's Ctrl+C in script context raises
# a PipelineStoppedException; the finally block below catches it.
[Console]::TreatControlCAsInput = $false

try {
    # ── 1. Start uvicorn ────────────────────────────────────────────
    Write-Host "[1/4] Starting uvicorn on :$ApiPort..." -ForegroundColor Green
    $apiJob = Start-Job -ScriptBlock {
        param($root, $port)
        Set-Location (Join-Path $root 'apps\api')
        & uv run uvicorn app.main:app --host 127.0.0.1 --port $port 2>&1
    } -ArgumentList $repoRoot, $ApiPort
    $global:demoJobs += $apiJob

    # Wait for /health
    $deadline = (Get-Date).AddSeconds(30)
    $apiReady = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest "http://127.0.0.1:$ApiPort/health" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $apiReady = $true; break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $apiReady) {
        Write-Host "      ✗ uvicorn did not respond on /health within 30s." -ForegroundColor Red
        Write-Host "        Check: Receive-Job -Job `$global:demoJobs[0]" -ForegroundColor Yellow
        throw "uvicorn boot timeout"
    }
    Write-Host "      ✓ uvicorn ready" -ForegroundColor DarkGreen

    # ── 2. Start vite ───────────────────────────────────────────────
    if ($DevMode) {
        Write-Host "[2/4] Starting vite dev on :$WebPort..." -ForegroundColor Green
        $webJob = Start-Job -ScriptBlock {
            param($root, $port)
            Set-Location (Join-Path $root 'apps\web')
            & pnpm dev --host 127.0.0.1 --port $port 2>&1
        } -ArgumentList $repoRoot, $WebPort
    } else {
        Write-Host "[2/4] Building web for production..." -ForegroundColor Green
        Push-Location (Join-Path $repoRoot 'apps\web')
        try {
            & pnpm build 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "pnpm build failed (exit $LASTEXITCODE)"
            }
        } finally {
            Pop-Location
        }
        Write-Host "      ✓ build done" -ForegroundColor DarkGreen
        Write-Host "[2/4] Starting vite preview on :$WebPort..." -ForegroundColor Green
        $webJob = Start-Job -ScriptBlock {
            param($root, $port)
            Set-Location (Join-Path $root 'apps\web')
            & pnpm preview --host 127.0.0.1 --port $port 2>&1
        } -ArgumentList $repoRoot, $WebPort
    }
    $global:demoJobs += $webJob

    # Wait for vite root
    $deadline = (Get-Date).AddSeconds(20)
    $webReady = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest "http://127.0.0.1:$WebPort/" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $webReady = $true; break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $webReady) {
        Write-Host "      ✗ vite did not respond within 20s." -ForegroundColor Red
        throw "vite boot timeout"
    }
    Write-Host "      ✓ vite ready" -ForegroundColor DarkGreen

    # ── 3. Start cloudflared quick tunnel ───────────────────────────
    Write-Host "[3/4] Starting cloudflared quick tunnel..." -ForegroundColor Green
    $tunnelLog = New-TemporaryFile
    $tunnelJob = Start-Job -ScriptBlock {
        param($port, $logPath)
        & cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$port" 2>&1 | Tee-Object -FilePath $logPath
    } -ArgumentList $WebPort, $tunnelLog.FullName
    $global:demoJobs += $tunnelJob

    # cloudflared prints the URL once on stdout. Poll log file.
    $deadline = (Get-Date).AddSeconds(30)
    $tunnelUrl = $null
    while ((Get-Date) -lt $deadline -and -not $tunnelUrl) {
        Start-Sleep -Milliseconds 500
        if (Test-Path $tunnelLog.FullName) {
            $logContent = Get-Content $tunnelLog.FullName -Raw -ErrorAction SilentlyContinue
            $match = [regex]::Match($logContent, 'https://[a-z0-9-]+\.trycloudflare\.com')
            if ($match.Success) { $tunnelUrl = $match.Value }
        }
    }
    if (-not $tunnelUrl) {
        Write-Host "      ✗ Could not parse tunnel URL from cloudflared logs within 30s." -ForegroundColor Red
        Write-Host "        Log file: $($tunnelLog.FullName)" -ForegroundColor Yellow
        throw "tunnel URL parse failed"
    }
    Write-Host "      ✓ tunnel up: $tunnelUrl" -ForegroundColor DarkGreen

    # ── 4. Print QR + summary ───────────────────────────────────────
    Write-Host "[4/4] Generating QR code..." -ForegroundColor Green
    Write-Host ""
    Write-Host "  ┌────────────────────────────────────────────────────────────┐" -ForegroundColor Cyan
    Write-Host "  │  DEMO IS LIVE                                              │" -ForegroundColor Cyan
    Write-Host "  ├────────────────────────────────────────────────────────────┤" -ForegroundColor Cyan
    Write-Host "  │  URL: $($tunnelUrl.PadRight(53))│" -ForegroundColor White
    Write-Host "  │  QR : open the URL below in any browser to display the QR  │" -ForegroundColor DarkGray
    Write-Host "  │       https://api.qrserver.com/v1/create-qr-code/?size=    │" -ForegroundColor DarkGray
    Write-Host "  │       400x400&data=$($tunnelUrl.Substring(0, [Math]::Min(40, $tunnelUrl.Length)).PadRight(40))│" -ForegroundColor DarkGray
    Write-Host "  └────────────────────────────────────────────────────────────┘" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Press Ctrl+C to stop everything." -ForegroundColor Yellow
    Write-Host ""

    # Try to print a real QR via pnpm dlx qrcode-terminal. Best-effort —
    # if it fails, the user still has the URL above + the qrserver link.
    try {
        & pnpm dlx -y qrcode-terminal $tunnelUrl --small 2>$null
    } catch {
        Write-Host "  (qrcode-terminal not available — copy the URL above instead)" -ForegroundColor DarkGray
    }

    # Idle wait — surface live job output so judge-side errors stream here.
    while ($true) {
        Start-Sleep -Seconds 1
        $deadJobs = $global:demoJobs | Where-Object { $_.State -ne 'Running' }
        if ($deadJobs) {
            Write-Host ""
            Write-Host "  ✗ One of the demo services exited unexpectedly:" -ForegroundColor Red
            foreach ($j in $deadJobs) {
                Write-Host "    - $($j.Name) ($($j.State))" -ForegroundColor Red
                Receive-Job $j -Keep | Select-Object -Last 20 | Write-Host -ForegroundColor DarkGray
            }
            throw "demo service died"
        }
    }

} finally {
    Stop-DemoJobs
}
