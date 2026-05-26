#!/usr/bin/env bash
# scripts/start-demo.sh
# ─────────────────────────────────────────────────────────────────────
# One-command CareerCoach demo launcher (macOS / Linux).
#
# See docs/demo-deployment.md for prereqs. PowerShell sibling at
# scripts/start-demo.ps1.
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-4173}"
DEV_MODE="${DEV_MODE:-0}"  # set to 1 to use vite dev (HMR + MSW) instead of preview

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cyan() { printf '\033[36m%s\033[0m\n' "$*"; }
gray() { printf '\033[90m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

echo
cyan "  CareerCoach AI · Demo Launcher"
gray "  ──────────────────────────────────"
echo "  API  → 127.0.0.1:${API_PORT}"
echo "  WEB  → 127.0.0.1:${WEB_PORT}"
if [[ "$DEV_MODE" == "1" ]]; then
    echo "  Mode → dev (HMR, MSW on)"
else
    echo "  Mode → preview (built, MSW off)"
fi
echo

# ── Preflight ─────────────────────────────────────────────────────────
if [[ ! -f "${REPO_ROOT}/apps/api/.env" ]]; then
    red "  ✗ apps/api/.env not found."
    yellow "    Run: cp apps/api/.env.demo.example apps/api/.env"
    yellow "    Then edit it: set JWT_SECRET / DEEPSEEK_API_KEY / QWEN_API_KEY"
    exit 1
fi

if ! command -v cloudflared >/dev/null 2>&1; then
    red "  ✗ cloudflared not on PATH."
    yellow "    macOS: brew install cloudflared"
    yellow "    Linux: see https://github.com/cloudflare/cloudflared/releases"
    exit 1
fi

# ── Cleanup ──────────────────────────────────────────────────────────
PIDS=()
cleanup() {
    echo
    gray "  Stopping demo services..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    # cloudflared sometimes spawns children; kill by name as belt-and-braces.
    pkill -f 'cloudflared tunnel' 2>/dev/null || true
    gray "  All stopped."
}
trap cleanup EXIT INT TERM

# ── 1. uvicorn ───────────────────────────────────────────────────────
green "[1/4] Starting uvicorn on :${API_PORT}..."
(
    cd "${REPO_ROOT}/apps/api"
    exec uv run uvicorn app.main:app --host 127.0.0.1 --port "${API_PORT}"
) &
PIDS+=($!)

# Wait for /health
for i in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
        green "      ✓ uvicorn ready"
        break
    fi
    sleep 0.5
    if [[ $i -eq 60 ]]; then
        red "      ✗ uvicorn did not respond on /health within 30s."
        exit 1
    fi
done

# ── 2. vite ──────────────────────────────────────────────────────────
if [[ "$DEV_MODE" == "1" ]]; then
    green "[2/4] Starting vite dev on :${WEB_PORT}..."
    (
        cd "${REPO_ROOT}/apps/web"
        exec pnpm dev --host 127.0.0.1 --port "${WEB_PORT}"
    ) &
    PIDS+=($!)
else
    green "[2/4] Building web for production..."
    (
        cd "${REPO_ROOT}/apps/web"
        pnpm build >/dev/null
    )
    green "      ✓ build done"
    green "[2/4] Starting vite preview on :${WEB_PORT}..."
    (
        cd "${REPO_ROOT}/apps/web"
        exec pnpm preview --host 127.0.0.1 --port "${WEB_PORT}"
    ) &
    PIDS+=($!)
fi

# Wait for vite
for i in $(seq 1 40); do
    if curl -sf "http://127.0.0.1:${WEB_PORT}/" >/dev/null 2>&1; then
        green "      ✓ vite ready"
        break
    fi
    sleep 0.5
    if [[ $i -eq 40 ]]; then
        red "      ✗ vite did not respond within 20s."
        exit 1
    fi
done

# ── 3. cloudflared quick tunnel ──────────────────────────────────────
green "[3/4] Starting cloudflared quick tunnel..."
TUNNEL_LOG="$(mktemp)"
(
    cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:${WEB_PORT}" 2>&1 | tee "$TUNNEL_LOG"
) &
PIDS+=($!)

# Parse the URL
TUNNEL_URL=""
for i in $(seq 1 60); do
    sleep 0.5
    if [[ -f "$TUNNEL_LOG" ]]; then
        TUNNEL_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1)"
        if [[ -n "$TUNNEL_URL" ]]; then break; fi
    fi
done
if [[ -z "$TUNNEL_URL" ]]; then
    red "      ✗ Could not parse tunnel URL within 30s. Log: $TUNNEL_LOG"
    exit 1
fi
green "      ✓ tunnel up: ${TUNNEL_URL}"

# ── 4. Summary + QR ──────────────────────────────────────────────────
green "[4/4] Generating QR code..."
echo
cyan "  ┌────────────────────────────────────────────────────────────┐"
cyan "  │  DEMO IS LIVE                                              │"
cyan "  ├────────────────────────────────────────────────────────────┤"
printf "  │  \033[37mURL: %-54s\033[36m│\n" "${TUNNEL_URL}"
cyan "  └────────────────────────────────────────────────────────────┘"
echo
yellow "  Press Ctrl+C to stop everything."
echo

# Try to print a real QR; fall back to qrserver link if not available.
if command -v qrencode >/dev/null 2>&1; then
    qrencode -t ANSIUTF8 "${TUNNEL_URL}"
elif command -v npx >/dev/null 2>&1; then
    npx -y qrcode-terminal "${TUNNEL_URL}" --small 2>/dev/null || \
        gray "  (qrcode-terminal not available — copy URL above)"
else
    gray "  (no QR tool installed — open this in browser to render:"
    gray "   https://api.qrserver.com/v1/create-qr-code/?size=400x400&data=${TUNNEL_URL})"
fi

# Idle wait
wait
