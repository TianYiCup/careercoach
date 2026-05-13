import { useRef, useCallback } from 'react'

/** WrappedCard — Spotify Wrapped 风可分享卡
 *  design-spec §10.1: 9:16 竖图
 *    gradient bg → K 表情 → 大数字 (italic display) → 一句话 → 水印
 *  Web: Canvas 渲染 + download
 *  wxapp: wx.canvasToTempFilePath + wx.saveImageToPhotosAlbum (TODO)
 */

interface WrappedCardProps {
  /** 分数 (如 8.9) */
  score: number
  /** 一句话锐评 */
  comment: string
  /** 渐变类型 */
  gradient?: GradientType
  /** K 表情 emoji */
  expression?: string
}

type GradientType = 'vivid' | 'glory' | 'crash'

const GRADIENT_MAP = {
  vivid: ['#6C4DFF', '#FF7AB6'],
  glory: ['#B0FF3C', '#3CFFE8'],
  crash: ['#FF6B35', '#FF7AB6'],
} as const satisfies Record<GradientType, readonly [string, string]>

export function WrappedCard({
  score,
  comment,
  gradient = 'vivid',
  expression = '✨',
}: WrappedCardProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const generateImage = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // 9:16 ratio — 360 × 640
    const W = 360
    const H = 640
    canvas.width = W
    canvas.height = H

    // Background gradient
    const [c1, c2] = GRADIENT_MAP[gradient]
    const bgGrad = ctx.createLinearGradient(0, 0, W, H)
    bgGrad.addColorStop(0, c1)
    bgGrad.addColorStop(1, c2)
    ctx.fillStyle = bgGrad
    ctx.fillRect(0, 0, W, H)

    // K expression (top center)
    ctx.font = '64px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText(expression, W / 2, 120)

    // Score — large italic display
    ctx.font = 'bold italic 96px sans-serif'
    ctx.fillStyle = '#ffffff'
    ctx.fillText(score.toFixed(1), W / 2, 280)

    // Subtitle
    ctx.font = '24px sans-serif'
    ctx.fillStyle = 'rgba(255,255,255,0.8)'
    ctx.fillText(score >= 8 ? '今日封神' : score >= 5 ? '继续加油' : '翻车了但还能救', W / 2, 320)

    // Comment
    ctx.font = '18px sans-serif'
    ctx.fillStyle = 'rgba(255,255,255,0.7)'
    wrapText(ctx, comment, W / 2, 380, W - 60, 26)

    // Watermark
    ctx.font = '12px sans-serif'
    ctx.fillStyle = 'rgba(255,255,255,0.4)'
    ctx.fillText('CareerCoach AI · 教练 K 出品', W / 2, H - 30)
  }, [score, comment, gradient, expression])

  const handleDownload = useCallback(() => {
    generateImage()
    const canvas = canvasRef.current
    if (!canvas) return

    const link = document.createElement('a')
    link.download = `careercoach-wrapped-${score}.png`
    link.href = canvas.toDataURL('image/png')
    link.click()
  }, [generateImage, score])

  return (
    <div className="flex flex-col items-center gap-4">
      <button
        type="button"
        onClick={handleDownload}
        className="px-6 py-2 rounded-radius-pill gradient-vivid text-white font-body font-medium glow-purple hover:scale-105 transition-transform"
      >
        生成 Wrapped 卡
      </button>
      <canvas ref={canvasRef} className="hidden" />
    </div>
  )
}

/** Canvas 文字自动换行 */
function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
) {
  let line = ''
  let currentY = y
  for (const char of text) {
    const testLine = line + char
    if (ctx.measureText(testLine).width > maxWidth && line) {
      ctx.fillText(line, x, currentY)
      line = char
      currentY += lineHeight
    } else {
      line = testLine
    }
  }
  ctx.fillText(line, x, currentY)
}
