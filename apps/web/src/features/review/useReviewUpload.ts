/**
 * useReviewUpload — fetch + poll lifecycle for one review upload.
 *
 * The backend persists the upload as `processing` and a worker flips it
 * to `done` / `failed`. The current backend runs the analysis inline so
 * the first GET is usually already terminal, but we poll regardless so
 * this stays correct if the queue is ever switched back to the async one
 * (see apps/api review SyncWorkerQueue note). Polling stops on a terminal
 * status or after a ceiling, mapping a stuck upload to a timeout error.
 */

import { useEffect, useState } from 'react'

import { apiClient, ApiError } from '../../api/v1/client'
import type { ReviewUploadResponse } from '../../api/v1'

const POLL_INTERVAL_MS = 1200
const MAX_POLL_ATTEMPTS = 25 // ~30s ceiling before we surface a timeout

export interface ReviewUploadState {
  data: ReviewUploadResponse | null
  error: string | null
}

export function useReviewUpload(uploadId: string): ReviewUploadState {
  const [data, setData] = useState<ReviewUploadResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const poll = async (attempt: number) => {
      try {
        const res = await apiClient.get<ReviewUploadResponse>(`/review/uploads/${uploadId}`)
        if (cancelled) return
        setData(res)
        // `done` / `failed` are terminal — stop. Keep polling only while
        // the worker is still analyzing.
        if (res.status === 'processing') {
          if (attempt >= MAX_POLL_ATTEMPTS) {
            setError('分析超时，请稍后重试')
            return
          }
          timer = setTimeout(() => void poll(attempt + 1), POLL_INTERVAL_MS)
        }
      } catch (e) {
        if (cancelled) return
        setError(e instanceof ApiError ? '加载失败，请稍后重试' : '网络异常')
      }
    }

    void poll(0)

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [uploadId])

  return { data, error }
}
