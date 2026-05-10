# apps/web

> **Web 浏览器版 + EXE 桌面版（Tauri 2 套同一份代码）**
> 副驾的主战场（后台麦克风权限 + 全局快捷键）

## 技术栈

- React 19 + TypeScript 5.5（strict 模式）
- Vite 6
- Tailwind CSS 4 + Radix UI
- Zustand + TanStack Query
- framer-motion + Rive（教练 K 动画）
- Tauri 2.0（EXE 打包）

## Sprint 0 待办

- [ ] D1: `pnpm create vite@latest` + Tailwind init
- [ ] D2: 接入 MSW + faker 跑 mock 数据
- [ ] D3: 设计 Token 翻译到 `tailwind.config.ts`
- [ ] D4: Tauri 2 init + 第一个 EXE 打包（Win 测）
- [ ] D4: Mascot K Rive 加载 + 1 表情切换
- [ ] D5: PWA / 三端打开同 URL

## 开发命令

```bash
pnpm dev               # Web 开发
pnpm build             # Web 生产构建
pnpm tauri dev         # EXE 开发
pnpm tauri build       # EXE 打包
pnpm test              # Vitest
pnpm lint              # ESLint
pnpm typecheck         # tsc --noEmit
```

## 关联文档

- [设计图纸](../../docs/careercoach-design-spec.md)
- [PRD 用户故事](../../docs/careercoach-prd-v2.md)
