# packages/shared

> **跨端共享：类型定义 + 工具函数 + 常量**
> Web / EXE / 小程序 / API（生成的客户端）共用

## 内容

- TypeScript 类型（从 OpenAPI Schema 生成）
- 通用工具函数（时间格式 / 字符串处理 / 验证）
- 业务常量（场景类目 / 评分语义 / K 表情枚举）
- i18n 文案 key

## 使用

```ts
// 在 apps/web 或 apps/wxapp 中
import { ScenarioCategory, MascotExpression } from '@careercoach/shared';
```

## 不要做什么

- ❌ 别把 Web/小程序专属代码塞进来（端无关）
- ❌ 别引入 React 或任何前端框架（纯逻辑）
- ❌ 别写 Node-only 代码（小程序跑不了）
