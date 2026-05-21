/**
 * Unit tests for wxapp API pure functions.
 *
 * Tests parseSseChunk — the SSE frame parser that runs on wx.request
 * chunked transfer. No component rendering or Taro runtime needed.
 *
 * @vitest-environment node
 */

import { describe, expect, it, vi } from 'vitest'

// Mock Taro before importing — sandbox.ts imports Taro at top level
vi.mock('@tarojs/taro', () => ({
  default: {
    reLaunch: vi.fn(),
    navigateBack: vi.fn(),
    showModal: vi.fn(),
    setClipboardData: vi.fn(),
  },
}))

// Mock API_BASE and auth helpers
vi.mock('../config', () => ({
  API_BASE: 'https://test-api.careercoach.ai',
}))

vi.mock('../../utils/auth-token', () => ({
  getAuthToken: vi.fn(() => 'test-token'),
  clearAuthToken: vi.fn(),
}))

vi.mock('../../utils/auth-user', () => ({
  clearAuthUser: vi.fn(),
}))

import { parseSseChunk } from '../sandbox'

describe('parseSseChunk', () => {
  it('parses a single opponent.delta frame', () => {
    const chunk = 'event: opponent.delta\ndata: {"text":"你"}\n\n'
    const frames = parseSseChunk(chunk)
    expect(frames).toHaveLength(1)
    expect(frames[0]).toEqual({ event: 'opponent.delta', data: { text: '你' } })
  })

  it('parses multiple frames in one chunk', () => {
    const chunk =
      'event: opponent.delta\ndata: {"text":"你"}\n\n' +
      'event: opponent.delta\ndata: {"text":"好"}\n\n' +
      'event: opponent.done\ndata: {"turn_id":"t_1","full_text":"你好"}\n\n'
    const frames = parseSseChunk(chunk)
    expect(frames).toHaveLength(3)
    expect(frames[0]).toEqual({ event: 'opponent.delta', data: { text: '你' } })
    expect(frames[1]).toEqual({ event: 'opponent.delta', data: { text: '好' } })
    expect(frames[2]).toEqual({ event: 'opponent.done', data: { turn_id: 't_1', full_text: '你好' } })
  })

  it('parses coach.hint frame', () => {
    const chunk = 'event: coach.hint\ndata: {"safe":"稳","aggressive":"刚","humor":"活"}\n\n'
    const frames = parseSseChunk(chunk)
    expect(frames).toHaveLength(1)
    expect(frames[0]).toEqual({
      event: 'coach.hint',
      data: { safe: '稳', aggressive: '刚', humor: '活' },
    })
  })

  it('parses meta frame', () => {
    const chunk = 'event: meta\ndata: {"turns_used":5,"turns_left":25}\n\n'
    const frames = parseSseChunk(chunk)
    expect(frames).toHaveLength(1)
    expect(frames[0]).toEqual({ event: 'meta', data: { turns_used: 5, turns_left: 25 } })
  })

  it('parses moderation redirect frame with redirect_resource', () => {
    const chunk = 'event: moderation\ndata: {"verdict":"redirect","categories":["self_harm"],"score":0.95,"redirect_resource":{"title":"心理援助","url":"https://help"}}\n\n'
    const frames = parseSseChunk(chunk)
    expect(frames).toHaveLength(1)
    expect(frames[0]).toMatchObject({
      event: 'moderation',
      data: {
        verdict: 'redirect',
        categories: ['self_harm'],
        score: 0.95,
        redirect_resource: { title: '心理援助', url: 'https://help' },
      },
    })
  })

  it('parses moderation block frame without redirect_resource', () => {
    const chunk = 'event: moderation\ndata: {"verdict":"block","categories":["violence"],"score":0.9}\n\n'
    const frames = parseSseChunk(chunk)
    expect(frames).toHaveLength(1)
    expect(frames[0]).toMatchObject({
      event: 'moderation',
      data: { verdict: 'block', categories: ['violence'], score: 0.9 },
    })
  })

  it('skips empty blocks', () => {
    expect(parseSseChunk('\n\n\n\n')).toHaveLength(0)
  })

  it('skips blocks with only event or only data', () => {
    expect(parseSseChunk('event: opponent.delta\n\n')).toHaveLength(0)
    expect(parseSseChunk('data: {"text":"hi"}\n\n')).toHaveLength(0)
  })

  it('skips unparseable JSON silently', () => {
    expect(parseSseChunk('event: opponent.delta\ndata: NOT JSON\n\n')).toHaveLength(0)
  })

  it('handles event name trimming', () => {
    const chunk = 'event: opponent.delta\ndata: {"text":"hi"}\n\n'
    const frames = parseSseChunk(chunk)
    expect(frames).toHaveLength(1)
    expect(frames[0]).toEqual({ event: 'opponent.delta', data: { text: 'hi' } })
  })

  it('ignores unknown event types', () => {
    const chunk = 'event: unknown_event\ndata: {"foo":"bar"}\n\n'
    expect(parseSseChunk(chunk)).toHaveLength(0)
  })
})
