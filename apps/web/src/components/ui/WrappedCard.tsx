import { useRef, useCallback, useState } from 'react'

/** WrappedCard — Spotify Wrapped 风可分享卡
 *  design-spec §10.1: 9:16 竖图
 *    gradient bg → K 表情 → 大数字 (italic display) → 一句话 → 水印
 *  Web: Canvas 渲染 + download/copy
 *  wxapp: wx.canvasToTempFilePath + wx.saveImageToPhotosAlbum (TODO)
 */

export interface WrappedCardProps {
  /** 分数 (如 8.9) */
  score: number
  /** 一句话锐评 */
  comment: string
  /** 渐变类型 */
  gradient?: GradientType
  /** K 表情 emoji */
  expression?: string
  /** StickerBadge 标签列表 (如 ["气场+1", "逻辑+1"]) */
  badges?: string[]
  /** 是否显示水印 */
  showWatermark?: boolean
}

type GradientType = 'vivid' | 'glory' | 'crash'

const GRADIENT_MAP = {
  vivid: ['#6C4DFF', '#FF7AB6'],
  glory: ['#B0FF3C', '#3CFFE8'],
  crash: ['#FF6B35', '#FF7AB6'],
} as const satisfies Record<GradientType, readonly [string, string]>

/** Canvas 文字自动换行 — returns final Y position */
function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
): number {
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
  return currentY
}

export function WrappedCard({
  score,
  comment,
  gradient = 'vivid',
  expression = '✨',
  badges = [],
  showWatermark = true,
}: WrappedCardProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [imageDataUrl, setImageDataUrl] = useState<string | null>(null)

  /** Render the card onto the hidden canvas and return data URL */
  const generateImage = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return null

    const ctx = canvas.getContext('2d')
    if (!ctx) return null

    // 9:16 — 1080×1920 for hi-res output
    const W = 1080
    const H = 1920
    canvas.width = W
    canvas.height = H

    // Background gradient
    const [c1, c2] = GRADIENT_MAP[gradient]
    const bgGrad = ctx.createLinearGradient(0, 0, W, H)
    bgGrad.addColorStop(0, c1)
    bgGrad.addColorStop(1, c2)
    ctx.fillStyle = bgGrad
    ctx.fillRect(0, 0, W, H)

    // Subtle noise overlay — semi-transparent dots for texture
    ctx.fillStyle = 'rgba(0,0,0,0.04)'
    for (let i = 0; i < 200; i++) {
      const x = Math.random() * W
      const y = Math.random() * H
      const r = 1 + Math.random() * 2
      ctx.beginPath()
      ctx.arc(x, y, r, 0, Math.PI * 2)
      ctx.fill()
    }

    // K expression (top center)
    ctx.font = '128px sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(expression, W / 2, 200)

    // Score — large italic display
    ctx.font = 'bold italic 192px sans-serif'
    ctx.fillStyle = '#ffffff'
    ctx.textBaseline = 'alphabetic'
    ctx.shadowColor = 'rgba(0,0,0,0.2)'
    ctx.shadowBlur = 20
    ctx.shadowOffsetY = 4
    ctx.fillText(score.toFixed(1), W / 2, 500)
    ctx.shadowColor = 'transparent'
    ctx.shadowBlur = 0
    ctx.shadowOffsetY = 0

    // Subtitle
    ctx.font = '48px sans-serif'
    ctx.fillStyle = 'rgba(255,255,255,0.85)'
    const subtitle = score >= 8 ? '今日封神' : score >= 5 ? '继续加油' : '翻车了但还能救'
    ctx.fillText(subtitle, W / 2, 580)

    // Comment
    ctx.font = '36px sans-serif'
    ctx.fillStyle = 'rgba(255,255,255,0.75)'
    wrapText(ctx, comment, W / 2, 700, W - 180, 52)

    // Badges (StickerBadge equivalent) — pill shapes
    if (badges.length > 0) {
      ctx.font = '28px sans-serif'
      const badgeY = 880
      const badgeH = 60
      const badgePadX = 32
      const badgeGap = 24

      // Calculate total width for centering
      const badgeWidths = badges.map((b) => ctx.measureText(b).width + badgePadX * 2)
      const totalWidth = badgeWidths.reduce((sum, w) => sum + w + badgeGap, -badgeGap)
      let currentX = (W - totalWidth) / 2

      for (let i = 0; i < badges.length; i++) {
        const bw = badgeWidths[i]!
        const bx = currentX
        const by = badgeY

        // Badge pill background
        ctx.fillStyle = 'rgba(255,255,255,0.2)'
        ctx.beginPath()
        const pillR = badgeH / 2
        ctx.roundRect(bx, by - badgeH / 2, bw, badgeH, pillR)
        ctx.fill()

        // Badge border
        ctx.strokeStyle = 'rgba(255,255,255,0.4)'
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.roundRect(bx, by - badgeH / 2, bw, badgeH, pillR)
        ctx.stroke()

        // Badge text
        ctx.fillStyle = '#ffffff'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(badges[i]!, bx + bw / 2, by)

        currentX += bw + badgeGap
      }
      ctx.textAlign = 'center'
      ctx.textBaseline = 'alphabetic'
    }

    // Watermark
    if (showWatermark) {
      ctx.font = '24px sans-serif'
      ctx.fillStyle = 'rgba(255,255,255,0.35)'
      ctx.textAlign = 'center'
      ctx.fillText('CareerCoach AI · 教练 K 出品', W / 2, H - 60)
    }

    // QR placeholder — small square at bottom-right
    if (showWatermark) {
      ctx.strokeStyle = 'rgba(255,255,255,0.3)'
      ctx.lineWidth = 2
      ctx.strokeRect(W - 140, H - 140, 80, 80)
      ctx.font = '14px sans-serif'
      ctx.fillStyle = 'rgba(255,255,255,0.3)'
      ctx.fillText('扫码体验', W - 100, H - 48)
    }

    const dataUrl = canvas.toDataURL('image/png')
    setImageDataUrl(dataUrl)
    return dataUrl
  }, [score, comment, gradient, expression, badges, showWatermark])

  /** Download as PNG */
  const handleDownload = useCallback(() => {
    const dataUrl = generateImage()
    if (!dataUrl) return

    const link = document.createElement('a')
    link.download = `careercoach-${score.toFixed(1)}-${Date.now()}.png`
    link.href = dataUrl
    link.click()
  }, [generateImage, score])

  /** Copy image to clipboard */
  const handleCopy = useCallback(async () => {
    const canvas = canvasRef.current
    if (!canvas) return

    generateImage()

    try {
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob(resolve, 'image/png'),
      )
      if (!blob) return
      await navigator.clipboard.write([
        new ClipboardItem({ 'image/png': blob }),
      ])
    } catch {
      // Clipboard API may not be available (e.g. HTTP context)
      // Fallback: nothing — the user can use download instead
    }
  }, [generateImage])

  /** Preview the generated image */
  const handlePreview = useCallback(() => {
    generateImage()
  }, [generateImage])

  return (
    <div className="flex flex-col items-center gap-4">
      {/* Action buttons */}
      <div className="flex flex-wrap items-center justify-center gap-3">
        <button
          type="button"
          onClick={handlePreview}
          className="px-5 py-2 rounded-radius-pill bg-ink-card border border-ink-line text-ink-text font-body text-sm hover:bg-ink-card-2 transition-colors"
        >
          预览
        </button>
        <button
          type="button"
          onClick={handleDownload}
          className="px-5 py-2 rounded-radius-pill gradient-vivid text-white font-body font-medium text-sm glow-purple hover:scale-105 transition-transform"
        >
          保存图片
        </button>
        <button
          type="button"
          onClick={handleCopy}
          className="px-5 py-2 rounded-radius-pill bg-ink-card border border-ink-line text-ink-text font-body text-sm hover:bg-ink-card-2 transition-colors"
        >
          复制
        </button>
      </div>

      {/* Preview image */}
      {imageDataUrl && (
        <div className="w-full max-w-xs animate-fade-in">
          <img
            src={imageDataUrl}
            alt="Wrapped 战报卡"
            className="w-full rounded-radius-md shadow-elev-2"
          />
        </div>
      )}

      <canvas ref={canvasRef} className="hidden" />
    </div>
  )
}
