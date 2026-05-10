# CareerCoach AI · AI 全局上下文

> 这份文档由 AI 助手（Claude / Cursor / Copilot 等）自动加载。
> 任何对话开始前，AI 都会读它，确保知道项目在哪、规则是什么。

---

## 项目摘要

CareerCoach AI · 中文语境对话练习教练 · 面向 18-25 岁在校大学生 + 实习生 + 应届毕业生。

三大核心模式：
- **沙盘对练**：用户 vs AI 扮演的对手，教练 K 实时给提示
- **实战副驾**：真实对话中耳机提示（仅 Web/EXE，小程序不做）
- **复盘师**：上传聊天截图，AI 标记每句得失分

Mascot 教练 K：紫色 mochi 拳手，嘴硬心软，不爹味。

---

## 关键约束（不可违反）

1. 不教唆撕逼、不替代心理咨询、不给法律建议
2. < 18 用户启用青少年模式（禁副驾、不存语音）
3. 所有用户输入和 AI 输出必须经过内容审核
4. LLM 调用必须支持主备切换（DeepSeek → 通义）
5. 所有 LLM 链路必须接 Langfuse trace
6. 所有时间相关代码用 UTC 存储 + Asia/Shanghai 显示
7. 不用 Redux / Electron / k8s（v1 不需要）
8. 不写超过 800 行的 PR
9. 不在前端硬编码 LLM key、不在 git 提交 .env
10. 教练 K 的人格"嘴硬心软不爹味"，文案禁止说教
11. **三端策略**：Web/EXE 共享 React 代码（apps/web）+ 小程序独立（apps/wxapp，Taro）
12. **副驾仅 Web/EXE**，小程序版不做副驾（录音 API 限制）
13. **小程序主包 ≤ 1.5MB**（留 0.5MB buffer），Mascot/字体放分包
14. **小程序所有外部域名必须在白名单**（不允许动态拼接）

---

## 编码规范

- 前端：React 19 + TypeScript strict + Tailwind 4 + Zustand
- 后端：Python 3.12 + FastAPI + Pydantic v2 + async first
- 包管理：pnpm（前）/ uv（后）
- Lint：ESLint + Prettier（前）/ Ruff（后）
- 类型：100% 类型注解
- 命名：snake_case（py）/ camelCase（ts）/ PascalCase（组件 + 类）
- 提交信息：Conventional Commits（feat / fix / refactor / chore）

---

## 文件结构

```
careercoach/
  apps/
    web/              # React + Vite + Tailwind（Web + EXE 共享）
      src/
        components/   # UI primitives + 业务组件
        features/     # 按 Epic 分（sandbox / copilot / review）
        stores/       # Zustand
        api/          # 客户端 API
        mascot/       # 教练 K 资源 + Rive 动画
      src-tauri/      # Tauri Rust 配置（EXE 打包用）
    wxapp/            # 微信小程序（Taro 4 + React）
      src/
        pages/        # 沙盘 / 复盘 / Wrapped / 我的
        components/   # NutUI + 自定义
        api/          # wx.request 封装
        mascot/       # K 表情 PNG（Lottie 兜底）
        subpackages/  # 分包
    api/              # FastAPI 后端
      app/
        agents/       # LangGraph 节点（roleplay / coach / judge / reviewer）
        services/     # auth / vibe / share / moderation
        wxapp/        # 小程序专属（wx-login / 分享回流）
        models/       # Pydantic models
        db/           # SQLAlchemy + alembic
        llm/          # LLMProvider 抽象 + adapters
        asr/          # ASR 抽象
  packages/
    shared/           # 跨端共享类型 + 工具
    api-client/       # OpenAPI 生成的客户端
  docs/               # PRD / 设计图纸 / Foundation / Engineering
  tests/
    sensitive_samples.csv
    blunt_test_protocol.md
```

---

## 不要做什么（Anti-Patterns）

1. ❌ 直接用 openai client，必须走 LLMProvider 抽象
2. ❌ 在前端给 LLM 输出加 `dangerouslySetInnerHTML`
3. ❌ 用 emoji 作为唯一语义标识（色盲不友好），必须配文字
4. ❌ 写 `then().then().then()`，必须用 async/await
5. ❌ 把 Mascot 表情硬编码 emoji，必须用 Rive 资源（小程序可用 Lottie 兜底）
6. ❌ 在 K 的话术里说"亲""您"（脱离年轻人语境）
7. ❌ 写超过 50 行的函数 / 超过 800 行的文件
8. ❌ 跳过内容审核直接调 LLM
9. ❌ 在 Service 层 return HTTPException（必须 raise）
10. ❌ 不写测试（任何 P0 故事的 PR 必须带测试）
11. ❌ 在小程序里调"未在白名单"的域名（编译期会被拦）
12. ❌ 在小程序主包加 Rive / 大字体 / 视频（都放分包）
13. ❌ 小程序里用 React Router（用 Taro Router）
14. ❌ 把 Web 和小程序代码强行共享（独立两套，仅 packages/shared 共类型）

---

## 关键术语速查

- **沙盘 / 副驾 / 复盘** = 三大核心模式
- **教练 K** = Mascot
- **三档话术** = 稳如老狗 🐶 / 正面刚 🔥 / 整活儿 🤡
- **评分语义** = 封神 ✨ / 路过 🌀 / 翻车 💥
- **Wrapped 卡** = 9:16 可分享战报
- **红线** = §3.0.5 六大不可逾越（自残 / 校园暴力 / 网贷 / 性骚扰 / 涉政 / 沟通伤害）

---

## 关联文档

- 总体方案：`docs/careercoach-vision.md`
- PRD：`docs/careercoach-prd-v2.md`
- 设计图纸：`docs/careercoach-design-spec.md`
- 项目地基：`docs/careercoach-foundation.md`
- 工程规范：`docs/careercoach-engineering.md`

---

## 跟我说话的方式

- 简洁 > 啰嗦
- 中文为主，技术术语保留英文
- 出现冲突优先级：**红线 > NFR > 功能 > 美观**
- 不确定就问，不要瞎猜
- 涉及内容审核 / 未成年 / K 调性的代码，必须明确标注
