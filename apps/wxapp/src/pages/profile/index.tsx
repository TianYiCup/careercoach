/**
 * 我的页 — 小程序版
 * PRD §4 bottom tab: 首页/对练/副驾/复盘/我的
 *
 * 显示用户信息 + 弱点画像入口 + 退出登录
 */

import { useState, useEffect } from 'react'
import { View, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { authedRequest } from '../../api/client'
import { getAuthUser } from '../../utils/auth-user'
import { clearAuthToken } from '../../utils/auth-token'
import { clearAuthUser } from '../../utils/auth-user'
import './index.scss'

interface WeaknessItem {
  tag: string
  count: number
  percentage: number
  remark: string
}

interface WeaknessProfile {
  total_sessions: number
  top_weakness: WeaknessItem
  weaknesses: WeaknessItem[]
}

const PERSONA_LABELS: Record<string, string> = {
  in_school: '在校生',
  intern: '实习生',
  graduate: '应届生',
}

export default function ProfilePage() {
  const user = getAuthUser()
  const [profile, setProfile] = useState<WeaknessProfile | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    _loadProfile()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const _loadProfile = async () => {
    try {
      const res = await authedRequest<WeaknessProfile>('/users/me/weaknesses', 'GET')
      setProfile(res)
    } catch {
      // Profile may not exist yet — don't block the page
      setProfile(null)
    }
  }

  const handleLogout = () => {
    Taro.showModal({
      title: '退出登录',
      content: '确定要退出登录吗？',
      success: (res) => {
        if (res.confirm) {
          clearAuthToken()
          clearAuthUser()
          Taro.reLaunch({ url: '/pages/login/index' })
        }
      },
    })
  }

  const topWeakness = profile?.top_weakness

  return (
    <View className="profile">
      {/* Header */}
      <View className="profile-header">
        <Text className="profile-header-title">我的</Text>
      </View>

      {/* User info card */}
      {user && (
        <View className="profile-card">
          <View className="profile-avatar">
            <Text className="profile-avatar-text">
              {(user.nickname ?? 'K')[0]!}
            </Text>
          </View>
          <View className="profile-info">
            <Text className="profile-nickname">{user.nickname}</Text>
            <View className="profile-tags">
              <Text className="profile-tag">{PERSONA_LABELS[user.persona_type] ?? '用户'}</Text>
              {user.is_minor && (
                <Text className="profile-tag profile-tag--minor">青少年模式</Text>
              )}
            </View>
          </View>
        </View>
      )}

      {/* Top weakness hero card */}
      {topWeakness && (
        <View className="profile-section">
          <Text className="profile-section-title">K 说的最多的弱点</Text>
          <View className="profile-hero-card">
            <Text className="profile-hero-percentage">{topWeakness.percentage}%</Text>
            <Text className="profile-hero-tag">{topWeakness.tag}</Text>
            <Text className="profile-hero-remark">{topWeakness.remark}</Text>
          </View>
        </View>
      )}

      {/* Weakness list */}
      {profile && profile.weaknesses.length > 0 && (
        <View className="profile-section">
          <Text className="profile-section-title">弱点排行</Text>
          {profile.weaknesses.slice(0, 5).map((w, i) => (
            <View key={w.tag} className="profile-weakness-item">
              <Text className="profile-weakness-rank">#{i + 2}</Text>
              <View className="profile-weakness-bar-wrap">
                <Text className="profile-weakness-tag">{w.tag}</Text>
                <View className="profile-weakness-bar">
                  <View
                    className={`profile-weakness-fill ${w.percentage >= 35 ? 'profile-weakness-fill--high' : w.percentage >= 20 ? 'profile-weakness-fill--mid' : 'profile-weakness-fill--low'}`}
                    style={{ width: `${Math.min(w.percentage, 100)}%` }}
                  />
                </View>
              </View>
              <Text className="profile-weakness-count">{w.count}次</Text>
            </View>
          ))}
        </View>
      )}

      {/* Stats */}
      {profile && (
        <View className="profile-stats">
          <View className="profile-stat">
            <Text className="profile-stat-value">{profile.total_sessions}</Text>
            <Text className="profile-stat-label">总对练次数</Text>
          </View>
          <View className="profile-stat">
            <Text className="profile-stat-value">{profile.weaknesses.length}</Text>
            <Text className="profile-stat-label">弱点类型</Text>
          </View>
        </View>
      )}

      {/* Quick actions */}
      <View className="profile-actions">
        <View
          className="profile-action-item"
          onClick={() => Taro.navigateTo({ url: '/pages/review-upload/index' })}
        >
          <Text className="profile-action-text">复盘师</Text>
          <Text className="profile-action-arrow">→</Text>
        </View>
        <View
          className="profile-action-item"
          onClick={() => Taro.navigateTo({ url: '/pages/wrapped/index' })}
        >
          <Text className="profile-action-text">战报</Text>
          <Text className="profile-action-arrow">→</Text>
        </View>
      </View>

      {/* Logout */}
      <View className="profile-logout" onClick={handleLogout}>
        <Text className="profile-logout-text">退出登录</Text>
      </View>

      {error && <Text className="profile-error">{error}</Text>}
    </View>
  )
}
