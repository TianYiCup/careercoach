/**
 * Wrapped 战报页 — 小程序版
 * PRD §7.9 / design-spec §10
 *
 * 周报 / 年报切换，生成分享卡片
 * 小程序用 Canvas 2D 绘制分享图
 */

import { useState } from 'react'
import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { authedRequest } from '../../../../api/client'
import './index.scss'

type TabType = 'weekly' | 'wrapped'

interface ShareCardResponse {
  card_id: string
  type: 'session' | 'weekly' | 'wrapped'
  png_url: string
  share_links: {
    wechat: string
    save_local: string
  }
}

const CURRENT_YEAR = new Date().getFullYear()

export default function WrappedPage() {
  const [activeTab, setActiveTab] = useState<TabType>('weekly')
  const [pending, setPending] = useState(false)
  const [cardUrl, setCardUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleGenerate = async () => {
    setError(null)
    setPending(true)
    try {
      let res: ShareCardResponse
      if (activeTab === 'weekly') {
        res = await authedRequest<ShareCardResponse>('/sharecards/weekly', 'POST', {
          include_qrcode: false,
        })
      } else {
        res = await authedRequest<ShareCardResponse>(
          `/sharecards/wrapped/year/${CURRENT_YEAR}`,
          'POST',
          { include_qrcode: true },
        )
      }
      setCardUrl(res.png_url)
    } catch {
      setError('生成失败，请稍后再试')
    } finally {
      setPending(false)
    }
  }

  const handleSaveImage = () => {
    if (!cardUrl) return
    Taro.downloadFile({
      url: cardUrl,
      success: (downloadRes) => {
        if (downloadRes.statusCode === 200) {
          Taro.saveImageToPhotosAlbum({
            filePath: downloadRes.tempFilePath,
            success: () => {
              Taro.showToast({ title: '已保存到相册', icon: 'success' })
            },
            fail: () => {
              Taro.showToast({ title: '保存失败', icon: 'none' })
            },
          })
        }
      },
    })
  }

  const handleCopyLink = () => {
    if (!cardUrl) return
    Taro.setClipboardData({ data: cardUrl })
  }

  return (
    <View className="wrapped">
      <View className="wrapped-header">
        <Text className="wrapped-back" onClick={() => Taro.navigateBack()}>
          ← 返回
        </Text>
        <Text className="wrapped-title">战报</Text>
        <View className="wrapped-spacer" />
      </View>

      {/* Tab switcher */}
      <View className="wrapped-tabs">
        <View
          className={`wrapped-tab ${activeTab === 'weekly' ? 'wrapped-tab--active' : ''}`}
          onClick={() => { setActiveTab('weekly'); setCardUrl(null) }}
        >
          <Text>周报</Text>
        </View>
        <View
          className={`wrapped-tab ${activeTab === 'wrapped' ? 'wrapped-tab--active' : ''}`}
          onClick={() => { setActiveTab('wrapped'); setCardUrl(null) }}
        >
          <Text>{CURRENT_YEAR} 年报</Text>
        </View>
      </View>

      {/* Card display area */}
      <View className="wrapped-card-area">
        {cardUrl ? (
          <View className="wrapped-card-preview">
            <image className="wrapped-card-img" src={cardUrl} mode="aspectFit" />
            <View className="wrapped-card-actions">
              <View className="wrapped-card-action" onClick={handleSaveImage}>
                <Text className="wrapped-card-action-text">保存到相册</Text>
              </View>
              <View className="wrapped-card-action wrapped-card-action--secondary" onClick={handleCopyLink}>
                <Text className="wrapped-card-action-text">复制链接</Text>
              </View>
            </View>
          </View>
        ) : (
          <View className="wrapped-card-empty">
            <Text className="wrapped-card-empty-text">
              {activeTab === 'weekly' ? '生成本周战报' : `生成 ${CURRENT_YEAR} 年度战报`}
            </Text>
          </View>
        )}
      </View>

      {/* Generate button */}
      {!cardUrl && (
        <View
          className={`wrapped-generate-btn ${pending ? 'wrapped-generate-btn--disabled' : ''}`}
          onClick={handleGenerate}
        >
          <Text className="wrapped-generate-btn-text">
            {pending ? '生成中…' : '生成战报'}
          </Text>
        </View>
      )}

      {error && (
        <Text className="wrapped-error">{error}</Text>
      )}
    </View>
  )
}
