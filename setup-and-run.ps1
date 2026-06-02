param([switch]$Web, [switch]$Persist)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$mode = if ($Web) { '网页版' } else { '桌面版' }
if ($Persist) { $mode = "$mode + 持久化" }
Write-Host "================================================="
Write-Host " CareerCoach AI - 一键配置 + 启动 ($mode)"
Write-Host "================================================="
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }

if (-not (Have 'uv')) {
  Write-Host ""
  Write-Host "[1] 安装 uv (Python 工具链)..."
  powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Have 'uv')) { throw "uv 不可用，请手动安装 https://docs.astral.sh/uv/ 后重试" }
Write-Host "[1] uv OK"

Write-Host ""
Write-Host "[2] 安装后端依赖 (uv sync，首次需联网，请稍候)..."
Push-Location "$root\apps\api"
try { uv sync } finally { Pop-Location }

if (-not (Test-Path "$root\apps\api\.env")) {
  Write-Warning "未找到 apps\api\.env —— 请先复制 apps\api\.env.example 为 .env 并填入密钥，否则沙盘/副驾无法正常工作。"
}

$persistPrefix = ''
if ($Persist) {
  Write-Host ""
  Write-Host "[2.5] 启用数据持久化 (Postgres)..."
  if (-not (Have 'docker')) {
    throw "需要 Docker 才能用 -Persist 持久化。请装 Docker Desktop 后重试，或去掉 -Persist 用内存模式。"
  }
  Push-Location $root
  try {
    docker compose up -d postgres
    Write-Host "    等待 Postgres 就绪..."
    $dbok = $false
    for ($i = 0; $i -lt 30; $i++) {
      Start-Sleep -Seconds 2
      docker exec careercoach-postgres pg_isready -U postgres *> $null
      if ($LASTEXITCODE -eq 0) { $dbok = $true; break }
    }
    if (-not $dbok) { throw "Postgres 30 秒内未就绪，请检查 Docker Desktop 是否已启动" }
  } finally { Pop-Location }
  Write-Host "    运行数据库迁移 (alembic upgrade head)..."
  Push-Location "$root\apps\api"
  try { uv run alembic upgrade head } finally { Pop-Location }
  # The backend window must inherit PERSIST_BACKEND so every repo wires
  # to Postgres; injected into that window's command below.
  $persistPrefix = "`$env:PERSIST_BACKEND='postgres'; "
  Write-Host "    持久化已启用：用户 / 弱点 / 策略数据将落库，重启不丢。"
}

Write-Host ""
Write-Host "[3] 启动后端 (新窗口, http://127.0.0.1:8000)..."
Start-Process powershell -ArgumentList @('-NoExit','-Command',"Set-Location '$root\apps\api'; ${persistPrefix}Write-Host '后端运行中 —— 关闭此窗口即停止后端'; uv run uvicorn app.main:app --host 127.0.0.1 --port 8000")
Write-Host "等待后端就绪..."
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 2
  try { if ((Invoke-WebRequest 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { $ok = $true; break } } catch {}
}
if (-not $ok) { Write-Warning "后端 60 秒内未就绪，请查看后端窗口的报错" }

if ($Web) {
  Write-Host ""
  Write-Host "[4] 准备网页版 (需 Node.js + pnpm)..."
  if (-not (Have 'node')) {
    Write-Host "    未检测到 Node，尝试用 winget 安装 (可能需要几分钟)..."
    try { winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements } catch {}
    $env:Path = "$env:ProgramFiles\nodejs;$env:Path"
  }
  if (-not (Have 'node')) { throw "Node.js 不可用，请从 https://nodejs.org 装 LTS 后重试网页版，或改用桌面版" }
  if (-not (Have 'pnpm')) { try { corepack enable; corepack prepare pnpm@latest --activate } catch {} }
  if (-not (Have 'pnpm')) { try { npm install -g pnpm } catch {} }
  if (-not (Have 'pnpm')) { throw "pnpm 不可用，请手动运行 'npm i -g pnpm' 后重试" }
  Write-Host "    安装前端依赖 (pnpm install，体积较大，请耐心等待)..."
  Push-Location "$root\apps\web"
  try { pnpm install } finally { Pop-Location }
  Write-Host "    启动网页版开发服务器 (pnpm dev)..."
  Start-Process powershell -ArgumentList @('-NoExit','-Command',"Set-Location '$root\apps\web'; pnpm dev")
  Start-Sleep -Seconds 6
  Start-Process "http://localhost:5173"
  Write-Host ""
  Write-Host "完成！浏览器应已打开 http://localhost:5173"
} else {
  $exe = @("$root\desktop\app.exe", "$root\apps\web\src-tauri\target\release\app.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($exe) {
    Write-Host ""
    Write-Host "[4] 打开桌面应用..."
    Start-Process $exe
    Write-Host "完成！"
  } else {
    Write-Warning "未找到桌面应用 app.exe。请先打包(cd apps\web; pnpm tauri build)，或改用网页版(启动网页版.bat)。后端已在运行。"
  }
}
Write-Host ""
Write-Host "提示：沙盘/副驾若不出真实结果，多为 .env 密钥未填 或 网络问题。"