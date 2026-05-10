# apps/wxapp

> **微信小程序版 · Taro 4 + React + NutUI**
> 移动端主入口 + 社交传播主战场（Wrapped 卡核心载体）

## ⚠️ 关键约束

| 约束 | 应对 |
|------|------|
| 主包 ≤ 1.5MB | Mascot Lottie / 字体放分包 |
| 不支持 Rive | 用 Lottie 兜底 |
| 不支持 backdrop-filter | Glass 卡降级半透明 + 描边 |
| 不能跑 WebAssembly | ASR 全走云端 |
| **不做副驾** | 入口显示"请用桌面版" |
| 域名必须备案 + 白名单 | 提前 4 周启动 ICP |

## Sprint 0 待办

- [ ] 备案进度确认（W -4 启动了吗？）
- [ ] 微信小程序 AppID 申请
- [ ] `taro init` Taro 4 + React
- [ ] 第一屏 Hello + 教练 K 静态 PNG
- [ ] 服务器域名白名单配置（dev/prod）
- [ ] 调通第一个 API
- [ ] Wrapped 卡分享 spike

## 开发命令

```bash
pnpm dev:weapp         # 微信小程序开发
pnpm build:weapp       # 微信小程序生产构建
pnpm dev:h5            # H5 模式（调试用）
```

> 主包 size 检查：`du -sk dist/weapp/app.js`，必须 ≤ 1500KB

## 关联文档

- [设计图纸 §5.3 小程序约束](../../docs/careercoach-design-spec.md)
- [PRD §9.A.5 三端能力矩阵](../../docs/careercoach-prd-v2.md)
- [Foundation §3.2.2 三端策略](../../docs/careercoach-foundation.md)
