# CareerCoach AI 🥊

> 中文语境的对话练习教练 AI · 面向在校大学生 + 实习生
> "不教你说违心话，只教你说真话还能赢。"

[![status](https://img.shields.io/badge/status-Sprint%200-orange)]()
[![license](https://img.shields.io/badge/license-Proprietary-red)]()
[![team](https://img.shields.io/badge/team-TianYiCup-blueviolet)]()

---

## 🎯 项目简介

CareerCoach AI 是一款 AI 对话教练，帮 18-25 岁的大学生 / 实习生 / 应届毕业生面对**导师、老板、HR、室友、父母**等高压对话场景敢说真话还能赢。

**三大核心模式**：
- 🥊 **沙盘对练**：和 AI 扮演的对手练习对话，AI 教练实时给提示
- 🎧 **实战副驾**：真实对话中耳机里小声提示下一句怎么回（仅 Web/EXE）
- 🔍 **复盘师**：上传聊天截图 → AI 标记每句的得失分

**Mascot · 教练 K**：紫色 mochi 拳手，嘴硬心软，不爹味。

---

## 🚀 快速开始

```bash
# 1. 克隆 + 配置 hooks
git clone git@github.com:TianYiCup/careercoach.git
cd careercoach
./scripts/setup.sh

# 2. 安装依赖（前端）
pnpm install

# 3. 安装依赖（后端）
cd apps/api && uv sync && cd ../..

# 4. 复制环境变量
cp .env.example .env
# 编辑 .env 填写你的 LLM API key 等

# 5. 一键启动开发环境
docker compose up -d   # postgres + redis + qdrant + langfuse
pnpm --filter web dev  # Web 端
# 后端: cd apps/api && uv run uvicorn app.main:app --reload
```

---

## 📁 项目结构

```
careercoach/
├── apps/
│   ├── web/            # React + Vite + Tailwind（Web + EXE 共享代码）
│   ├── wxapp/          # 微信小程序（Taro 4 + React）
│   └── api/            # FastAPI + LangGraph + PostgreSQL
├── packages/
│   └── shared/         # 跨端共享类型 + 工具
├── docs/               # PRD / 设计图纸 / 项目地基 / 工程规范
├── scripts/            # 工具脚本
└── .github/            # CI / PR 模板 / CODEOWNERS
```

---

## 📚 核心文档（开干前必读）

| 文档 | 作用 | 路径 |
|------|------|------|
| 🌟 **AI 上下文** | AI 助手自动加载 | [`CLAUDE.md`](./CLAUDE.md) |
| 🏗️ **项目地基** | 边界 + NFR + 技术栈 + 架构 | [`docs/careercoach-foundation.md`](./docs/careercoach-foundation.md) |
| 📋 **PRD** | 产品需求 + 验收标准 + 红线 + 答辩脚本 | [`docs/careercoach-prd-v2.md`](./docs/careercoach-prd-v2.md) |
| 🎨 **设计图纸** | Vivid Coach + Mascot K + 三端 UI | [`docs/careercoach-design-spec.md`](./docs/careercoach-design-spec.md) |
| ⚙️ **工程规范** | Git/CI/分工/Sprint Backlog | [`docs/careercoach-engineering.md`](./docs/careercoach-engineering.md) |
| 💡 **总体方案** | 创意 + 主推/备选 | [`docs/careercoach-vision.md`](./docs/careercoach-vision.md) |

---

## 🤝 团队约定（雷打不动）

> 我们没付费 GitHub Team，所以 Branch Protection 不强制。**靠纪律**。

🚨 **任何人永不直推 main**——本地 pre-push hook 会拦截
🚨 **任何 PR 必须 1 个 approval 才能合并**
🚨 **CI 红 ❌ 的 PR 不允许合并**（即使能技术上 merge）
🚨 **提交信息走 [Conventional Commits](https://www.conventionalcommits.org/)**

详见 [`docs/careercoach-engineering.md`](./docs/careercoach-engineering.md)。

---

## 👥 团队

| 角色 | GitHub | 主负责 |
|------|--------|--------|
| Owner | [@patient-Zero-0](https://github.com/patient-Zero-0) | 待分工 |
| Member | [@k4392](https://github.com/k4392) | 待分工 |

---

## 📅 时间线

| Sprint | 目标 | 周次 |
|--------|------|------|
| **Sprint 0** | 基础设施 + Spike 验证 | W1 |
| **Sprint 1** | 沙盘 MVP | W2 |
| **Sprint 2** | 副驾 + 复盘 + Wrapped | W3 |
| **Sprint 3** | UAT + Polish | W4 |
| **Demo Day** | 答辩 | W5 |

---

## 📜 License

Proprietary · 2026 TianYiCup Team · 详见 [LICENSE](./LICENSE)
