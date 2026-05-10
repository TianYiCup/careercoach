/**
 * CareerCoach AI · Mascot K 类型定义
 * Source: design-spec §3
 */

/** 教练 K 的 8 种表情 */
export type MascotExpression =
  | 'confident'   // 自信
  | 'hype'        // 热血
  | 'thinking'    // 思考
  | 'god'         // 封神
  | 'fail'        // 翻车
  | 'fun'         // 整活
  | 'heart'       // 心疼
  | 'rotten'      // 摆烂

/** Mascot 资源类型（跨端适配） */
export type MascotAssetType =
  | 'rive'        // Web/EXE 用 Rive 动画
  | 'lottie'      // 小程序用 Lottie 兜底
  | 'png'         // 最低兜底：静态 PNG
