# Sprint 0 · W1 任务清单（A / B 分工版）

> **A · patient-Zero-0**（后端主）+ **B · k4392**（前端主）
> 周期：W1 D1（周一）→ D5（周五）· 共 5 天
> 目的：把 6 个 Spike 跑通，所有"假设"验证掉。**没过的 Spike 砍对应功能，不进 W2**。
>
> 引用：[engineering §7.1](./careercoach-engineering.md#71-sprint-0--w1基础设施--验证) · [foundation §6](./careercoach-foundation.md#6-sprint-0-验证-spike-清单)

---

## 0. 共同准则

| 准则 | 说明 |
|------|------|
| 每天 09:30 异步站会 | 群里发 3 句：昨天 / 今天 / 卡点 |
| 周三 14:00 中段对齐 | 30 min 看进度 + 协调接口 |
| 周五 16:00 Demo + Retro | 60 min 互演 + 改进点 |
| Spike 卡 24h | 立刻开会重新评估 / 砍功能 |
| 所有 PR ≤ 800 行 | CI 会标黄 / 1500 直接 block |
| 红线模块（moderation/coach/judge）| 双 LGTM，永不绕过 |

---

## 1. D1（周一）· 项目初始化 + 起骨架

### A · patient-Zero-0（后端）

| 时段 | 任务 | EOD 通过门槛 |
|------|------|-------------|
| AM | 验证 `docker compose up -d` 起 postgres + redis + qdrant + langfuse + langfuse-db 5 个服务 | `docker compose ps` 全 healthy |
| AM | `uv init apps/api` + 写 pyproject.toml（FastAPI 0.115 / Uvicorn / Pydantic v2 / SQLAlchemy / alembic / httpx / ruff / mypy） | `uv sync` 成功 |
| PM | 写 `app/main.py` + `/health` 端点（返回 `{"status":"ok"}`）+ ruff/mypy 配好 | `curl http://localhost:8000/health` = 200 |
| PM | 配 Sentry SDK（dsn 走 .env，dev 时可空）| 故意 `raise Exception` 在 Sentry dev project 看到 |
| EOD | ✅ docker 全栈 + FastAPI hello 双双跑通 | — |

### B · k4392（前端）

| 时段 | 任务 | EOD 通过门槛 |
|------|------|-------------|
| AM | `pnpm create vite@latest apps/web --template react-ts`（React 19 + TS strict）| `pnpm --filter web dev` 启动，浏览器看到 vite 页 |
| AM | Tailwind CSS 4 init + Radix UI 装好 + `tsconfig.json` strict 全开（`noUncheckedIndexedAccess` 也开）| `tailwind.config.ts` + 第一屏带 Tailwind 类的 hello 页 |
| PM | `pnpm tauri init`（Tauri 2.0）+ 第一次 `pnpm tauri build` 出 EXE | EXE 在 Win10 双击启动 ≤ 3s，包大小 < 10MB |
| PM | 配 ESLint + Prettier（已有项目根 prettier 配，apps/web 装 eslint-config-next 或 eslint:recommended + plugin:react/recommended + tseslint）| `pnpm --filter web lint` + `typecheck` 全过 |
| EOD | ✅ Web 在 Chrome/Edge 可访问 + EXE Win10 双击启动 ≤ 3s | — |

> **配对建议**：D1 上午两人共享屏幕一起把 `docker compose` 跑起来 + 第一次 PR `chore: bootstrap api & web` 跑通流程。这是唯一可能配对的一天。

---

## 2. D2（周二）· OpenAPI 锁定 + Mock 联通

### A · patient-Zero-0

| 时段 | 任务 | EOD 通过门槛 |
|------|------|-------------|
| AM | FastAPI hello + `/health` + Sentry 接入完成（D1 末 unfinished 收尾）| 上面三项 PR 合入 main |
| AM | **OpenAPI Schema v0.1**（`apps/api/openapi.yaml`）—— **D2 当天必须锁定** | 见 §2.1 接口清单 |
| PM | alembic init + env.py 异步引擎 + 第一个 migration（User 表，对应 PRD §6.1）| `alembic upgrade head` 成功 ，psql 看到 users 表 |
| PM | Schema 给 B review，约定 11:30 同步会确认 | B 同意签字 + commit `docs(api): lock openapi v0.1` |
| EOD | ✅ Schema 锁定 + B 签字 + alembic 跑通 | — |

### B · k4392

| 时段 | 任务 | EOD 通过门槛 |
|------|------|-------------|
| AM | `pnpm create taro init apps/wxapp`（Taro 4 + React + NutUI 4）+ 第一屏 Hello | 微信开发者工具打开能看到 |
| AM | 申请微信小程序 AppID（个人认证，~1 天）| 收到 AppID 即可填到 `apps/wxapp/project.config.json` |
| PM | MSW 装到 apps/web + `src/mocks/handlers.ts` 第一批 mock（POST /sessions / POST /sessions/:id/turns SSE）| Web 端 fetch 这两个 mock 接口能拿到假数据 |
| PM | 三端首页都打开同一份 Hello（Web / EXE / 小程序）| 三端截图 |
| PM | review A 的 OpenAPI Schema（11:30 会议）| 用 `openapi-typescript` 生成 TS 类型，跑过 |
| EOD | ✅ 三端打开同 Hello + Schema review 完成 | — |

### 2.1 OpenAPI Schema v0.1 至少要有的端点

> 这是合同。锁定后 A 按它写实现，B 按它写 mock 和类型。

| 端点 | 描述 | 优先级 |
|------|------|--------|
| `GET /health` | 健康检查 | D1 已有 |
| `POST /auth/sms/send` | 发短信验证码（演示阶段可 mock）| D2 |
| `POST /auth/sms/verify` | 验码 + 颁 JWT | D2 |
| `GET /scenarios?category=campus&q=` | 场景库列表 | D2 |
| `POST /sessions` | 创建沙盘 session | D2 |
| `POST /sessions/{id}/turns` | 单轮对话（**SSE**）| D2 |
| `POST /sessions/{id}/end` | 结束对练 + 出评分 | D2 |
| `POST /moderation/check` | 内容审核（同步）| D2 |

完整契约见 PRD §7。**v0.1 不需要全做，但 schema 必须有**——这样 B 才能 mock。

---

## 3. D3（周三）· LLM 主备 + Design Token + Mascot

### A · patient-Zero-0

| 时段 | 任务 | EOD 通过门槛 |
|------|------|-------------|
| AM | `app/llm/provider.py` 写 `LLMProvider` Protocol（见 foundation §3.3.4）| 接口签名 + Pydantic Message 模型 |
| AM | `app/llm/deepseek.py` adapter（OpenAI 兼容协议）+ `app/llm/qwen.py` adapter | 两个 adapter 各自的 stream_chat 单测通过 |
| PM | `app/llm/router.py` 主备路由（DeepSeek 失败 ≤ 800ms 切通义）| 故意 401 DeepSeek，通义在 800ms 内接管 |
| PM | LangGraph 3 节点图：`agents/orchestrator.py` + `roleplay.py` + `coach.py` + `judge.py`（最小 stub）| 跑一遍 RolePlay → Coach → Judge 状态机 |
| PM | Langfuse SDK 接入 + 第一条 trace 在 langfuse dashboard 可见 | `localhost:3001` dashboard 看到 |
| PM | 30 个 prompt × 2 model 的人工评分（找 5 个场景就够）| LLM-judge 平均 ≥ 4/5 |
| EOD | ✅ 主备切换 ≤ 800ms + LangGraph 3 节点跑通 + 首条 trace | — |

### B · k4392

| 时段 | 任务 | EOD 通过门槛 |
|------|------|-------------|
| AM | 设计 Token JSON（design-spec §12.2）翻译到 `apps/web/tailwind.config.ts` | 8 个 vivid 色 + 4 个渐变 + radius/motion token 全在 |
| AM | 装 framer-motion + `@rive-app/react-canvas` | `pnpm --filter web build` 通过 |
| PM | Mascot K Rive 文件加载（先用占位 .riv，资源待设计）+ 1 个表情切换 demo | 8 表情中至少 1 个能切（confident → thinking）|
| PM | 弹簧入场动画（scale 0.3 → 1 spring(500,18) + rotate -10° → 0）| Web/EXE 60 fps 实测 |
| PM | StickerBadge / GlassCard / BlobBackground 三个底层组件骨架（见 design-spec §6） | Storybook 或 demo page 能看到 |
| EOD | ✅ K 弹簧入场在 Web/EXE 都流畅 + Token 全部翻译完 | — |

> ⚠️ **Mascot Rive 资源**：D1-D2 联系设计同学/AI 绘图出 .riv 文件（紫色 mochi 拳手 + 8 表情）。如 D3 拿不到，先用 emoji + Lottie 占位（design-spec §3.3 表情列表）。

---

## 4. D4（周四）· ASR Spike + 沙盘对练房 UI

### A · patient-Zero-0

| 时段 | 任务 | EOD 通过门槛 |
|------|------|-------------|
| AM | 阿里云实时 ASR SDK 集成 + 100 句标准测试集准确率 ≥ 92% | 跑测试输出准确率报告 |
| AM | WebSocket 服务（`/copilot/{id}` ws endpoint）骨架 | wscat 可连 |
| PM | whisper.cpp WASM 在 Web 端跑通（先用 whisper-tiny 模型，75MB）| Chrome 能加载 + 真机测试中端手机延迟 ≤ 800ms |
| PM | 端到端：浏览器录音 → 优先端侧 → 失败降级云端 | 演示视频 |
| EOD | ✅ 云端 ≥ 92% / 端侧 ≥ 85% / 端侧延迟 ≤ 800ms | — |

> 不通过预案：副驾 v1 仅支持 WiFi 环境（去掉端侧降级）。**当天开会决定**。

### B · k4392

| 时段 | 任务 | EOD 通过门槛 |
|------|------|-------------|
| AM | NutUI 4 接入小程序 + 设计还原首页（design-spec §9.2）| 微信开发者工具截图对照设计稿 80% |
| PM | Web 沙盘对练房静态页（design-spec §9.3）—— 用 MSW mock 数据驱动 | 顶栏 K 表情 + 对手气泡（渐变）+ 用户气泡（vivid 渐变）+ HintCardV2 三档话术 + 输入框 |
| PM | HintCardV2 / CoachBubble / VibePill / StreakFire 4 个业务组件 | Storybook |
| EOD | ✅ Web 沙盘房 80% 还原图纸 + 4 个核心业务组件 | — |

---

## 5. D5（周五）· 内容审核 + 小程序联通 + Demo

### A · patient-Zero-0

| 时段 | 任务 | EOD 通过门槛 |
|------|------|-------------|
| AM | 阿里云内容安全 API 联通 + `app/services/moderation.py` | 单测：自残样本 → block 响应 |
| AM | 自建敏感词词典 v0（200 词，覆盖 PRD §3.0.5 五大类）| `tests/sensitive_dict.txt` 提交 |
| PM | 200 对抗样本 v0 CSV（`tests/sensitive_samples.csv`，按 PRD §13.A 类目结构）| 文件提交 |
| PM | 跑全量 200 样本：红线召回 ≥ 99.5% + 误杀 ≤ 3% | 回归报告输出 |
| PM | Sprint Demo + Retro（16:00）| 与 B 同步 |
| EOD | ✅ 召回 ≥ 99.5% / 误杀 ≤ 3% / 整周 Spike 报告完成 | — |

### B · k4392

| 时段 | 任务 | EOD 通过门槛 |
|------|------|-------------|
| AM | 小程序服务器域名白名单：dev/staging/prod 后端域名添加到微信小程序后台 | 后台截图 |
| AM | 小程序内调通第一个 API（`GET /health`）—— 用 dev 域名 | 真机扫码看到 health 返回 |
| PM | Wrapped 卡分享 spike：小程序生成 1 张卡 + 转发到自己微信 | 朋友打开能看到内容 |
| PM | Sprint Demo + Retro（16:00）| 与 A 同步 |
| EOD | ✅ 小程序真机扫码可访问 + Wrapped 卡能分享 | — |

> **域名 ICP 备案**：W -4 应该已启动，D5 必须确认进度。如未启动，立即报警 + 切"网页二维码"分享方案兜底。

---

## 6. W1 关键交付物（周五 16:00 Demo 必须展示）

### 共同交付
- [ ] `docker compose up` 全栈一键运行
- [ ] **OpenAPI Schema v0.1 锁定**（D2）
- [ ] 三端最小可运行版本（首页能打开 + 假数据驱动）
- [ ] 6 个 Spike 全过（A：LLM / ASR / 内容审核；B：Mascot / 三端 / Wrapped 分享）
- [ ] Spike 报告（每项验证结果 + 数据，foundation §6.7 模板）

### A 独立交付
- [ ] FastAPI + alembic 第一张 User 表
- [ ] LLMProvider 抽象 + DeepSeek + 通义双 adapter
- [ ] 主备切换 ≤ 800ms 演练通过
- [ ] LangGraph 3 节点图（RolePlay/Coach/Judge）+ Langfuse trace
- [ ] ASR 双链路（云端 + whisper.cpp）
- [ ] 内容审核服务 + 200 对抗样本 v0

### B 独立交付
- [ ] apps/web（Vite + React 19 + Tailwind 4 + Radix）+ Tauri 2 EXE
- [ ] apps/wxapp（Taro 4 + React + NutUI 4）+ 微信小程序后台
- [ ] Design Token 翻译完成
- [ ] Mascot K Rive 加载 + 1 表情切换 + 弹簧入场
- [ ] 沙盘对练房 Web 静态页 80% 还原
- [ ] 4 个核心业务组件（HintCardV2 / CoachBubble / VibePill / StreakFire）
- [ ] Wrapped 卡分享 spike 通

---

## 7. Spike 不通过预案速查（foundation §6.7）

| Spike 失败 | 影响 | 当天决定 |
|-----------|------|---------|
| LLM 主备切换不达标 | 答辩翻车 | 删除"主备切换"宣传点 |
| ASR 准确率 < 92% | 副驾不能用 | **副驾砍掉**，仅做沙盘 |
| 端侧 ASR 不可行 | 副驾依赖网络 | PRD §1.5 加一行"v1 仅 WiFi" |
| 红线召回 < 99.5% | 红线塌陷 | 加严规则 + 人工抽审 |
| Rive 渲染卡顿 | Mascot 出场不爽 | 降级 Lottie / 静态 PNG |
| 域名备案延误 | 小程序拿不到二维码 | 切"网页+二维码"分享方案 |

> **核心准则**：**Sprint 0 没过的功能不进 Sprint 1**。砍功能比答辩翻车强 10 倍。

---

## 8. 协作时点（必须对齐）

| 时点 | 内容 | 双方动作 |
|------|------|---------|
| **D2 11:30** | OpenAPI Schema v0.1 review 会 | A 起草，B review，11:30 会议确认锁定 |
| **D3 整天** | Design Token 同步 | B 翻译时遇到 token 缺失，群里 ping 设计同学 |
| **D4 EOD** | Mock 切真接口 | A 把 D2 锁定的 endpoint 实现完，B 在 MSW 里删除对应 handler |
| **D5 16:00** | Sprint Demo + Retro | 两人互演 60 min |

---

## 9. 我（A）需要 B 配合的事（提前 ping）

- D1 早：一起共享屏幕跑 docker compose（确认环境一致）
- D2 11:30：OpenAPI Schema review 会议
- D3 期间：如果 Mascot Rive 资源没到位，先用占位
- D4 EOD：Mock 切真接口的对接清单
- D5：盲测样本里如有"学生原话"需要 B 帮忙找校友收集

---

## 10. B 需要 A 配合的事

- D1 末：Mock 用 SSE 格式时按 OpenAPI v0.1 草案 stub
- D2 11:30：Schema 评审，B 拿出"我哪里需要这个字段"
- D3：Design Token 翻译时如有歧义，群里同步
- D4：whisper.cpp WASM 集成到 Web 端时 A 要提供 worker 接口
- D5：小程序 health endpoint 调通要 A 确认 CORS / 域名白名单

---

> 一句话总结：**A 写后端 B 写前端，OpenAPI 是合同，CI 是闸门，红线是底线，每天合并不熬夜。**
