import { useState } from 'react'
import type { Score } from './api/v1/types'
import { AuthProvider, LoginPage, AgeGatePage, useAuth } from './features/auth'
import { SandboxRoom } from './features/sandbox/SandboxRoom'
import { ScorePage } from './features/sandbox/ScorePage'
import { ReviewUploadPage } from './features/review/ReviewUploadPage'
import { ReviewResultPage } from './features/review/ReviewResultPage'
import { CopilotPage } from './features/copilot'
import { WeaknessProfilePage } from './features/weakness'
import { WrappedPage } from './features/wrapped/WrappedPage'
import { HomePage, type Page } from './features/home/HomePage'

/**
 * AppGate — auth boundary + page navigation.
 *
 * Routing is a tiny union ('home' | 'sandbox' | 'wrapped') instead of
 * react-router for now — the surface is small and the gate is one bit.
 * We'll bring in router when the route count grows.
 */
function AppGate() {
  const { isAuthenticated, needsAge } = useAuth()
  const [page, setPage] = useState<Page>('home')
  const [scoreData, setScoreData] = useState<{
    score: Score
    expression: 'godlike' | 'crashed' | 'confident'
    sessionId: string | null
  } | null>(null)
  const [reviewUploadId, setReviewUploadId] = useState<string | null>(null)

  if (!isAuthenticated) return <LoginPage />
  if (needsAge) return <AgeGatePage />

  const handleScore = (score: Score, expression: 'godlike' | 'crashed' | 'confident', sessionId: string | null) => {
    setScoreData({ score, expression, sessionId })
    setPage('score')
  }

  return (
    <>
      {page === 'home' && <HomePage onNavigate={setPage} />}
      {page === 'sandbox' && (
        <SandboxRoom
          onExit={() => setPage('home')}
          onScore={handleScore}
        />
      )}
      {page === 'copilot' && (
        <CopilotPage onBack={() => setPage('home')} />
      )}
      {page === 'weakness' && (
        <WeaknessProfilePage onBack={() => setPage('home')} />
      )}
      {page === 'wrapped' && <WrappedPage onBack={() => setPage('home')} />}
      {page === 'reviewUpload' && (
        <ReviewUploadPage
          onResult={(uploadId) => {
            setReviewUploadId(uploadId)
            setPage('reviewResult')
          }}
          onBack={() => setPage('home')}
        />
      )}
      {page === 'reviewResult' && reviewUploadId && (
        <ReviewResultPage
          uploadId={reviewUploadId}
          onBack={() => setPage('home')}
        />
      )}
      {page === 'score' && scoreData && (
        <ScorePage
          score={scoreData.score}
          mascotExpression={scoreData.expression}
          sessionId={scoreData.sessionId ?? undefined}
          onBack={() => {
            setScoreData(null)
            setPage('home')
          }}
        />
      )}
    </>
  )
}

function App() {
  return (
    <AuthProvider>
      <AppGate />
    </AuthProvider>
  )
}

export default App
