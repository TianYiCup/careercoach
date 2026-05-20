# CareerCoach AI · 阶段性全面审查报告

| 项          | 值                                                                                                     |
| ----------- | ------------------------------------------------------------------------------------------------------ |
| 日期        | 2026-05-20                                                                                             |
| 范围        | 全仓 · `apps/api`(A) + `apps/web` & `apps/wxapp`(B) + 流程/CI                                          |
| 基准 commit | `e63054d`（远程 `main`，PR #119）                                                                      |
| 审查角度    | **阶段进度对照**（Sprint 0→3 路线图）—— 区别于 `code-review-2026-05-20.md`（代码质量角度），二者互补   |
| 判定基准    | `CLAUDE.md`（14 约束 + 14 反模式）+ 五份文档：vision / prd-v2 / design-spec / foundation / engineering |

> **方法学说明**：本报告初稿曾基于落后 10 个 commit 的本地副本（#116），对 B 端做出「不可运行 / 瓶颈全在 B 端」的误判。修复 `git fetch` 后发现 #118–122 恰好是 B 端追赶工作（含 B-11 白屏修复），故本版基于真实 `main` @ `e63054d` 重做，并取代早前判断。所有验证数字均为重做时实测。

---

## 一、实测验证（本次审查亲跑）

| 检查       | 命令                           | 结果                                        |
| ---------- | ------------------------------ | ------------------------------------------- |
| A 端单测   | `uv run pytest -q`             | **803 passed / 30 skipped**（833 总）· 135s |
| A 端 lint  | `uv run ruff check .`          | ✅ All checks passed                        |
| A 端格式   | `uv run ruff format --check .` | ✅ 215 files formatted                      |
| Web lint   | `pnpm --filter web lint`       | ✅ eslint 干净                              |
| Web 类型   | `pnpm --filter web typecheck`  | ✅ tsc --noEmit 干净                        |
| Web 单测   | `pnpm --filter web test`       | ✅ 6 文件 / **51 测试通过**                 |
| wxapp lint | `pnpm --filter wxapp lint`     | ⚠️ **无 lint script**，命令 no-op           |

> 30 个 skip 均为 PostgreSQL / Redis / Aliyun 集成测试 —— 本地无 `docker compose` 故跳过；CI 有 service container，会全量跑 833。

---

## 二、结论速览

按工程规范 §7 的 4 周 Sprint 计划，今天 = W2 中段（首个 migration `20260511`）。但 122 个 PR / ~9 天的节奏表明团队已大幅压缩日历。

| 端                     | 实际进度                                                             | 判定                |
| ---------------------- | -------------------------------------------------------------------- | ------------------- |
| **A 端**（apps/api）   | Sprint 0→2 全功能 + Sprint 3 监控后端                                | 🟢 领先计划，质量高 |
| **B 端**（apps/web）   | auth/沙盘/副驾/复盘/弱点页齐全 + D16 mascot 全场景 + D17 demo polish | 🟢 基本跟上         |
| **B 端**（apps/wxapp） | 仅沙盘一条线，缺复盘/Wrapped/我的                                    | 🟠 半成品           |

**核心判断**：两端功能广度都跑在 Sprint 日历前面，A 端后端尤其扎实。项目风险**不是进度落后，而是 5 处完成度凹陷**。无 CRITICAL。

---

## 三、按 Sprint 阶段对照

### Sprint 0 · W1（基础设施 + 6 Spike）

| 交付物                                 | A 端                                                                    | B 端                          |
| -------------------------------------- | ----------------------------------------------------------------------- | ----------------------------- |
| `docker compose up` 全栈               | ✅ `docker-compose.yml`                                                 | —                             |
| OpenAPI Schema 锁定                    | ✅ `test_openapi_contract.py`                                           | ✅ 已 review                  |
| LLMProvider + DeepSeek/通义 + 主备路由 | ✅                                                                      | —                             |
| LangGraph 多 Agent 图                  | ✅ orchestrator/roleplay/coach/judge/reviewer/summarizer                | —                             |
| ASR 接入                               | ✅ `asr/aliyun.py`（端侧 whisper.cpp 未见）                             | —                             |
| 内容审核 + 200 对抗样本                | ✅ aliyun+local_dict+cascading；`tests/sensitive_samples.csv`（201 行） | —                             |
| 三端骨架 + Design Token + Mascot       | —                                                                       | ⚠️ 在位，Mascot 仍 emoji 占位 |

### Sprint 1 · W2（沙盘 MVP）

| 能力                                      | A 端            | web            | wxapp                         |
| ----------------------------------------- | --------------- | -------------- | ----------------------------- |
| `POST /sessions` + `/turns`(SSE) + `/end` | ✅              | ✅             | ✅                            |
| RolePlay / Coach / Judge Agent            | ✅              | —              | —                             |
| 评分页                                    | ✅ Score 持久化 | ✅ `ScorePage` | ✅（B-9 已补齐再来一次/分享） |

### Sprint 2 · W3（复盘 + 副驾 + Wrapped）—— A 端已提前完成

| 能力                                             | A 端           | B 端                                          |
| ------------------------------------------------ | -------------- | --------------------------------------------- |
| 复盘 `POST/GET /review/uploads` + Reviewer Agent | ✅             | web 有；**wxapp 无复盘页**                    |
| 副驾 WS `/copilot/sessions/{id}/stream` + ASR 流 | ✅             | web `CopilotPage`；wxapp 正确不做（约束 #12） |
| Wrapped 卡 `sharecards/{session,weekly,wrapped}` | ✅ Pillow 渲染 | **wxapp 无 Wrapped 页**                       |

### Sprint 3 · W4（监控 + 盲测 + 答辩）

A 端已提前交付红线监控后端：`ops/{token-cost, moderation-events, moderation-stats, token-cost-daily, llm-calls}`（A-42~A-46）。B 端已合入 D16 mascot 全场景 + D17 demo polish。30 人盲测 / 答辩 PPT / 兜底视频属非代码项，未启动（符合时间）。

---

## 四、真实风险清单（按严重度）

### 🔴 R1 · 场景库只有 7 个，PRD 要 ≥40 / 答辩要 ≥30

`apps/api/app/services/scenarios/seed_data.py` 实测 **7 个 `ScenarioRecord`**：周末加班谈判 / 实习转正薪资 / 室友深夜打游戏 / 导师让无偿干私活 / 面试自我介绍 / 父母催考公务员 / 房东恶意涨房租。

- PRD US-A1 L2 要求 **≥40 个、4 大类各有下限**（校园≥12 / 求职≥10 / 实习≥10 / 生活≥8）。
- PRD §10.2 答辩验收：「沙盘场景库 ≥ 30 个」「评委可挑任意场景试用且不出错」。
- 每个场景需 ≥5 真实学生认证（红线 §3.0.5 D，`real_user_certified` 字段在、值全 `False`）。
- **唯一会当场让答辩穿帮的缺口。** 属 A/B 共建项（engineering §1.3）。

### 🟠 R2 · wxapp 信息架构缺一半

5 个页面：`age-gate / health / index / login / sandbox`。**缺复盘、缺 Wrapped、缺「我的」**（PRD §4 底部 Tab = 首页/对练/副驾/复盘/我的）。foundation §3.2.2 明确「小程序是社交传播主入口，Wrapped 是其核心能力」—— 当前小程序恰恰没有 Wrapped。

附带：`app.config.ts` 无 `subPackages`（违反约束 #13，CI 主包≤1.5MB 闸门也关闭）；无 lint script；0 测试。

### 🟠 R3 · A 端缺多个 PRD 正面用户路径端点

实测缺失：`POST /scenarios/custom`（自定义场景，US-A1）、`GET /personas`（US-A2，人格内嵌于 seed）、`POST /sessions/{id}/voice`（沙盘语音，US-A3）、`GET /me/profile`、`GET /me/weaknesses`（弱点画像，US-C3）、`POST /vibe/today`、`GET /streak`、`GET /mascot/expression`、TTS / Edge-TTS（副驾耳机提示，US-B2）。

A 端把可观测性做到 Sprint 3，却跳过了上述用户路径端点。`weakness` 目前仅作为 `/sessions/{id}/end` 响应里的 `weakness_updates` 字段存在，无独立画像接口。

### 🟠 R4 · CI 红线闸门关闭

`.github/workflows/ci.yml:276` 起，`red-line-test`（200 对抗样本）、`llm-regression`、`build-web`、`build-wxapp`（主包≤1.5MB）全是注释 TODO。`tests/sensitive_samples.csv` 已备好 200 样本，但**没有 CI 任务跑它** —— 红线召回是 §3.0.5 P0 底线，回归塌陷会静默合并。

### 🟡 R5 · B 端测试覆盖不足

web 51 个测试全在 `api/v1/__tests__`（5）+ `mocks/__tests__/contract`（1）；`useSandboxSession` / `useCopilotSession` / 各页面 **0 组件/hook 测试**。wxapp **0 测试**、无测试框架。全项目无 E2E（无 Playwright）。违反 testing.md（80% + 单元/集成/E2E）与反模式 #10（P0 故事 PR 必带测试）。

### 🟡 R6 · 遗留代码债

- `apps/web/src/App.tsx` **733 行**，单文件混 8 个页面 + 手写路由（`refactor/B-split-apptsx` 分支未合）。
- `zustand` + `react-router-dom` 已装但**完全未用**（App.tsx 注释自承「react-router for now」，用 `useState<Page>` 手写路由）。
- Mascot 仍 emoji 占位（`MascotReaction.tsx:17` TODO；`@rive-app/react-canvas` 装了未接，`.riv` 资产未交付）。
- H-1（沙盘 self-harm `redirect` 资源漏出）、H-2（SMS dispatcher 缺生产 fail-closed）仍在 —— 详见 `code-review-2026-05-20.md`。

---

## 五、CLAUDE.md 14 硬约束逐条

| #   | 约束                             | 判定                                |
| --- | -------------------------------- | ----------------------------------- |
| 1   | 不教唆撕逼/不替代咨询/不法律建议 | ✅                                  |
| 2   | <18 青少年模式                   | ✅ age-gate + minor strictness      |
| 3   | 输入/输出全经审核                | ⚠️ H-1 沙盘 self-harm redirect 漏出 |
| 4   | LLM 主备切换                     | ✅ DeepSeek→Qwen，800ms 预算        |
| 5   | LLM 链路接 Langfuse              | ✅ 全链路 trace                     |
| 6   | UTC 存储 + Asia/Shanghai 显示    | ✅                                  |
| 7   | 不用 Redux/Electron/k8s          | ⚠️ zustand/react-router 装了未用    |
| 8   | PR ≤ 800 行                      | ⚠️ CI 实为 800 warn / 1500 block    |
| 9   | 不硬编码 key / 不提交 .env       | ✅                                  |
| 10  | K 调性「嘴硬心软不爹味」         | ✅                                  |
| 11  | 三端策略（web/wxapp 独立）       | ✅                                  |
| 12  | 副驾仅 Web/EXE                   | ✅ wxapp 无副驾页                   |
| 13  | 小程序主包≤1.5MB + 分包          | ❌ 无 subPackages，CI 体积闸门关    |
| 14  | 小程序域名白名单                 | ❌ 占位域名待 ICP 备案              |

反模式命中：#5（Mascot emoji）、#7（App.tsx 733 行）、#10（B 端无组件测试）。

---

## 六、对照五份文档的其他缺口

- CLAUDE.md / foundation §5 文件结构列 `packages/api-client`（OpenAPI 生成客户端），实际只有 `packages/shared`。
- engineering §8.2 的 `commitlint.config.js` scope 列表是短版，实际已扩展（含 tts/config/sandbox/copilot/auth/wrapped 等）—— NFR M-04「文档同步」未落实。
- 端侧 whisper.cpp ASR（foundation §3.4.3，副驾隐私模式 US-B3 依赖）未见实现。
- RAG / Qdrant（复盘 Reviewer Agent 依赖，架构图路径 C）接入度低。

---

## 七、建议处理顺序

| 优先级 | 事项                                                                                             | 责任端   |
| ------ | ------------------------------------------------------------------------------------------------ | -------- |
| **P0** | 场景库 7 → ≥30（含 `GET /personas` 端点 + 真实学生认证）—— 答辩硬指标                            | A+B 共建 |
| **P0** | 启用 CI `red-line-test` + `build-wxapp` 体积闸门                                                 | A        |
| **P1** | wxapp 补复盘 + Wrapped + 我的页（小程序是 Wrapped 核心端）                                       | B        |
| **P1** | A 端补正面路径端点：`/scenarios/custom`、`/me/weaknesses`、`/vibe`、`/streak`                    | A        |
| **P1** | H-1 沙盘 self-harm redirect 漏出 + H-2 SMS fail-closed 守卫                                      | A        |
| **P2** | 合并 `refactor/B-split-apptsx`；落地或删除 zustand/react-router 依赖                             | B        |
| **P2** | B 端补组件/hook 测试（先 `useSandboxSession`/`useCopilotSession` SSE 解析）+ 引入 Playwright E2E | B        |

---

## 八、亮点（值得保留的良好实践）

- A 端 803 测试全绿、ruff 全绿；JWT secret fail-closed 校验；Ops 接口未配 token 时 503 fail-closed + `hmac.compare_digest` 常量时间比较。
- A 端可观测性已做到答辩级（红线监控 ops 接口齐全）。
- LLM 主备路由 + Langfuse 全链路 trace + Repository 模式（memory/postgres config 切换）。
- B 端 B-11 白屏已修（`mockServiceWorker.js` 提交 + bootstrap try/catch 守卫），契约错误码 B-1/B-2/B-6/B-9 已陆续补齐。

---

_审查人：Claude（AI 助手）· 基于 commit `e63054d` 实测验证。R1 场景库为答辩最高优先级缺口。_
