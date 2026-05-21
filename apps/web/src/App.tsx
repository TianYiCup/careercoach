import { useState } from 'react'
import { SandboxRoom } from './features/sandbox/SandboxRoom'
import { ScorePage } from './features/sandbox/ScorePage'
import { WrappedPage } from './features/wrapped/WrappedPage'
import { HomePage } from './features/home/HomePage'
import type { Score } from './api/v1/types'
import { AuthProvider, LoginPage, AgeGatePage, useAuth } from './features/auth'
import { ReviewUploadPage } from './features/review/ReviewUploadPage'
import { ReviewResultPage } from './features/review/ReviewResultPage'
import { CopilotPage } from './features/copilot'
import { WeaknessProfilePage } from './features/weakness'

type Page = 'home' | 'sandbox' | 'copilot' | 'wrapped' | 'score' | 'reviewUpload' | 'reviewResult' | 'weakness'

/**
 * AppGate — auth boundary + page navigation.
 *
 * Uses lightweight useState<Page> routing — sufficient for current scope.
 * Browser history integration can be added later if needed.
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
        <SandboxRoom onExit={() => setPage('home')} onScore={handleScore} />
      )}
      {page === 'copilot' && <CopilotPage onBack={() => setPage('home')} />}
      {page === 'weakness' && <WeaknessProfilePage onBack={() => setPage('home')} />}
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
