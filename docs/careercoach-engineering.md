# CareerCoach AI — 工程规范 v1.0

> **目标**：让 2 人小组用 4 周交付一个可演示、可上线、不翻车的产品。
>
> **核心理念**：契约先行、互不阻塞、每天可见、持续合并。
>
> 关联：[Foundation](./careercoach-foundation.md) · [PRD v2](./careercoach-prd-v2.md) · [Design Spec](./careercoach-design-spec.md)
> 状态：Locked for Sprint 0

---

## 0. 文档导航

1. [团队与分工](#1-团队与分工)
2. [Git 工作流](#2-git-工作流)
3. [质量闸门 4 层](#3-质量闸门-4-层)
4. [编码规范](#4-编码规范)
5. [测试规范](#5-测试规范)
6. [并行开发实操](#6-并行开发实操)
7. [4 周 Sprint Backlog（按天分到人）](#7-4-周-sprint-backlog按天分到人)
8. [Ready-to-paste 配置文件](#8-ready-to-paste-配置文件)
9. [应急流程](#9-应急流程)
10. [Day 0 启动清单](#10-day-0-启动清单)

---

## 1. 团队与分工

### 1.1 角色（请把 A / B 替换成真实名字）

| 代号 | 主域 | 副域 | 共同职责 |
|------|------|------|---------|
| **A · 后端主**（建议技术更熟的人）| API · LangGraph · LLM/ASR/审核 · DB · 部署 | Web 业务接入（mock 完成后）| 设计评审 · 答辩演练 · 互相 Review |
| **B · 前端主**（建议设计感更强的人）| Web · Tauri EXE · 小程序（Taro）· 设计还原 · Mascot · Wrapped 卡 | API mock 维护 · Prompt 调优 | 同上 |

### 1.2 分工原则（吵架前先看这个）

| 原则 | 落地 |
|------|------|
| **接口契约先行** | OpenAPI Schema 在 W1D2 锁定 → A 和 B 都按 Schema 写 |
| **Mock 优先于真实** | B 用 MSW mock 整个 API，A 没写完前 B 也能跑 |
| **垂直 ownership** | 每个 Epic 有"Primary 责任人"（PR 里第一行加 `Owner: A`）|
| **互助而不抢活** | 每天站会指认明天的"被卡块"，预先 unblock |
| **凌晨 2 点不写代码** | 4 周不是冲刺，是马拉松。质量比时数重要 |

### 1.3 两人分工大表

| Epic / 模块 | A 主负责 | B 主负责 |
|-------------|---------|---------|
| 项目初始化（W1D1）| ✅ Bash + Docker + CI | ✅ Vite + Taro + Tauri |
| FastAPI 骨架 | ✅ | — |
| OpenAPI Schema | ✅ 起草 | ✅ Review + 用 |
| LangGraph 多 Agent | ✅ | — |
| LLM Provider 抽象 | ✅ | — |
| 内容审核服务 | ✅ | — |
| ASR / TTS 接入 | ✅ | — |
| 数据库 + 迁移 | ✅ | — |
| 部署 & CI | ✅ | — |
| 设计 Token | — | ✅ |
| 通用 UI 组件库 | — | ✅ |
| 业务组件（HintCard / ScoreRadar / WrappedCard）| — | ✅ |
| Web 沙盘对练房 UI | — | ✅ |
| Web 评分页 + 复盘三栏 | — | ✅ |
| Tauri EXE 打包 | — | ✅ |
| 副驾 HUD UI | — | ✅ |
| 小程序 Taro 全栈 | — | ✅ |
| Mascot 教练 K（Rive + Lottie 兜底）| — | ✅ |
| Wrapped 卡 Canvas 渲染 | ✅（服务端）| ✅（客户端触发）|
| 红线监控仪表盘 | ✅ | — |
| 200 对抗样本数据集 | 共建 | 共建 |
| 30 人盲测招募 + 跑 | 共建 | 共建 |
| 答辩 PPT + 演示脚本 | 共建 | 共建 |

### 1.4 一周节奏（雷打不动）

```
周一 09:00  Sprint Planning（30 min）
周一-周五  每天 09:30 异步站会（在群里发 3 句话：昨天 / 今天 / 卡点）
周三 14:00  中段对齐（30 min · 看进度 + 协调接口）
周五 16:00  Sprint Demo + Retro（60 min · 互演当周成果 + 改进点）
周六-周日  休息（如确实赶进度，最多干一天）
```

### 1.5 沟通机制

| 场景 | 工具 | 响应时长 |
|------|------|---------|
| 紧急（CI 挂、生产挂、合规问题）| 电话 | 立刻 |
| 一般问题 | 微信群 @ | ≤ 2h |
| 异步讨论（PR review / 设计反馈）| GitHub Issue / PR | ≤ 24h |
| 长文记录 | Notion / 本仓库 docs/ | — |

**禁止**：在微信群里吵架超过 5 条还没结论 → 立刻开 30 分钟会面对面解决。

---

## 2. Git 工作流

### 2.1 选型：**GitHub Flow**（适合 2 人 / 4 周 / 高频集成）

不用 Git Flow（develop+release+hotfix 太复杂）；不用纯 trunk（少了 PR review 的把关）。

```
main（永远可演示，受保护）
 ├─ feat/A-langgraph-skeleton
 ├─ feat/B-mascot-rive
 ├─ fix/B-tauri-build-win
 └─ chore/A-ci-workflow
```

### 2.2 分支命名规则（强制）

```
<type>/<owner>-<short-description>
```

- **type**：`feat` / `fix` / `refactor` / `chore` / `docs` / `test` / `perf`
- **owner**：你的名字首字母（A 或 B），便于一眼看是谁的活
- **description**：英文 kebab-case，≤ 5 词

✅ 好例子：
- `feat/A-coach-agent-3-tier-hints`
- `fix/B-wxapp-share-card-canvas`
- `chore/A-github-actions-setup`

❌ 坏例子：
- `develop`（一律不用）
- `feature_branch_2`（无意义）
- `B-fix`（没说改什么）

### 2.3 提交信息（Conventional Commits · 强制）

```
<type>(<scope>): <subject>

<body 可选>

<footer 可选>
```

| type | 含义 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修 bug |
| `refactor` | 重构（不改行为）|
| `docs` | 仅文档 |
| `style` | 代码格式（不改逻辑）|
| `test` | 加/改测试 |
| `chore` | 工具链 / 配置 |
| `perf` | 性能优化 |
| `ci` | CI 配置 |
| `build` | 构建系统 |
| `revert` | 回滚 |

| scope（建议范围）| 含义 |
|------|------|
| `web` / `wxapp` / `api` | 三端 |
| `agent` / `llm` / `asr` / `mod` | 后端模块 |
| `mascot` / `card` | 业务组件 |
| `ci` / `docs` / `db` | 杂项 |

✅ 好例子：
```
feat(api): add /sessions/turns SSE endpoint
fix(wxapp): handle wx.login token expiry
refactor(agent): extract Coach agent prompt into yaml
docs(prd): add §3.0.7 red-line story matrix
chore(ci): pin pnpm to 9.12
```

❌ 坏例子：
- `update code` （太模糊）
- `Fix bug` （type 大写 + 未说改了什么）
- `feat: 改了登录` （没用英文 type）

### 2.4 PR 流程

```
1. 创建分支：git checkout -b feat/A-langgraph-skeleton
2. 写代码 + 写测试（TDD 不强制但鼓励）
3. 本地 pre-commit 通过
4. push：git push -u origin feat/A-langgraph-skeleton
5. 在 GitHub 上开 PR（用模板，必填红线 checkbox）
6. CI 自动跑
7. 找另一人 Review（≤ 24h SLA）
8. 解决评论 / 重新 push
9. Approve + Squash merge to main
10. 删除分支（GitHub 自动）
```

### 2.5 PR 大小限制

- **目标**：单 PR ≤ 800 行（含 +/-，不含 lock 文件）
- **超 800**：必须拆，CI 会标黄
- **超 1500**：CI 直接 block，不允许合并

**拆分策略**：
- 大功能 → 多个小 PR（feat/A-coach-step1, step2, step3）
- 重构 + 业务 → 拆开（先重构 PR，再业务 PR）
- 第一个 PR 只搭骨架，后续 PR 填肉

### 2.6 合并策略

- **Squash and merge** 默认（main 历史干净）
- 不要 Rebase merge（GitHub 上视觉乱）
- 不要 Merge commit（多余的 merge node）

### 2.7 Tag 与 Release

- Tag 格式：`v0.1.0` (semver)
- W2 末：`v0.1.0-mvp`（沙盘 MVP）
- W3 末：`v0.2.0-feature-complete`
- W4 末：`v0.3.0-rc1`
- 答辩日：`v1.0.0-demo`

---

## 3. 质量闸门 4 层

> 越往后越严，越往前修复成本越低。

```
代码完成
  ↓
🟢 L1 · 本地（pre-commit）        ← 5 秒
  ↓
🟡 L2 · CI（GitHub Actions）       ← 5 分钟
  ↓
🟠 L3 · PR Review                  ← 24 小时内
  ↓
🔴 L4 · 合并保护（Branch Protection）← 不可绕过
  ↓
main 分支
```

### 3.1 L1 · 本地闸门（pre-commit）

工具：[pre-commit.com](https://pre-commit.com)（安装：`uv tool install pre-commit`）

**触发时机**：每次 `git commit` 前

**检查项**：

| 检查 | 工具 | 失败的话 |
|------|------|---------|
| 大文件 | check-added-large-files (>500KB) | 自动 block |
| 密钥泄露 | detect-secrets | block + 报告位置 |
| 末尾空行 / 空格 | trailing-whitespace | 自动修复 |
| Python lint | Ruff | 自动 fix + block 严重 |
| Python 类型 | mypy（仅 staged 文件）| block |
| TS lint | ESLint | 自动 fix + block 严重 |
| TS 格式化 | Prettier | 自动 fix |
| 提交信息 | commitlint | block |

**安装**（每人本地一次）：
```bash
uv tool install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
```

### 3.2 L2 · CI 闸门（GitHub Actions）

**触发时机**：PR 创建 / PR 更新 / push 到 main

**检查项**（YAML 见 §8）：

| Job | 跑什么 | 通过门槛 |
|-----|--------|---------|
| `lint-api` | `ruff check && ruff format --check && mypy app/` | 0 错 0 警 |
| `lint-web` | `pnpm lint && pnpm typecheck` | 0 错 |
| `lint-wxapp` | `pnpm --filter wxapp lint` | 0 错 |
| `test-api` | `pytest --cov` | 通过率 100% / 覆盖率 ≥ 60% |
| `test-web` | `pnpm test --coverage` | 同上 |
| `build-web` | `pnpm build` | 产物 ≤ 2MB |
| `build-tauri` | `pnpm tauri build`（Win runner）| EXE ≤ 10MB |
| `build-wxapp` | `pnpm --filter wxapp build:weapp` | 主包 ≤ 1.5MB |
| `pr-size` | 自定义脚本 | < 800 行 warn / < 1500 block |
| `red-line-test` | 跑 200 对抗样本 | 召回 ≥ 99.5% / 误杀 ≤ 3% |
| `llm-regression` | 跑 30 标准场景 LLM-judge | ≥ 4/5 平均分 |

> `red-line-test` 和 `llm-regression` 在 nightly + PR 触发，因为成本较高（调 LLM）。PR 触发只跑 5 个抽样。

### 3.3 L3 · PR Review 闸门

**SLA**：开 PR 后 24 小时内必须有 Review。

**Review 必看 5 件事**：

1. **红线**：是否引用 §3.0.5？涉及内容审核 / 未成年模式 / K 调性？
2. **测试**：新代码有没有测试？覆盖率没掉？
3. **接口契约**：API 变更是否同步 OpenAPI？前端是否同步类型？
4. **可读性**：变量名 / 函数 / 注释是否能让 6 个月后的自己看懂？
5. **性能**：有没有 N+1 / 大 payload / 同步阻塞？

**用 GitHub PR 模板**（见 §8）。

### 3.4 L4 · Branch Protection（main）

在 GitHub 仓库 Settings → Branches → main 配置：

- ✅ Require a pull request before merging
- ✅ Require approvals: **1**
- ✅ Dismiss stale pull request approvals when new commits are pushed
- ✅ Require status checks to pass before merging
  - 必过：`lint-api` / `lint-web` / `test-api` / `test-web` / `pr-size`
- ✅ Require branches to be up to date before merging
- ✅ Require conversation resolution before merging
- ✅ Do not allow bypassing the above settings
- ❌ Force push（任何人都不允许）
- ❌ Allow deletions（main 不可删）

### 3.5 红线闸门（特殊）

任何修改以下文件 / 模块的 PR，**必须** 在 PR body 中勾选"红线检查"：

- `apps/api/app/agents/coach.py`（教练话术生成）
- `apps/api/app/services/moderation.py`（内容审核）
- `apps/api/app/services/wxapp/auth.py`（小程序登录）
- 涉及未成年用户字段（User.is_minor / age）
- 涉及录音 / TTS 资源

**红线 checkbox**：
```markdown
## 红线检查（涉及核心安全才必填）
- [ ] 内容审核已覆盖新增输入路径
- [ ] 未成年模式行为已验证
- [ ] 教练 K 调性未偏移（playful / 共情 / 不爹味）
- [ ] 30 人盲测覆盖（如修改话术）
- [ ] 涉及红线已 LGTM by 团队所有成员
```

---

## 4. 编码规范

### 4.1 通用

| 项 | 规则 |
|----|------|
| 文件大小 | ≤ 800 行（超出必须拆）|
| 函数长度 | ≤ 50 行 |
| 函数参数 | ≤ 5 个（超出用对象）|
| 嵌套深度 | ≤ 4 层 |
| 注释 | 写**为什么**不写**是什么**；TODO 必带名字和日期 |
| 时间戳 | UTC 存储 + Asia/Shanghai 显示 |
| 钱币 | 整数（分），不用浮点 |

### 4.2 TypeScript / React（Web + EXE 共享）

```ts
// ✅ 严格模式
"strict": true,
"noUncheckedIndexedAccess": true,

// ✅ 命名
const userId = "u_123";          // camelCase
const MAX_TURNS = 30;            // SCREAMING_SNAKE
function CoachAvatar() {}        // PascalCase 组件
type SessionMode = "sandbox";    // PascalCase 类型

// ✅ Import 顺序（ESLint 自动整理）
// 1. node_modules
import { useState } from "react";
// 2. 别名
import { Button } from "@/components";
// 3. 相对
import { useSandbox } from "./hooks";

// ❌ 不允许
any                              // 用 unknown 替代
@ts-ignore                       // 用 @ts-expect-error 加理由
console.log（生产）              // 上 logger
```

### 4.3 Python / FastAPI

```py
# ✅ 类型注解 100%（mypy 严格）
async def get_session(
    session_id: UUID,
    user_id: UUID,
) -> Session: ...

# ✅ 命名
session_id          # snake_case
MAX_RETRIES = 3     # SCREAMING_SNAKE
class CoachAgent:   # PascalCase

# ✅ Pydantic v2 用法
class SessionCreate(BaseModel):
    mode: Literal["sandbox", "copilot"]
    scenario_id: UUID

    model_config = ConfigDict(strict=True)

# ❌ 不允许
*args, **kwargs                  # 必须显式
print(...)                       # 用 logger
return HTTPException(...)        # 必须 raise
```

### 4.4 Taro 小程序

```tsx
// ✅ 遵守小程序规范
import Taro from "@tarojs/taro";

// 路由
Taro.navigateTo({ url: "/pages/sandbox/index" });
// 而不是 useNavigate()

// 网络
Taro.request({ url: API_BASE + "/sessions" });
// 而不是 fetch()

// 存储
Taro.setStorage({ key: "token", data });
// 不要 localStorage

// ✅ 主包瘦身
// 大资源（Mascot Lottie / 字体）放分包
// app.config.ts:
subPackages: [
  { root: "subpackages/mascot", pages: [...] },
  { root: "subpackages/wrapped", pages: [...] },
]
```

### 4.5 注释守则

```ts
// ❌ 废话注释
const i = 0;  // 初始化 i 为 0

// ✅ 解释 WHY
// 用 setTimeout 0 而非直接调用：等下一个 event loop，
// 让 React 完成重渲染后再触发滚动，避免抖动
setTimeout(() => scrollToBottom(), 0);

// ✅ 标记 TODO（必带名字 + 日期）
// TODO(A · 2026-05-15): 升级 LangGraph 到 0.3 后重写
```

### 4.6 错误处理

```py
# ✅ 显式
try:
    result = await llm.stream(...)
except LLMTimeoutError:
    # 主备切换
    result = await llm_backup.stream(...)
except LLMRateLimitError as e:
    raise HTTPException(429, detail=str(e))

# ❌ 吞错
try: ...
except: pass  # 永远不允许
```

---

## 5. 测试规范

### 5.1 测试金字塔

```
       ┌──────┐
       │  E2E │  5 个核心流程（Playwright）  · 慢但宝贵
       └──────┘
      ┌────────┐
      │ 集成   │  20-30 条 API 测试（pytest）
      └────────┘
   ┌────────────┐
   │  单元测试   │  60% 覆盖率（vitest + pytest）
   └────────────┘
```

### 5.2 单元测试要求

- 每个 P0 故事至少 3 条单测
- 关键工具函数 / Service 方法必测
- LLM 调用走 mock，不真打 API
- 文件名：`<file>.test.ts` / `test_<file>.py`

### 5.3 集成测试

```py
# pytest + httpx + 本地 docker compose
async def test_session_full_flow(client: AsyncClient):
    # 创建 session
    resp = await client.post("/sessions", json={...})
    session_id = resp.json()["id"]

    # 发送 turn（mock LLM）
    with mock_llm_response("..."):
        async for event in client.stream("POST", f"/sessions/{session_id}/turns", json={...}):
            ...

    # 验证 DB 状态
    assert await db.session_count(session_id) == 1
```

### 5.4 E2E（核心流程必跑）

- ✅ 注册 → 登录 → 进入沙盘 → 完成 5 轮 → 评分页
- ✅ 上传聊天截图 → 复盘报告
- ✅ 副驾启动 → 录音 → 显示提示
- ✅ Wrapped 卡生成 → 保存
- ✅ 小程序：扫码登录 → 沙盘 → 分享 Wrapped

### 5.5 LLM 回归测试

每周一 0 点跑：
- 30 个标准场景 × 当前 prompt → LLM-judge 评分 ≥ 4/5
- 200 对抗样本 → 红线召回 ≥ 99.5%

任一不达标自动开 issue。

### 5.6 覆盖率门槛

| 模块 | 单测覆盖率 |
|------|----------|
| `apps/api/app/agents/` | ≥ 70% |
| `apps/api/app/services/` | ≥ 70% |
| `apps/api/app/llm/` | ≥ 80% |
| `apps/web/src/features/` | ≥ 60% |
| `apps/web/src/components/` | ≥ 50% |
| `apps/wxapp/` | ≥ 40%（小程序测试基础设施较弱）|

---

## 6. 并行开发实操

### 6.1 接口契约先行（第一周必做）

**W1 D2 当天**：A 写完 OpenAPI Schema（YAML），两人 review 确认锁版本。

```yaml
# apps/api/openapi.yaml（部分示例）
paths:
  /sessions:
    post:
      summary: 创建对练 session
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [mode, scenario_id]
              properties:
                mode:
                  type: string
                  enum: [sandbox, copilot]
```

锁定后：
- A 按 Schema 实现 endpoint
- B 用 `openapi-typescript` 自动生成 TS 类型 + 用 MSW 跑 mock

### 6.2 Mock 优先（B 不被 A 阻塞）

`apps/web/src/mocks/handlers.ts`：

```ts
import { http, HttpResponse } from "msw";

export const handlers = [
  http.post("/api/sessions", () => {
    return HttpResponse.json({
      id: "ses_mock",
      opening_line: "小林啊，这次项目就靠你了。",
    });
  }),
  http.post("/api/sessions/:id/turns", function* () {
    yield new Response("event: opponent.delta\ndata: ...\n\n", {
      headers: { "content-type": "text/event-stream" },
    });
  }),
];
```

A 没写完时，B 用 mock 跑通 UI；A 上线后，删除对应 mock，切真接口。

### 6.3 冲突预防清单

**写代码前必看**：

| 你要做 | 必先做的事 |
|-------|----------|
| 改 API Schema | 在群里说一声 + 通知另一人改类型 |
| 加新表 / 改 schema | 写 alembic migration + Review |
| 改设计 token | 在 Figma 标注 + 群里 ping |
| 改 K Mascot 表情资源 | 群里 ping（B 主导）|
| 改 prompt 文件 | 跑 LLM 回归再合并 |
| 改 CI 配置 | A 主导，B Review |

### 6.4 代码评审 SLA + 礼仪

| 项 | 要求 |
|----|------|
| Review 时长 | ≤ 24h（紧急 ≤ 4h，PR 标 `urgent`）|
| Review 颗粒 | 一行一行看，不能"LGTM" 走过场 |
| 评论态度 | 用 `nit:` 标小问题；用 `?` 提疑问；用 `must:` 标必改 |
| Approve 标准 | 不只看代码，还看测试、看红线 checkbox |
| Reject 时机 | 红线问题 / 严重架构问题 / 缺测试 |

**好的 Review 评论例子**：

> ✅ `nit:` 这个变量名 `data` 太泛，建议 `sessionPayload`
> ✅ `must:` 这里没做空值检查，user 可能为 None（参考 §3.0.5 C）
> ✅ `?` 为什么用 setTimeout 而不是 useEffect？担心性能？

---

## 7. 4 周 Sprint Backlog（按天分到人）

> 每个任务都标注【天数 · 责任人 · 通过门槛】。卡了 24h 必须开会重新评估。

### 7.1 Sprint 0 · W1（基础设施 + 验证）

| Day | 时段 | A · 后端主 | B · 前端主 |
|-----|------|------------|------------|
| **D1 周一** | AM | 创建 GitHub repo + 邀请 + 初始结构 | 同 A 配对 |
| | PM | Docker Compose（pg+redis+qdrant+langfuse）一键起 | Vite + React + Tailwind 项目骨架 |
| | EOD | ✅ `docker compose up` 全绿 | ✅ Tauri 2 init + 第一个 EXE 打出（Win10 测）|
| **D2 周二** | AM | FastAPI hello + `/health` + Sentry | Taro 4 init + 第一屏 |
| | PM | **OpenAPI Schema v0.1**（重要！）+ alembic + User 表 | MSW + faker mock 数据 |
| | EOD | ✅ Schema 锁定，B 同意 | ✅ 三端打开同一 Hello 页 |
| **D3 周三** | AM | LLMProvider 抽象 + DeepSeek adapter | 设计 Token 翻译到 tailwind.config |
| | PM | LangGraph 3 节点图（RolePlay/Coach/Judge）+ Langfuse 接入 | Mascot K Rive 文件加载 + 1 个表情切换 |
| | EOD | ✅ 30 prompt × 2 model 评分 ≥ 4/5 | ✅ K 弹簧入场动画在 Web/EXE 都流畅 |
| **D4 周四** | AM | 阿里云 ASR SDK 集成 + 100 句测试 | NutUI 接入小程序 + 设计还原首页 |
| | PM | whisper.cpp WASM 真机 spike | Web 沙盘对练房静态页（用 mock 数据）|
| | EOD | ✅ 云端 ≥ 92% / 端侧 ≥ 85% / ≤ 800ms | ✅ Web 沙盘 UI 80% 还原图纸 |
| **D5 周五** | AM | 内容审核服务 + 200 对抗样本 v0 跑通 | 小程序服务器域名白名单 + API 联通 |
| | PM | Sprint Demo + Retro + 问题清单 | Sprint Demo + Retro + 问题清单 |
| | EOD | ✅ 召回 ≥ 99.5% / 误杀 ≤ 3% | ✅ 小程序真机扫码可访问 |

**W1 关键交付物**：
- ✅ 完整 docker compose 可运行栈
- ✅ OpenAPI Schema v0.1（锁版本）
- ✅ 5 个 Spike 全过（详见 [Foundation §6](./careercoach-foundation.md#6-sprint-0-验证-spike-清单)）
- ✅ 三端最小可运行版本（首页能打开，假数据驱动）

> 🚨 **如果某个 Spike 没过**：当天开会决定砍功能（详见 [Foundation §6.7](./careercoach-foundation.md#67-不通过的-spike-怎么办)）。**不允许带着不确定性进 W2**。

---

### 7.2 Sprint 1 · W2（沙盘 MVP 全功能）

| Day | A · 后端主 | B · 前端主 |
|-----|------------|------------|
| **D6 周一** | 沙盘 API：POST /sessions / POST /sessions/:id/turns（SSE）| 沙盘对练房交互层（输入框 / 流式光标 / 滚动）|
| **D7 周二** | RolePlay Agent prompt 调优 × 4 人格 | 三档话术卡 HintCardV2 + 教练 K 表情联动 |
| **D8 周三** | Coach Agent（三档生成）+ 并行机制 | 顶栏 + 退出确认 + 30 轮上限 UI |
| **D9 周四** | Judge Agent（评分）+ Score 持久化 | 评分页（雷达图 + 高光/失分 + Sticker）|
| **D10 周五** | 小程序专属：wx-login + 简化版沙盘 API | 小程序沙盘对练房（仅文字版）+ Sprint Demo |

**W2 验收**（详见 [PRD §3.1](./careercoach-prd-v2.md#31-epic-a沙盘对练练习模式)）：
- ✅ 评委可现场试沙盘对练 5 轮，端到端 ≤ 2s
- ✅ 评分页有 confetti（封神时刻）
- ✅ 小程序版沙盘可分享

---

### 7.3 Sprint 2 · W3（复盘 + 副驾 + Wrapped）

| Day | A · 后端主 | B · 前端主 |
|-----|------------|------------|
| **D11 周一** | 复盘 API：上传 + OCR + 说话人分离 + 异步任务 | 复盘三栏 UI（左上传 / 中分析 / 右报告）|
| **D12 周二** | Reviewer Agent（逐句分析 + RAG）| 三色标记 + 失分弹出更佳话术 |
| **D13 周三** | 副驾 WS + ASR 流 + Coach 实时 + 端侧降级 | 副驾 HUD UI（GlassCard + 字幕 + 三档切换）|
| **D14 周四** | Wrapped 卡 Canvas 服务端渲染 + 三种模板 | Wrapped 卡客户端触发 + 保存到相册 |
| **D15 周五** | 弱点画像 API + 推荐场景算法 | 弱点画像页 + Sprint Demo |

**W3 验收**：
- ✅ 复盘三色标记 + 改进话术能演示
- ✅ 副驾延迟现场 ≤ 1.5s
- ✅ Wrapped 卡可在小程序生成 + 转发到群

---

### 7.4 Sprint 3 · W4（联调 + 用户测试 + 答辩准备）

| Day | A · 后端主 | B · 前端主 |
|-----|------------|------------|
| **D16 周一** | 30 人盲测招募完成 + 数据收集脚本 | Mascot 全场景接入（8 种表情 × 关键时刻）|
| **D17 周二** | 全链路监控仪表盘 + 红线告警 | 演示 polish（动效细节 + 文案调）|
| **D18 周三** | **30 人盲测开跑**（全程协助）| **30 人盲测开跑**（全程协助）|
| **D19 周四** | bug 修复（按盲测反馈优先级）| bug 修复 + 答辩 PPT 起稿 |
| **D20 周五** | 部署到生产 ECS + 备份机预案 | 兜底视频录制 6 个 + Sprint Demo |

**W4 验收**：
- ✅ 30 人盲测：Q1 "更糟" ≤ 10% / Q3 "会用" ≥ 50%
- ✅ 答辩 PPT 完成
- ✅ 兜底视频全部录好
- ✅ 监控仪表盘绿灯

---

### 7.5 W5 · 答辩周（最后一周）

| Day | 共同任务 |
|-----|---------|
| **D21 周一** | 答辩走稿 ×3 次（按 [PRD §12](./careercoach-prd-v2.md#12-答辩-demo-逐秒级脚本) 逐秒）|
| **D22 周二** | 设备最终冻结 + 演示账号检查 + 备份机就绪 |
| **D23 周三** | 第二轮走稿 + 应对 Q&A 演练 |
| **D24 周四** | **24h 静默期**：不再改代码，只跑监控、看日志 |
| **D25 周五** | **答辩日**：A 主控后台 + B 主操作前台 |

---

## 8. Ready-to-paste 配置文件

### 8.1 `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-added-large-files
        args: ["--maxkb=500"]
      - id: check-merge-conflict
      - id: check-yaml
      - id: check-json

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]
      - id: ruff-format

  - repo: local
    hooks:
      - id: prettier
        name: prettier
        entry: pnpm exec prettier --write
        language: system
        files: \.(ts|tsx|js|jsx|json|md|yaml|yml)$

      - id: eslint
        name: eslint
        entry: pnpm exec eslint --fix
        language: system
        files: \.(ts|tsx|js|jsx)$

  - repo: https://github.com/alessandrojcm/commitlint-pre-commit-hook
    rev: v9.18.0
    hooks:
      - id: commitlint
        stages: [commit-msg]
        additional_dependencies: ['@commitlint/config-conventional']
```

### 8.2 `commitlint.config.js`

```js
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'type-enum': [2, 'always', [
      'feat', 'fix', 'refactor', 'docs', 'style',
      'test', 'chore', 'perf', 'ci', 'build', 'revert',
    ]],
    'scope-enum': [2, 'always', [
      'web', 'wxapp', 'api', 'agent', 'llm', 'asr', 'mod',
      'mascot', 'card', 'ci', 'docs', 'db', 'deps',
    ]],
    'subject-max-length': [2, 'always', 72],
    'body-leading-blank': [2, 'always'],
  },
};
```

### 8.3 `.github/workflows/ci.yml`

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint-api:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
        working-directory: apps/api
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy app/

  lint-web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: pnpm }
      - run: pnpm install --frozen-lockfile
      - run: pnpm --filter web lint
      - run: pnpm --filter web typecheck
      - run: pnpm --filter wxapp lint

  test-api:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_PASSWORD: postgres }
        ports: ["5432:5432"]
        options: --health-cmd pg_isready
      redis:
        image: redis:7
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
        working-directory: apps/api
      - run: uv run pytest --cov=app --cov-fail-under=60
        working-directory: apps/api

  test-web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: pnpm }
      - run: pnpm install --frozen-lockfile
      - run: pnpm --filter web test --coverage

  build-web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: pnpm }
      - run: pnpm install --frozen-lockfile
      - run: pnpm --filter web build
      - name: Check bundle size
        run: |
          SIZE=$(du -sk apps/web/dist | cut -f1)
          echo "Bundle size: ${SIZE}KB"
          [ $SIZE -lt 2048 ] || (echo "Bundle > 2MB" && exit 1)

  build-wxapp:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: pnpm }
      - run: pnpm install --frozen-lockfile
      - run: pnpm --filter wxapp build:weapp
      - name: Check main bundle size
        run: |
          MAIN_SIZE=$(du -sk apps/wxapp/dist/weapp/app.js | cut -f1)
          [ $MAIN_SIZE -lt 1500 ] || (echo "Main bundle > 1.5MB" && exit 1)

  pr-size:
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: Check PR size
        run: |
          ADDED=$(git diff --shortstat origin/main..HEAD | awk '{print $4+$6}')
          echo "Lines changed: $ADDED"
          [ $ADDED -lt 1500 ] || (echo "PR > 1500 lines, please split" && exit 1)
          [ $ADDED -lt 800 ] || echo "::warning::PR > 800 lines, consider splitting"
```

### 8.4 `.github/PULL_REQUEST_TEMPLATE.md`

```markdown
## What & Why
<!-- 描述这个 PR 干了什么、为什么 -->

## Owner
<!-- A 或 B 或 both -->

## 关联
- 关闭 #
- PRD §
- Design §

## Checklist 必填
- [ ] 我加了测试覆盖新代码
- [ ] CI 通过（见下方）
- [ ] 我自己 self-review 过了一遍
- [ ] 文档同步了（如改了 API / 数据模型）
- [ ] PR < 800 行（超出请拆）

## 红线检查（涉及 Coach / Moderation / 未成年 / 录音 / 学生场景才必填）
- [ ] 内容审核已覆盖新增输入路径
- [ ] 未成年模式行为已验证
- [ ] 教练 K 调性未偏移
- [ ] 30 人盲测覆盖（如修改话术）
- [ ] 涉及红线已 LGTM by 团队所有成员

## 演示
<!-- 截图 / 录屏链接 -->

## Review 提示
<!-- 想让 reviewer 重点看哪里 -->
```

### 8.5 `.github/ISSUE_TEMPLATE/bug.yml`

```yaml
name: Bug Report
description: 报告一个 bug
labels: [bug]
body:
  - type: textarea
    attributes: { label: "现象", placeholder: "看到了什么？" }
    validations: { required: true }
  - type: textarea
    attributes: { label: "复现步骤" }
  - type: textarea
    attributes: { label: "期望" }
  - type: dropdown
    attributes:
      label: "影响端"
      options: [Web, EXE, 小程序, API, All]
    validations: { required: true }
  - type: dropdown
    attributes:
      label: "严重性"
      options: ["P0 阻塞", "P1 高", "P2 中", "P3 低"]
    validations: { required: true }
  - type: checkboxes
    attributes:
      label: "红线？"
      options:
        - { label: 涉及内容审核 / 未成年 / K 调性 }
```

### 8.6 `CODEOWNERS`

```
# 全部 PR 默认两人都 Review
*  @yourname-A  @yourname-B

# 后端关键模块 A 必须 LGTM
/apps/api/app/agents/         @yourname-A
/apps/api/app/services/       @yourname-A
/apps/api/openapi.yaml        @yourname-A  @yourname-B

# 前端关键模块 B 必须 LGTM
/apps/web/                    @yourname-B
/apps/wxapp/                  @yourname-B
/packages/shared/             @yourname-A  @yourname-B

# 红线关键 · 两人都必须 LGTM
/apps/api/app/services/moderation.py     @yourname-A  @yourname-B
/apps/api/app/agents/coach.py            @yourname-A  @yourname-B
/tests/sensitive_samples.csv             @yourname-A  @yourname-B
```

### 8.7 `commit message hook（手册式提示）`

`.gitmessage` 文件（每次 commit 弹出）：

```
# <type>(<scope>): <subject>
#
# Body（可选）
#
# Footer（可选 · BREAKING CHANGE / Closes #）
#
# type: feat fix refactor docs style test chore perf ci build revert
# scope: web wxapp api agent llm asr mod mascot card ci docs db deps
# subject: 简短动词开头，≤ 72 字
```

启用：`git config commit.template .gitmessage`

---

## 9. 应急流程

### 9.1 Hotfix（生产挂了 / 答辩前发现致命 bug）

```
1. 从 main 拉 hotfix/<owner>-<topic>
2. 修代码（最小改动）
3. 加测试覆盖该 bug
4. 开 PR 标签 `urgent`
5. 另一人 ≤ 1h 内 Review
6. 合并 + tag 新 patch（如 v1.0.1）
7. 立刻部署
8. 事后写 ADR（决策日志）
```

### 9.2 Revert（合并后才发现问题）

```
git revert <commit-sha>
git push origin main
```

revert 也走 PR 流程（CI 必须通过）。

### 9.3 演示日故障应对

详见 [PRD §9.B.5 失败兜底脚本](./careercoach-prd-v2.md#9b5-失败兜底脚本) + [§12 逐秒脚本](./careercoach-prd-v2.md#12-答辩-demo-逐秒级脚本)。

**核心原则**：
- 主讲人 A 永远不停（话术兜底）
- 演示员 B 1 秒内切到备份
- 后台 C（如有）实时监控告警

### 9.4 LLM 行为漂移（突然给出违规话术）

```
1. 立刻把"专家模式"切到固定 prompt（不调 LLM）
2. 拉 Langfuse 看最近 100 条触发样本
3. 如果是模型本身漂移 → 切备用模型
4. 如果是 prompt 退化 → revert 上次 prompt 改动
5. 修复 + 加新对抗样本 + 跑回归
```

---

## 10. Day 0 启动清单

> **今晚就做**（可以一边吃外卖一边做，2 小时搞定）

### 10.1 Day 0 共同任务（建议晚上 7-9 点开个 2h 共享屏幕）

- [ ] 创建 GitHub 组织 + Repo（private）
- [ ] 互相加 collaborator
- [ ] 配置 Branch Protection（main · §3.4）
- [ ] 把 [Foundation §5 CLAUDE.md](./careercoach-foundation.md#5-ai-全局上下文) 内容写到 repo 根目录的 `CLAUDE.md`
- [ ] 把本文 §8 所有配置文件粘贴到 `.github/` 和根目录
- [ ] 创建初始目录结构（按 [Foundation §5 文件结构](./careercoach-foundation.md#文件结构建议)）
- [ ] 第一个 PR：`chore: project bootstrap`（互相 Review 一下，跑通流程）
- [ ] 在群里 pin **Sprint 0 backlog**（§7.1）
- [ ] 启动 ICP 域名备案（最长尾的 task）
- [ ] 申请微信小程序 AppID（个人认证）

### 10.2 第一周心理准备

| 想法 | 校正 |
|------|------|
| "差不多就能用了，不用 CI 那么麻烦"| 4 周不用 CI = 第 3 周开始改一行报错三处 |
| "测试以后再写"| 没写过测试的代码到 W3 一定会塌方 |
| "主备 LLM 临时再加"| Spike 0 不验证 = 答辩日翻车 |
| "评委不会问那么细"| 红线是底线，不是可选项 |
| "我先写完再 PR"| 单 PR > 1500 行没法 review，等于没 review |

### 10.3 一句话总结

> **A 写后端 B 写前端，OpenAPI 是合同，CI 是闸门，红线是底线，每天合并不熬夜。**

---

## 11. 关联文档

| 资源 | 路径 |
|------|------|
| 项目地基 | `careercoach-foundation.md` |
| PRD v2 | `careercoach-prd-v2.md` |
| 设计图纸 | `careercoach-design-spec.md` |
| 总体方案 v1 | `careercoach-vision.md` |
| 本文（工程规范）| `careercoach-engineering.md` |

---

## 12. 待补充（v1.1 准备）

- [ ] 上线后的监控告警 Runbook
- [ ] 用户反馈处理 SOP
- [ ] 性能优化 cookbook
- [ ] 数据迁移 + 备份恢复演练
