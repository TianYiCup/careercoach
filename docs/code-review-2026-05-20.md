# CareerCoach AI 代码审查报告 · A / B 双端

| 项          | 值                                                                                                                              |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------- |
| 日期        | 2026-05-20                                                                                                                      |
| 范围        | 全仓 · `apps/api`(A) + `apps/web` & `apps/wxapp`(B)                                                                             |
| 基准 commit | `06f3a81`（远程 `main`，含 PR #118）                                                                                            |
| 判定基准    | `CLAUDE.md` 14 条硬约束 + 14 条反模式 + 编码规范；`~/.claude/rules` 全局规范（coding-style / testing / security / code-review） |
| 验证        | A 端 `pytest` 833 收集 / 803 通过（不含 PG 集成）、`ruff check`+`ruff format` 全绿；web `eslint`+`tsc`+`vitest` 51 通过         |

> 本报告供 A / B 两端共同审阅。**「责任端」** 一列标明该项应由谁跟进。

---

## 一、结论速览

| 端                                    | 总体判定                                                                                                                                                    |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A 端**（`apps/api`）                | ✅ **基本达标**。质量很高、测试充分、安全规范到位。遗留 1 条红线缺口（H-1）需产品+工程共同决策。                                                            |
| **B 端**（`apps/web` + `apps/wxapp`） | ⚠️ **部分达标，有明确缺口**。lint/类型/调性/平台隔离合格；**测试、Zustand、文件结构、Mascot Rive、小程序分包/域名** 多项未达 .md 规范，其中测试缺口最严重。 |

无 CRITICAL。**HIGH 3 条**、MEDIUM 7 条、若干 LOW。

---

## 二、HIGH（合并前必须处理）

### H-1. 沙盘对练里「自残（self-harm）」被放行，危机热线不显示 — 责任端：A（+产品）

- 位置：`apps/api/app/services/sessions/turn_service.py:239`
- `validate_turn_request` 对用户输入只拦截 `verdict == "block"`。`self_harm` 映射到 `verdict=redirect`（见 `local_dict.py:87`、`aliyun.py:274`），会以 `input_verdict="redirect"` **直接继续**，`decision.redirect_resource`（危机热线 `SELF_HARM_RESOURCE`）被丢弃。
- 沙盘 SSE 流（`opponent.delta/done` / `coach.hint` / `meta`）**没有 `moderation` 帧**，所以前端无法展示热线。`apps/web` 与 `apps/wxapp` 的 SSE switch 同样无 `moderation` 分支——缺口贯穿后端 + 两个前端。
- 对比：`/v1/moderation/check` 与 copilot WS 都正确 surface 了 redirect 资源；唯独沙盘 turn 路径不对称（AI 输出侧 `_output_passes_moderation:510` 同时处理 `block`+`redirect`，输入侧只处理 `block`）。
- 影响：违反 `CLAUDE.md` §3.0.5 A（自残→危机热线）与优先级「红线 > NFR > 功能」。沙盘是使用频率最高的核心模式。
- 建议：SSE 增加 `moderation` 帧；输入命中 `redirect` 时下发 `redirect_resource`；两端前端消费并展示热线卡。是否继续 roleplay 由产品决策。

### H-2. SMS dispatcher 缺生产环境 fail-closed 防护 — 责任端：A

- 位置：`apps/api/app/services/auth/__init__.py:86`
- `get_auth_service()` 不看 `app_env`，无条件接 `LoggingDispatcher()`；该类 docstring 自述「NEVER swap this in for prod — 验证码明文进日志是审计失败」。
- `jwt_secret` 有非 dev 拒绝 dev 默认值的 `model_validator`，dispatcher 却无同级守卫。生产部署会：① 短信验证码明文落 INFO 日志（违反 §6.2）；② 不发真实短信，用户无法登录。
- 建议：增加 fail-closed 守卫——`app_env != "development"` 且 dispatcher 为 `LoggingDispatcher` 时启动直接 raise。

### H-3. B 端测试覆盖严重不足 — 责任端：B

- 实测 `pnpm test`：web **51 个测试，全部集中在 `api/v1/__tests__/` 与 MSW contract**。
- **0 个组件/hook 测试**：`SandboxRoom`、`CopilotPage`、`ReviewResultPage`、`useSandboxSession`(302 行)、`useCopilotSession`(444 行) 全未测——而这些 hook 正是 SSE 流式解析核心逻辑。
- **`apps/wxapp` 0 测试**：无 `test` script、无测试框架、无一个 `.test.ts`；沙盘（P0 模式）含 SSE 处理却零测试。
- **全项目无 E2E**：无 Playwright 配置。
- 影响：违反 `testing.md`（80% 覆盖 + 单元/集成/E2E）与反模式 #10「任何 P0 故事的 PR 必须带测试」。三大核心模式（沙盘/副驾/复盘）均 P0。
- 建议：优先给 `useSandboxSession`/`useCopilotSession` 的 SSE 解析补单元测试；给 wxapp 搭测试基建；引入 Playwright 覆盖三大模式 E2E。

---

## 三、MEDIUM

| #   | 责任端 | 位置                                   | 内容                                                                                                                                                                                                                               |
| --- | ------ | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M-1 | A      | `services/moderation/cascading.py:79`  | 主备双双失败时异常上抛；`TurnService` 把 `ModerationBackendError` 当 fail-open（`backend_failed` 继续），审核宕机期间存在「红线内容通过」的已知窗口。注释自承「v1 要加 hard `block` fallback」。应作为产品/合规决策明确化。        |
| M-2 | A      | `routes/v1/copilot.py` WS              | WebSocket 不重验 `require_adult`，URL 路径里的 `copilot_id`（64 位熵、一次性）即 capability ticket；URL 会泄漏到代理日志/历史/Referer。代码注明「transitional」。副驾是成人专属/未成年敏感面（R-15），上线前应改 JWT subprotocol。 |
| M-3 | A      | `services/moderation/local_dict.py:81` | `term in content` 子串匹配，插空格/同形字/全角即可绕过。仅备用后端、v0 可接受，但需 200-sample regression 验证 cascade 真以 Aliyun 为主。                                                                                          |
| M-4 | B      | `apps/web/package.json:30`             | `zustand` 已装但**全 web 从未使用**，无 `src/stores/`；编码规范规定前端用 Zustand、结构图列 `stores/`。要么落地 store，要么移除依赖并更新 .md。                                                                                    |
| M-5 | B      | `apps/web/src/App.tsx`                 | 文件 722 行，`SandboxRoom`/`WrappedPage`/`HomePage` 多个整页挤在一处；违反结构图「`features/` 分 Epic」与 coding-style「many small files」。应拆到各自 `features/*/`。                                                             |
| M-6 | B      | `apps/web/src/App.tsx:650`             | 手写 `useState<Page>` 路由共 8 个页面，但 `react-router-dom@7` 已装未用，且无深链接/浏览器后退。要么接入路由器，要么移除依赖。                                                                                                     |
| M-7 | B      | `apps/wxapp/src/app.config.ts`         | 无 `subPackages` 配置、无 `src/subpackages/`，CI `build-wxapp (主包≤1.5MB)` 闸门仍注释关闭。约束 #13 的分包结构与体积验证均缺位。                                                                                                  |

---

## 四、LOW / 备注

| #   | 责任端 | 内容                                                                                                                                                                                                                                                                                            |
| --- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| L-1 | B      | **Mascot Rive（反模式 #5）**：`MascotReaction.tsx:17` 注明「`.riv` 到位后替换 emoji」——属设计资产未交付的阻塞态；`@rive-app/react-canvas` 已装未接。`WrappedCard` 因 Canvas 渲染用 emoji 字符可理解。严格按 .md 仍未达标，需排期明确。a11y 做得好（按钮有 `aria-label`、emoji `aria-hidden`）。 |
| L-2 | B      | **wxapp 域名白名单（约束 #14）**：`apps/wxapp/src/api/config.ts` 占位域名待 ICP 备案——已知阻塞，wxapp 暂不可真机出货。                                                                                                                                                                          |
| L-3 | B      | `apps/wxapp` 沙盘 `🎤` 发送按钮：沙盘是文字输入却用麦克风图标，无文字/label（web 版有 `aria-label`）。轻微误导 + a11y 小瑕疵。                                                                                                                                                                  |
| L-4 | B      | 反模式 #7：`SandboxRoom`(~312 行)、`useCopilotSession`(444 行) 等组件/函数偏大，建议拆分。                                                                                                                                                                                                      |
| L-5 | A      | `InProcessWorkerQueue`：进程重启会让 in-flight 的 `processing` review 行永久卡「分析中」。已注明，计划由 A-14 Redis 队列解决；单 worker v0 可接受。                                                                                                                                             |
| L-6 | A      | `dependency.py:38` `ANONYMOUS_USER_ID`：legacy sentinel，已非运行时返回值，legacy 行失效后可清理。                                                                                                                                                                                              |
| L-7 | A      | `turn_service.py` 的 prompt 常量与 agents 包重复（docstring 注明有意为之，轻微 DRY 负债）。                                                                                                                                                                                                     |
| L-8 | 共     | CI：`build-wxapp` / `red-line-test (200 对抗样本)` / `llm-regression` 在 `.github/workflows/ci.yml:277` 仍注释关闭——红线与主包体积均 P0，回归可能静默合并。                                                                                                                                     |
| L-9 | 共     | PR 体积：`CLAUDE.md` 写「不写超过 800 行的 PR」，CI 却 800 行 warn、1500 行才 fail。规则与执行不一致。                                                                                                                                                                                          |

---

## 五、.md 规范逐条对照

| 规范来源                 | 条目                                           | A 端                                        | B 端                                                           |
| ------------------------ | ---------------------------------------------- | ------------------------------------------- | -------------------------------------------------------------- |
| testing.md               | 80% 覆盖 + 单元/集成/E2E                       | ✅ 833 测试                                 | ❌ web 仅 51（纯 API 层）；wxapp 0；无 E2E                     |
| 反模式 #10               | P0 故事 PR 必带测试                            | ✅                                          | ❌ 沙盘/副驾/复盘 P0 UI 与 hook 无测试                         |
| 编码规范                 | 前端用 Zustand                                 | —                                           | ❌ 装了未用，无 `stores/`                                      |
| 文件结构                 | `features/` 分 Epic、`stores/`、`subpackages/` | —                                           | ⚠️ `App.tsx` 722 行混页；无 `stores/`；wxapp 无 `subpackages/` |
| 反模式 #5                | Mascot 必须用 Rive                             | —                                           | ⚠️ emoji 占位（`.riv` 未到位）                                 |
| 反模式 #7 / coding-style | 函数<50 行、文件 200-400 行                    | ✅                                          | ⚠️ `SandboxRoom`~312 行等                                      |
| 反模式 #14               | 不强行共享 web/wxapp 代码                      | —                                           | ✅ 两套独立                                                    |
| 约束 #12                 | 副驾仅 Web/EXE                                 | —                                           | ✅ wxapp 无 copilot 页                                         |
| 约束 #13                 | 小程序主包 ≤1.5MB、大资源分包                  | —                                           | ⚠️ 无 `subPackages`，CI 闸门关闭，未验证                       |
| 约束 #14                 | 小程序域名白名单                               | —                                           | ❌ fallback 域名待 ICP 备案                                    |
| 反模式 #6                | K 不说「亲」「您」                             | —                                           | ✅ 全仓未命中                                                  |
| 约束 #3                  | 输入/输出全经内容审核                          | ⚠️ 见 H-1（自残 redirect 漏出）             | —                                                              |
| 约束 #4                  | LLM 主备切换                                   | ✅ DeepSeek→Qwen，800ms 首字节预算          | —                                                              |
| 约束 #5                  | LLM 链路接 Langfuse                            | ✅ 全链路 trace，未配 key 时降级 no-op      | —                                                              |
| 约束 #6                  | UTC 存储 + Asia/Shanghai 显示                  | ✅ `datetime.now(UTC)`、夜间静默用 Shanghai | —                                                              |
| 约束 #7                  | 不用 Redux/Electron/k8s                        | ✅                                          | ✅（用 Zustand 依赖/Tauri，未用 Redux/Electron）               |
| 编码规范                 | TS strict、ESLint/Prettier、Ruff               | ✅ ruff 全绿                                | ✅ `strict:true`、eslint/tsc 干净                              |
| 编码规范                 | React 19                                       | —                                           | web ✅；wxapp React 18（Taro 4.2 限制，可接受）                |
| security.md              | 无硬编码密钥、`.env` 不入库                    | ✅ 仅 `.env.example` 入库，无密钥           | ✅ 无硬编码 key                                                |

---

## 六、A 端亮点（供 B 端参考的良好实践）

- JWT secret fail-closed 校验（非 dev 拒绝 dev 默认值、最少 32 字节）。
- Ops 接口未配 `OPS_API_TOKEN` 时 503 fail-closed；`hmac.compare_digest` 常量时间比较；missing/wrong 合并单一错误码避免 oracle。
- `user_id`/`is_minor` 始终取自 JWT、不取请求体（Pydantic `extra="ignore"` 丢弃伪造）。
- 未成年 strictness：`warn`→`block`，但保留 `redirect`（不剥夺危机中未成年的求助资源）。
- 注释解释「为什么」而非「做什么」；Repository 模式 + memory/postgres 由 config flag 切换。

---

## 七、建议处理顺序

1. **H-3**：补 B 端测试——先 `useSandboxSession`/`useCopilotSession` 的 SSE 解析单测，搭 wxapp 测试基建，引入 Playwright E2E。
2. **H-1**：SSE 加 `moderation` 帧（A 端），两端前端消费展示热线（B 端）——需 A/B 协同。
3. **H-2**：A 端补 SMS dispatcher fail-closed 守卫。
4. **M-1 / M-2**：A 端列入上线前 backlog。
5. **M-4 / M-5 / M-6**：B 端决策 Zustand（落地或删依赖+改 .md）、拆分 `App.tsx`、路由器去留。
6. **L-8**：启用 CI 的 `build-wxapp` 体积闸门与红线闸门。

---

_审查人：Claude（AI 助手）· 工具验证基于 commit `06f3a81`。H-1 为跨端红线问题，建议优先级最高。_
