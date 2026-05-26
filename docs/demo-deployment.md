# CareerCoach AI · Demo 部署指南

> 场景：竞赛答辩 / 30 人内测 — 评委或测试用户扫二维码 5–30 分钟玩一遍核心链路。
>
> 策略：本地跑 uvicorn + vite + 一条 Cloudflare Tunnel 暴露 vite 端口。
> 同源 vite proxy 把 `/v1` 反代到 localhost:8000，浏览器只见到一个 origin —— 零 CORS、零隧道域名同步问题。
>
> 后端从不公开暴露。

---

## 0. TL;DR

```powershell
# 一次性准备（30 分钟）
cp apps/api/.env.demo.example apps/api/.env
# 编辑 apps/api/.env：填 JWT_SECRET、DEEPSEEK_API_KEY、QWEN_API_KEY

cp apps/web/.env.example apps/web/.env.local
# 默认值就行（VITE_API_BASE_URL=/v1 走 proxy）

winget install Cloudflare.cloudflared      # Windows
# brew install cloudflared                 # macOS
# https://github.com/cloudflare/cloudflared/releases  · 手动下载

# 每次答辩前（3 分钟）
.\scripts\start-demo.ps1                   # Windows
# ./scripts/start-demo.sh                  # macOS / Linux

# → 终端打印一个 https://<random>.trycloudflare.com 二维码
# → 评委扫码 → 浏览器打开 → 用 MSW 假账号 13800138000 + code 123456 直接登录
```

---

## 1. 架构

```
┌──────────────┐   HTTPS   ┌──────────────────────┐   HTTP   ┌─────────────┐
│  评委手机    │ ────────→ │ Cloudflare Tunnel    │ ───────→ │ 你的电脑     │
│  浏览器      │           │ (trycloudflare.com)  │          │              │
└──────────────┘           └──────────────────────┘          │  vite :5173 │
                                                              │     ↓        │
                                                              │  uvicorn    │
                                                              │  :8000      │
                                                              └─────────────┘
```

- 评委只看到 `https://xxx.trycloudflare.com`
- cloudflared 把流量转发到你电脑的 `localhost:5173`（vite）
- vite 把 `/v1/*` 和 `/health` 反代到 `localhost:8000`（uvicorn）
- 浏览器 JS 看到的 `/v1/*` 是同源相对路径 → 没有 CORS preflight
- WebSocket（副驾）通过 vite proxy `ws: true` 透传

---

## 2. 一次性准备

### 2.1 安装 cloudflared

| OS | 命令 |
|---|---|
| Windows | `winget install Cloudflare.cloudflared` |
| macOS | `brew install cloudflared` |
| Linux | 从 https://github.com/cloudflare/cloudflared/releases 下 .deb / .rpm |

验证：
```
cloudflared --version
```

> **不用 Cloudflare 账号** —— Quick Tunnel 无需登录，每次启动随机域名。
> 缺点：每次重启 URL 会变，二维码要重生成。优点：零设置。

### 2.2 准备 .env

```powershell
# 后端 demo 环境变量
Copy-Item apps/api/.env.demo.example apps/api/.env

# 编辑 apps/api/.env：
# JWT_SECRET=  ← 必须改！生成命令见模板注释
# DEEPSEEK_API_KEY=sk-xxx
# QWEN_API_KEY=sk-xxx
```

```powershell
# 前端 vite proxy 环境变量
Copy-Item apps/web/.env.example apps/web/.env.local
# 默认值（VITE_API_BASE_URL=/v1）就行
```

### 2.3 装依赖

```powershell
pnpm install --frozen-lockfile
cd apps/api ; uv sync ; cd ../..
```

### 2.4 试启动一次（不开 tunnel）

```powershell
# Terminal 1
cd apps/api
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2
cd apps/web
pnpm dev --host 127.0.0.1 --port 5173

# 浏览器打开 http://127.0.0.1:5173 验证：
# 1. 登录页能进
# 2. MSW 假账号 13800138000 + code 123456 能登录
# 3. 沙盘对练能跑通（看 uvicorn 终端有 POST /v1/sessions 日志）
```

---

## 3. 答辩当天启动

### 3.1 一键脚本（推荐）

```powershell
.\scripts\start-demo.ps1
```

脚本会：
1. 起 uvicorn（后台）
2. 等待 `/health` 200
3. 起 vite preview（先 build 一次再 preview，避免 dev HMR 噪音）
4. 等待 vite 起来
5. 起 cloudflared quick tunnel 指向 vite
6. 抓取 tunnel URL
7. 用 ANSI block 字符在终端打出二维码（无需额外工具）
8. Ctrl+C → 顺序停止 tunnel / vite / uvicorn

### 3.2 手动启动（脚本炸了用）

```powershell
# Terminal 1：后端
cd apps/api
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2：前端（生产构建 + preview 模式，比 dev 稳）
cd apps/web
pnpm build
pnpm preview --host 127.0.0.1 --port 4173

# Terminal 3：cloudflared tunnel
cloudflared tunnel --url http://127.0.0.1:4173
# 输出形如：
#   2026-05-26 ... INF +--------------------------------------------------------------------------------+
#   2026-05-26 ... INF | Your quick Tunnel has been created! Visit it at:                              |
#   2026-05-26 ... INF |   https://random-words-12345.trycloudflare.com                                |
#   2026-05-26 ... INF +--------------------------------------------------------------------------------+

# 复制 URL → 生成二维码：
#   https://api.qrserver.com/v1/create-qr-code/?size=400x400&data=https://random-words-12345.trycloudflare.com
# 或本地：
pnpm dlx qrcode-terminal "https://random-words-12345.trycloudflare.com"
```

---

## 4. 演示流程（评委视角）

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | 评委扫码 / 打开链接 | 看到登录页 |
| 2 | 输手机号 `13800138000` | "发送验证码" |
| 3 | MSW 控制台打印 code（你给评委看屏幕，或事先约定 `123456`） | 进年龄门 |
| 4 | 输 `2000`（成年） | 进首页 |
| 5 | 点 "立刻进入沙盘" → 选 mission | 进沙盘 |
| 6 | 对线 5 回合 → 结束 | 看评分页 + 雷达 |
| 7 | 回首页 → 副驾 / 复盘 / 弱点 / Wrapped | 全链路覆盖 |

---

## 5. 故障应急

### 5.1 cloudflared tunnel URL 不通

- 检查 cloudflared 日志有没有 "Tunnel connection lost"
- 重启 cloudflared，URL 会变，重新发码
- 兜底：直接用本地 IP 给评委连同一 WiFi（`ipconfig` 看 IPv4 + http://192.168.x.x:5173）

### 5.2 uvicorn 启动失败 ValueError: jwt_secret

- `apps/api/.env` 里 `JWT_SECRET` 还是占位符或不到 32 字节
- 生成：`python -c "import secrets; print(secrets.token_urlsafe(48))"`

### 5.3 浏览器报 401 / 403

- MSW 假账号在生产 build 默认**关闭** —— `import.meta.env.DEV` 为 false 时不挂载
- 如果想保留 MSW（demo 完全不走真 API），把 vite preview 改成 vite dev（保留 `--mode development`）：
  ```powershell
  cd apps/web
  pnpm dev --host 127.0.0.1 --port 5173
  ```
  cloudflared 改指 5173。

### 5.4 LLM 调用慢 / 超时

- 检查 `apps/api/.env` 里 DEEPSEEK_API_KEY / QWEN_API_KEY
- DeepSeek 限流 → 自动切通义（看 uvicorn 日志 `llm_fallback_to_qwen`）
- 都炸 → 没救，准备好答辩 PPT 兜底视频

### 5.5 WebSocket（副驾）连不上

- 检查 vite.config.ts 里 proxy `'/v1': { ws: true }` 在不在
- cloudflared Quick Tunnel 默认支持 WS，无需额外配置
- 实在不行答辩跳过副驾，沙盘 + 复盘已经够看

---

## 6. 兜底视频建议

按 `careercoach-engineering §9.3` 应急流程，提前录好这 6 段，演示挂了立刻切：

1. 登录 → 年龄门 → 首页 dashboard 全屏 30s
2. 沙盘选 mission → 对线 5 回合 → 评分 60s
3. 副驾启动 → K 给提示 30s
4. 复盘上传 → 三栏分析 45s
5. 弱点画像 全屏 20s
6. Wrapped 卡生成 + 下载 30s

录制工具：OBS / Mac QuickTime / Win 自带 Snipping Tool。

---

## 7. 关联文档

- [foundation §3.8 部署 / CI](./careercoach-foundation.md#38-部署--ci)
- [foundation §4.5 部署架构](./careercoach-foundation.md#45-部署架构v1-单机)
- [engineering §9 应急流程](./careercoach-engineering.md#9-应急流程)
- [PRD §12 答辩 demo 逐秒级脚本](./careercoach-prd-v2.md#12-答辩-demo-逐秒级脚本)
