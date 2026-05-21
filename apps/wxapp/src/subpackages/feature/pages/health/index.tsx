import { View, Text } from '@tarojs/components'
import { useState } from 'react'
import Taro from '@tarojs/taro'
import { getHealth } from '../../../../api/client'
import './index.scss'

/** 健康检查页 — D5-B: 验证小程序能调通后端 API */
export default function HealthCheck() {
  const [status, setStatus] = useState<'idle' | 'loading' | 'ok' | 'error'>('idle')
  const [detail, setDetail] = useState('')

  async function checkHealth() {
    setStatus('loading')
    try {
      const res = await getHealth()
      if (res.statusCode === 200 && res.data?.status === 'ok') {
        setStatus('ok')
        setDetail(JSON.stringify(res.data))
      } else {
        setStatus('error')
        setDetail(`HTTP ${res.statusCode}: ${JSON.stringify(res.data)}`)
      }
    } catch (err) {
      setStatus('error')
      setDetail(String(err))
    }
  }

  return (
    <View className="health-page">
      <Text className="health-title">API 健康检查</Text>
      <Text className="health-desc">验证小程序可调通后端 /health 端点</Text>

      <View className="health-status" onClick={checkHealth}>
        <Text className={
          status === 'ok' ? 'health-badge health-ok' :
          status === 'error' ? 'health-badge health-err' :
          status === 'loading' ? 'health-badge health-loading' :
          'health-badge health-idle'
        }>
          {status === 'ok' ? '✅ 连通' :
           status === 'error' ? '❌ 失败' :
           status === 'loading' ? '⏳ 检查中...' :
           '点击检查'}
        </Text>
      </View>

      {detail && <Text className="health-detail">{detail}</Text>}

      <Text className="health-tip">
        提示：本地开发需在微信开发者工具→详情→本地设置→勾选「不校验合法域名」
      </Text>
    </View>
  )
}
